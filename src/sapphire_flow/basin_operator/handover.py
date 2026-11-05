"""Write a new approved subset; never alter a producer package."""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import geopandas as gpd
import polars as pl

from .inventory import inventory_json
from .review import ReviewStatus, decide, decision_json, file_hash, open_batch

if TYPE_CHECKING:
    from .review import ReviewBatch, ReviewDecision


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )


def export_handover(
    batch: ReviewBatch,
    decisions: tuple[ReviewDecision, ...],
    destination: Path,
    *,
    package_id: str,
    now: str,
    map_html: str,
    source_archive_sha256: str | None = None,
) -> Path:
    current = open_batch(batch.package_dir, batch.stations)
    if current.fingerprint != batch.fingerprint:
        raise ValueError("package changed; fresh review is required")
    if destination.exists():
        raise ValueError("handover destination already exists; exports are immutable")
    if not package_id.strip() or package_id == batch.package.manifest.package_id:
        raise ValueError("derived package needs a new package_id")
    if len({d.station_code for d in decisions}) != len(decisions):
        raise ValueError("duplicate review decisions")
    approved = {
        d.station_code
        for d in decisions
        if d.status == ReviewStatus.APPROVED and d.fingerprint == batch.fingerprint
    }
    if not approved:
        raise ValueError("no currently approved stations to export")
    if not approved <= {s.code for s in batch.stations}:
        raise ValueError("approval references an unknown station")
    for decision in decisions:
        if decision.station_code in approved:
            decide(
                batch,
                codes=(decision.station_code,),
                status=decision.status,
                reviewer=decision.reviewer,
                reason=decision.reason,
                acknowledgment=decision.acknowledgment,
                now=decision.reviewed_at,
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".handover-", dir=destination.parent))
    try:
        package = staging / "package"
        package.mkdir()
        _write_subset(batch, approved, package, package_id, now)
        selected = tuple(s for s in batch.stations if s.code in approved)
        derived = open_batch(package, selected)
        if any(derived.blockers.values()):
            raise ValueError("derived package failed acceptance")
        originals = {b.station_code: b for b in batch.package.basins}
        for basin in derived.package.basins:
            original = originals[basin.station_code]
            if (
                basin.geometry is None
                or original.geometry is None
                or not basin.geometry.equals_exact(original.geometry, 0)
            ):
                raise ValueError("derived geometry differs from reviewed geometry")
            if (
                derived.package.static_attributes[basin.gauge_id]
                != (batch.package.static_attributes[original.gauge_id])
            ):
                raise ValueError("derived attributes differ from reviewed attributes")
        shutil.copyfile(
            package / "static_attributes.parquet", staging / "static_attributes.parquet"
        )
        pl.read_parquet(package / "static_attributes.parquet").write_csv(
            staging / "static_attributes.csv"
        )
        shutil.copyfile(
            package / "feature_catalog.json", staging / "feature_catalog.json"
        )
        write_json(
            staging / "coordinates-and-snap.json",
            [
                {
                    **row,
                    "selected_outlet": {
                        "latitude": basin.outlet_lat,
                        "longitude": basin.outlet_lon,
                    },
                    "snap_distance_m": basin.outlet_snap_distance_m,
                    "extracted_area_km2": basin.area_km2,
                }
                for row in inventory_json(selected)
                for basin in batch.package.basins
                if basin.station_code == row["code"]
            ],
        )
        write_json(
            staging / "approval.json",
            {
                "identity": "self-declared, not authenticated",
                "source_fingerprint": batch.fingerprint,
                "source_archive_sha256": source_archive_sha256,
                "derived_fingerprint": derived.fingerprint,
                "decisions": decision_json(
                    tuple(d for d in decisions if d.station_code in approved)
                ),
            },
        )
        by_code = {d.station_code: d for d in decisions}
        with (staging / "excluded-stations.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["station_code", "status", "reason"])
            for station in batch.stations:
                if station.code in approved:
                    continue
                decision = by_code.get(station.code)
                writer.writerow(
                    [
                        station.code,
                        decision.status if decision else "pending",
                        decision.reason if decision else "Awaiting review",
                    ]
                )
        (staging / "review.html").write_text(map_html)
        (staging / "README.md").write_text(
            "# Approved basin handover\n\n"
            "Only package/ and the static tables are approved training inputs.\n"
            "review.html is read-only feedback evidence and may show excluded basins.\n"
            "Reviewer identity is self-declared. No remote approvals are accepted.\n"
            "Feature definitions and units are in feature_catalog.json.\n"
            "Original coordinates remain in coordinates-and-snap.json; "
            "outlet coordinates "
            "in the package retain the producer contract.\n"
            "Internet is required for map backgrounds. This is not model readiness "
            "or permission to register stations or deploy forecasts.\n"
        )
        paths = sorted(path for path in staging.rglob("*") if path.is_file())
        (staging / "checksums.sha256").write_text(
            "".join(
                f"{file_hash(path).removeprefix('sha256:')}  "
                f"{path.relative_to(staging)}\n"
                for path in paths
            )
        )
        if (
            open_batch(batch.package_dir, batch.stations).fingerprint
            != batch.fingerprint
        ):
            raise ValueError("package changed during export; fresh review is required")
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging)
        raise
    return destination


