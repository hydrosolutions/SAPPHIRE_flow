"""Plan 191 task T4 — narrow ERA5-Land t2m point extraction.

Delivers exactly one thing: a per-station hourly ERA5-Land 2m-temperature
series in degC, at the nearest grid cell, for the 26 gauge stations. NOT the
M-A5 bundle shape — no bilinear comparand, no `operator_sensitivity.csv`, no
orography run (D5). Elevations are never re-derived: this module reads the
already-published precipitation extraction bundle's
`station_grid_elevation.csv` and records ITS `extraction_identity` as a
reference (D6) — after verifying, per station, that the t2m extraction
actually lands on the same nearest grid cell that table already claims
(never assumed).

The manifest records the referenced precipitation bundle's IDENTITY only,
never its run-numbered PATH: `publish_bundle`/`allocate_published_dir`
allocate a fresh `<NNNN>-<identity>` directory on every gate-suite
invocation (P1a — no adoption, no dedup), so the same identity can live
under many different `NNNN` prefixes over time, and a path recorded today
is not stable.

An identity is a LABEL, never a lookup key (`era5_extract_manifest` P3), and
the same identity may legitimately cover DIFFERENT payloads, so a consumer
must NOT resolve a bundle by globbing `*-<identity>`. Discovery is P2/P6's
convention — the highest `NNNN` whose manifest validates — which is what
`_discover_precip_bundle` below actually does.

Usage:
    uv run python scripts/dhm_precip/extract_era5_t2m.py \
        --data-root data/dhm_precip/era5_land_t2m \
        --precip-data-root data/dhm_precip

Environment:
    DHM_PRECIP_XLSX  required for the workbook-derived usable-station
                     inventory (2d), same boundary input the precipitation
                     extraction CLI uses.
    DHM_PRECIP_COORDS  optional override for the station coordinate table
                       (default `data/dhm_precip/station_coordinates.csv`).

Exit codes:
    0  success
    2  inputs absent (no t2m product for a study year, no published
       precipitation bundle to reference for D6)
    4  an extraction post-condition failed (bounds, non-finite value,
       checksum, D6 grid disagreement)
    5  storage/manifest read or write failed (including a missing t2m
       acquisition manifest)
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
import structlog  # noqa: E402
import xarray as xr  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from sapphire_flow.logging import configure_cli_logging  # noqa: E402
from scripts.dhm_precip.domain_types import ExtractionOperator, Station  # noqa: E402
from scripts.dhm_precip.era5_errors import (  # noqa: E402
    Era5AcquisitionError,
    Era5SchemaValidationError,
    Era5StorageError,
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
)
from scripts.dhm_precip.era5_extract import (  # noqa: E402
    ExtractedSeries,
    assert_expected_station_cardinality,
    assert_no_missing_primary,
    assert_registration,
    assert_source_checksum,
    assert_utc_epoch_encoding,
    extract_nearest_series,
    load_expected_station_coordinates,
)
from scripts.dhm_precip.era5_extract_manifest import (  # noqa: E402
    manifest_filename,
    points_root,
    read_extraction_manifest,
)
from scripts.dhm_precip.era5_instantaneous import (  # noqa: E402
    validate_instantaneous_schema,
)
from scripts.dhm_precip.era5_manifest import (  # noqa: E402
    DEFAULT_DATA_ROOT,
    checksum_file,
    manifest_path_for,
    product_artifact_path,
    publish_atomic,
    read_manifest,
    tmp_path_for,
)
from scripts.dhm_precip.era5_request import (  # noqa: E402
    DEFAULT_REQUEST_SPEC,
    STUDY_YEARS,
    expected_total_hours,
)
from scripts.dhm_precip.loader import (  # noqa: E402
    PRODUCTION_SOURCE_SHA256,
    DhmPrecipLoaderError,
    load_long_frame,
    resolve_coords_path,
    resolve_source_path,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from scripts.dhm_precip.params import DhmPrecipParams

log = structlog.get_logger(__name__)

DEFAULT_T2M_DATA_ROOT = Path("data/dhm_precip/era5_land_t2m")
T2M_OUTPUT_SCHEMA_VERSION = "1"
T2M_OUTPUT_DTYPE = "float32"
T2M_DATA_VARIABLE = "temperature_degc"

_GRID_TOLERANCE_DEG = 1e-6

_EXIT_BY_ERROR: tuple[tuple[type[Exception], int], ...] = (
    (DhmPrecipLoaderError, 2),
    (ExtractionInputAbsentError, 2),
    (ExtractionPostConditionError, 4),
    (Era5StorageError, 5),
    # ORDERED SUBCLASS-FIRST (mirrors extract_era5.py's own table): every
    # other Era5*Error (e.g. NonFiniteExtractionError, StationOutsideGridError,
    # StationSetMismatchError, SourceChecksumMismatchError from the reused
    # era5_extract.py assertions) not covered above is an extraction
    # post-condition failure.
    (Era5AcquisitionError, 4),
    # a raw OSError never reaches the typed hierarchy: it is storage.
    (OSError, 5),
)


def _exit_code_for(exc: Exception) -> int:
    for exc_type, code in _EXIT_BY_ERROR:
        if isinstance(exc, exc_type):
            return code
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="extract_era5_t2m", description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_T2M_DATA_ROOT)
    parser.add_argument("--precip-data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path for a human-readable JSON run summary.",
    )
    return parser


def t2m_points_dir(data_root: Path) -> Path:
    return data_root / "era5_land" / "points"


def _default_expected_stations() -> frozenset[Station]:
    """The same workbook-derived usable-station inventory (2d's boundary
    decision) the precipitation extraction CLI reads (`extract_era5.
    _default_expected_stations`) — replicated here (not imported) because
    that helper is module-private; both read the column INVENTORY only,
    never a gauge value (D4)."""
    source_path = resolve_source_path()
    _long_frame, inventory = load_long_frame(
        source_path, expected_sha256=PRODUCTION_SOURCE_SHA256
    )
    return frozenset(
        Station(name)
        for name in inventory.all_columns
        if name not in inventory.empty_columns
    )


def _discover_precip_bundle(precip_data_root: Path) -> tuple[Path, str]:
    """P6's own discovery convention, reused read-only: the highest `NNNN`
    whose manifest is present and readable. D6 — this never re-runs
    orography or publishes anything into the precipitation bundle; it only
    reads the already-published one."""
    root = points_root(precip_data_root)
    if not root.exists():
        raise ExtractionInputAbsentError(
            f"no precipitation extraction points root at {root} — D6 needs "
            "a published precipitation bundle's elevation table to "
            "reference"
        )
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name != ".staging"),
        key=lambda p: p.name,
    )
    for candidate in reversed(candidates):
        manifest = read_extraction_manifest(candidate / manifest_filename())
        if manifest is not None:
            return candidate, manifest.extraction_identity
    raise ExtractionInputAbsentError(
        f"no published precipitation extraction bundle with a readable "
        f"manifest found under {root}"
    )


def assert_grid_matches_precipitation_bundle(
    nearest_by_station: Mapping[Station, ExtractedSeries], elevation_csv_path: Path
) -> None:
    """D6 — verify, never assume, that this t2m extraction lands on the SAME
    nearest grid cell the published precipitation bundle's
    `station_grid_elevation.csv` already recorded for each station. If any
    station disagrees, D6's shared-0.1-deg-grid reuse is falsified and must
    be reported, not engineered past."""
    if not elevation_csv_path.exists():
        raise ExtractionInputAbsentError(
            "referenced precipitation bundle's elevation table is missing "
            f"at {elevation_csv_path}"
        )
    elevation = pl.read_csv(elevation_csv_path)
    by_station = {str(row["station"]): row for row in elevation.iter_rows(named=True)}
    mismatches: list[str] = []
    for station, series in nearest_by_station.items():
        row = by_station.get(str(station))
        if row is None:
            mismatches.append(
                f"{station!r}: absent from the precipitation elevation table"
            )
            continue
        if int(row["grid_i"]) != series.grid_i or int(row["grid_j"]) != series.grid_j:
            mismatches.append(
                f"{station!r}: grid_i/grid_j ({series.grid_i}, {series.grid_j}) "
                f"!= precipitation bundle's ({row['grid_i']}, {row['grid_j']})"
            )
            continue
        if not math.isclose(
            float(row["grid_lat"]), series.grid_lat, abs_tol=_GRID_TOLERANCE_DEG
        ) or not math.isclose(
            float(row["grid_lon"]), series.grid_lon, abs_tol=_GRID_TOLERANCE_DEG
        ):
            mismatches.append(
                f"{station!r}: grid_lat/grid_lon "
                f"({series.grid_lat}, {series.grid_lon}) != precipitation "
                f"bundle's ({row['grid_lat']}, {row['grid_lon']})"
            )
    if mismatches:
        raise ExtractionPostConditionError(
            "D6's shared-grid reuse is FALSIFIED for "
            f"{len(mismatches)} station(s) — the t2m extraction lands on a "
            "different nearest cell than the published precipitation "
            f"bundle's station_grid_elevation.csv: {'; '.join(mismatches)}"
        )


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def t2m_extraction_identity(
    *,
    operator_id: str,
    coordinate_table_sha256: str,
    source_sha256s_by_year: Mapping[str, str],
    referenced_precipitation_bundle_identity: str,
    output_schema_version: str,
    output_dtype: str,
) -> str:
    """D3/D5 — this extraction's own identity, over ONLY what it reads: the
    operator, the coordinate table, the six t2m source product hashes, the
    precipitation bundle it references for elevations (D6), and the output
    schema/dtype. No `wet_threshold_mm_per_h`, `zero_policy`, quantile grid
    or delta statistics — those are precipitation semantics this module
    never reads, and hashing them would violate D3 in the other direction."""
    canonical = _canonical_json(
        {
            "operator_id": operator_id,
            "coordinate_table_sha256": coordinate_table_sha256,
            "source_sha256s_by_year": dict(source_sha256s_by_year),
            "referenced_precipitation_bundle_identity": (
                referenced_precipitation_bundle_identity
            ),
            "output_schema_version": output_schema_version,
            "output_dtype": output_dtype,
        }
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class T2mExtractionManifest(BaseModel):
    """D8/D9 — the narrow manifest: operator, source hashes, coordinate
    table hash, and the referenced precipitation bundle's identity — the
    D6 provenance link, never a re-derivation of what it references.

    Deliberately carries NO run-numbered PATH to the referenced bundle
    (only its IDENTITY, a stable content label per P3): `allocate_published_
    dir` hands out a fresh `<NNNN>-<identity>` directory on every gate-suite
    run (P1a — no adoption, no dedup), so a path recorded today can point at
    a directory pruned tomorrow while an identically-identified sibling
    survives. A consumer resolves the current directory by globbing
    `<precip_data_root>/era5_land/points/*-<referenced_precipitation_bundle_
    identity>`, mirroring `_discover_precip_bundle`."""

    extraction_identity: str
    operator_id: str
    coordinate_table_sha256: str
    source_sha256s_by_year: dict[str, str]
    referenced_precipitation_bundle_identity: str
    station_count: int
    stamp_count: int
    output_schema_version: str
    output_dtype: str
    generated_at: datetime


def _write_t2m_manifest(manifest: T2mExtractionManifest, path: Path) -> None:
    tmp_path = tmp_path_for(path)
    tmp_path.write_text(manifest.model_dump_json(indent=2))
    publish_atomic(tmp_path, path)


def _write_t2m_series_netcdf(
    path: Path, by_station: dict[Station, ExtractedSeries]
) -> None:
    """D9's own (narrow) on-disk contract: `temperature_degc(station,
    valid_time)` float32, `units` attr `degC`, `valid_time.attrs["timezone"]
    == "UTC"`."""
    stations = sorted(by_station)
    valid_time = by_station[stations[0]].valid_time
    values = np.stack([by_station[s].values for s in stations], axis=0).astype(
        np.float32
    )
    ds = xr.Dataset(
        {T2M_DATA_VARIABLE: (["station", "valid_time"], values)},
        coords={"station": [str(s) for s in stations], "valid_time": valid_time},
    )
    ds[T2M_DATA_VARIABLE].attrs["units"] = "degC"
    ds["valid_time"].attrs["timezone"] = "UTC"
    tmp_path = tmp_path_for(path)
    tmp_path.unlink(missing_ok=True)
    ds.to_netcdf(
        tmp_path,
        engine="h5netcdf",
        encoding={
            T2M_DATA_VARIABLE: {"dtype": "float32", "zlib": True, "complevel": 4},
            "valid_time": {
                "units": "hours since 1970-01-01 00:00:00",
                "dtype": "int64",
            },
        },
    )
    publish_atomic(tmp_path, path)


def _concat_series(parts: list[ExtractedSeries]) -> ExtractedSeries:
    first = parts[0]
    valid_time = np.concatenate([p.valid_time for p in parts])
    values = np.concatenate([p.values for p in parts])
    n_nan = int(np.isnan(values).sum())
    n_inf = int(np.isinf(values).sum())
    return replace(
        first,
        valid_time=valid_time,
        values=values,
        n_finite=values.size - n_nan - n_inf,
        n_nan=n_nan,
        n_inf=n_inf,
    )


def run(
    args: argparse.Namespace,
    *,
    clock: Callable[[], datetime] | None = None,
    coords_path: Path | None = None,
    expected_stations: frozenset[Station] | None = None,
    params: DhmPrecipParams | None = None,
    request_area: tuple[int, int, int, int] | None = None,
) -> int:
    data_root: Path = args.data_root
    precip_data_root: Path = args.precip_data_root
    resolved_params = params if params is not None else DEFAULT_PARAMS
    resolved_clock: Callable[[], datetime] = (
        clock if clock is not None else (lambda: datetime.now(UTC))
    )
    area = request_area if request_area is not None else DEFAULT_REQUEST_SPEC.area

    resolved_coords_path = (
        coords_path if coords_path is not None else resolve_coords_path()
    )
    resolved_expected_stations = (
        expected_stations
        if expected_stations is not None
        else _default_expected_stations()
    )
    assert_expected_station_cardinality(
        resolved_expected_stations,
        expected_count=resolved_params.expected_station_count,
    )
    stations = load_expected_station_coordinates(
        resolved_coords_path, expected_stations=resolved_expected_stations
    )
    coordinate_table_sha256 = checksum_file(resolved_coords_path)

    manifest_path = manifest_path_for(data_root)
    era5_manifest = read_manifest(manifest_path)
    if era5_manifest is None:
        raise Era5StorageError(
            f"no ERA5-Land acquisition manifest found at {manifest_path}"
        )

    precip_bundle_dir, precip_bundle_identity = _discover_precip_bundle(
        precip_data_root
    )

    nearest_parts: dict[Station, list[ExtractedSeries]] = {}
    source_sha256s_by_year: dict[str, str] = {}
    first_grid_by_station: dict[Station, tuple[int, int]] = {}

    for year in STUDY_YEARS:
        product_path = product_artifact_path(
            year,
            data_root,
            variable_code="t2m",
            product_dir_name="degc",
            unit_label="degc",
        )
        record = era5_manifest.transformed_years.get(str(year))
        if record is None:
            raise ExtractionInputAbsentError(
                f"no transformed-year record for {year} in the t2m acquisition manifest"
            )
        if not product_path.exists():
            raise ExtractionInputAbsentError(
                f"acquired ERA5-Land t2m product for {year} is missing at "
                f"{product_path}"
            )
        assert_source_checksum(product_path, expected_sha256=record.sha256)
        source_sha256s_by_year[str(year)] = record.sha256
        with xr.open_dataset(product_path, engine="h5netcdf") as reopened:
            ds = reopened.load()
        try:
            validate_instantaneous_schema(ds, expected_year=year, expected_area=area)
        except Era5SchemaValidationError as exc:
            raise ExtractionPostConditionError(str(exc)) from exc
        assert_registration(ds["latitude"].values, ds["longitude"].values)
        assert_utc_epoch_encoding(ds)
        for station, coord in stations.by_station.items():
            nearest = extract_nearest_series(ds, coord, variable="temperature")
            assert_no_missing_primary(nearest)
            grid_key = (nearest.grid_i, nearest.grid_j)
            if station not in first_grid_by_station:
                first_grid_by_station[station] = grid_key
            elif first_grid_by_station[station] != grid_key:
                raise ExtractionPostConditionError(
                    f"station {station!r} resolved to a different nearest "
                    f"grid cell across t2m product years: "
                    f"{first_grid_by_station[station]} (year {STUDY_YEARS[0]}) "
                    f"vs {grid_key} (year {year})"
                )
            nearest_parts.setdefault(station, []).append(nearest)

    merged = {s: _concat_series(v) for s, v in nearest_parts.items()}

    # D6 — verified, not assumed: same nearest cell as the already-published
    # precipitation bundle's elevation table.
    assert_grid_matches_precipitation_bundle(
        merged, precip_bundle_dir / "station_grid_elevation.csv"
    )

    expected_stamp_count = expected_total_hours(STUDY_YEARS)
    stamp_counts = {series.values.size for series in merged.values()}
    if stamp_counts != {expected_stamp_count}:
        raise ExtractionPostConditionError(
            f"merged t2m series stamp count(s) {sorted(stamp_counts)} != "
            f"expected {expected_stamp_count} (D9 coverage, 6 study years)"
        )

    identity = t2m_extraction_identity(
        operator_id=str(ExtractionOperator.NEAREST),
        coordinate_table_sha256=coordinate_table_sha256,
        source_sha256s_by_year=source_sha256s_by_year,
        referenced_precipitation_bundle_identity=precip_bundle_identity,
        output_schema_version=T2M_OUTPUT_SCHEMA_VERSION,
        output_dtype=T2M_OUTPUT_DTYPE,
    )

    points_dir = t2m_points_dir(data_root)
    points_dir.mkdir(parents=True, exist_ok=True)
    series_path = points_dir / "series_t2m_degc.nc"
    manifest_out_path = points_dir / "extraction_manifest.json"

    _write_t2m_series_netcdf(series_path, merged)

    manifest = T2mExtractionManifest(
        extraction_identity=identity,
        operator_id=str(ExtractionOperator.NEAREST),
        coordinate_table_sha256=coordinate_table_sha256,
        source_sha256s_by_year=source_sha256s_by_year,
        referenced_precipitation_bundle_identity=precip_bundle_identity,
        station_count=len(merged),
        stamp_count=expected_stamp_count,
        output_schema_version=T2M_OUTPUT_SCHEMA_VERSION,
        output_dtype=T2M_OUTPUT_DTYPE,
        generated_at=resolved_clock(),
    )
    _write_t2m_manifest(manifest, manifest_out_path)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "extraction_identity": identity,
                    "series_path": str(series_path),
                    "manifest_path": str(manifest_out_path),
                },
                indent=2,
            )
        )

    log.info("era5_extract_t2m.cli.published", extraction_identity=identity)
    return 0


def main(argv: list[str] | None = None, **kwargs: object) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_cli_logging()
    try:
        return run(args, **kwargs)  # type: ignore[arg-type]
    except (DhmPrecipLoaderError, Era5AcquisitionError, OSError) as exc:
        log.error(
            "era5_extract_t2m.cli.failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return _exit_code_for(exc)


if __name__ == "__main__":
    raise SystemExit(main())