def _write_subset(
    batch: ReviewBatch,
    approved: set[str],
    target: Path,
    package_id: str,
    now: str,
) -> None:
    source = batch.package_dir
    # GeoPandas' dynamic column interface has incomplete third-party stubs.
    basins: Any = gpd.read_file(source / "basins.gpkg")  # pyright: ignore[reportUnknownMemberType]
    selected = basins.loc[basins["station_code"].isin(approved)].copy()
    selected.to_file(target / "basins.gpkg", layer="polygons", driver="GPKG")
    gauges = selected["gauge_id"].tolist()
    pl.read_parquet(source / "static_attributes.parquet").filter(
        pl.col("gauge_id").is_in(gauges)
    ).write_parquet(target / "static_attributes.parquet")
    shutil.copyfile(source / "feature_catalog.json", target / "feature_catalog.json")
    validation = json.loads((source / "validation_report.json").read_text())
    entries = [row for row in validation["basins"] if row["station_code"] in approved]
    write_json(
        target / "validation_report.json",
        {
            "summary": {
                key: sum(row["status"] == status for row in entries)
                for key, status in (
                    ("passed", "passed"),
                    ("failed", "failed"),
                    ("warnings", "warning"),
                )
            },
            "basins": entries,
        },
    )
    hrus: set[str] = set(selected["gateway_hru_name"])
    if (source / "bands.gpkg").exists():
        bands: Any = gpd.read_file(source / "bands.gpkg")  # pyright: ignore[reportUnknownMemberType]
        bands = bands.loc[bands["station_code"].isin(approved)].copy()
        if not bands.empty:
            bands.to_file(target / "bands.gpkg", layer="polygons", driver="GPKG")
            hrus.update(bands["gateway_hru_name"])
    comparison = source / "official_geometry_comparison.parquet"
    if comparison.exists():
        frame = pl.read_parquet(comparison)
        if "station_code" not in frame.columns:
            raise ValueError(
                "cannot subset official comparison without station_code; ask producer"
            )
        frame.filter(pl.col("station_code").is_in(sorted(approved))).write_parquet(
            target / comparison.name
        )
    (target / "README.md").write_text(
        "# Approved subset\n\nGeometry and attributes are selected unchanged from "
        "the source identified in derivation.json. This is a new package identity.\n"
    )
    write_json(
        target / "derivation.json",
        {
            "source_package_id": batch.package.manifest.package_id,
            "source_fingerprint": batch.fingerprint,
            "source_checksums": batch.package.computed_checksums,
            "operation": "approved subset; no geometry or attribute calculation",
            "approved_station_codes": sorted(approved),
        },
    )
    original = json.loads((source / "manifest.json").read_text())
    keys = (
        "contract_version",
        "network",
        "crs",
        "extractor",
        "source_datasets",
        "climatology_window",
    )
    manifest = {key: original[key] for key in keys if key in original}
    manifest.update(
        package_id=package_id, created_at=now, gateway_hru_names=sorted(hrus)
    )
    manifest["files"] = {
        "basins": "basins.gpkg",
        "static_attributes": "static_attributes.parquet",
        "feature_catalog": "feature_catalog.json",
        "validation_report": "validation_report.json",
    }
    for filename in (
        "bands.gpkg",
        "official_geometry_comparison.parquet",
        "derivation.json",
        "README.md",
    ):
        if (target / filename).exists():
            manifest["files"][filename.split(".")[0]] = filename
    manifest["checksums"] = {
        path.name: file_hash(path) for path in target.iterdir() if path.is_file()
    }
    write_json(target / "manifest.json", manifest)
