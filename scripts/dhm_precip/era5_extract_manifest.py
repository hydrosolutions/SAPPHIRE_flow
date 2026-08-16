"""Plan 174 (M-A5) task 4a — D7's two identities, the identity-addressed
bundle publication, and the extraction manifest.

Mirrors `era5_manifest.py`'s atomic tmp -> reopen-and-validate -> publish
discipline, extended for a MULTI-FILE bundle (D7): every output is written
into a staging directory keyed by `extraction_identity`, reopened and
validated there, then the whole directory is published as a unit and the
`CURRENT` pointer (one level ABOVE the identity directories, D7.2) is
switched last.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: src/sapphire_flow/adapters/meteoswiss_nwp.py:1 — xarray ships
# partial type stubs; the same three rules are relaxed repo-wide for every
# adapter that touches it.
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - pydantic must resolve this at runtime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from scripts.dhm_precip.era5_errors import (
    Era5StorageError,
    ExtractionPostConditionError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

# D7: the SET of delta statistics D1a computes — part of the identity so a
# future change to WHAT is computed forces regeneration even without a
# version bump, mirroring `transform_identity`'s own inputs.
DELTA_STATISTICS: tuple[str, ...] = ("absolute", "ratio", "sign_agreement")
EXTRACTION_CODE_VERSION = "1"


def points_root(data_root: Path) -> Path:
    return data_root / "era5_land" / "points"


def staging_dir(data_root: Path, *, identity: str) -> Path:
    return points_root(data_root) / ".staging" / identity


def published_dir(data_root: Path, *, identity: str) -> Path:
    return points_root(data_root) / identity


def current_pointer_path(data_root: Path) -> Path:
    return points_root(data_root) / "CURRENT"


def manifest_filename() -> str:
    return "extraction_manifest.json"


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def extraction_identity(
    *,
    operator_id: str,
    coordinate_table_sha256: str,
    source_sha256s: Sequence[str],
    orography_identity: str,
    jjas_months: Sequence[int],
    djf_months: Sequence[int],
    mam_months: Sequence[int],
    on_months: Sequence[int],
    wet_threshold_mm_per_h: float,
    wet_threshold_side: str,
    zero_policy: str,
    quantile_definition: str,
    quantile_grid: Sequence[float],
    station_elevation_datum: str,
    orography_elevation_datum: str,
    output_schema_version: str,
    output_format: str,
    output_dtype: str,
    output_encoding: Mapping[str, object],
    extraction_code_version: str = EXTRACTION_CODE_VERSION,
) -> str:
    """D7 — sha256(canonical-JSON of every VALUE-AFFECTING input). A version
    bump alone (`extraction_code_version`) forces regeneration, mirroring
    `era5_manifest.transform_identity`."""
    canonical = _canonical_json(
        {
            "operator_id": operator_id,
            "coordinate_table_sha256": coordinate_table_sha256,
            "source_sha256s": sorted(source_sha256s),
            "orography_identity": orography_identity,
            "seasons": {
                "jjas_months": list(jjas_months),
                "djf_months": list(djf_months),
                "mam_months": list(mam_months),
                "on_months": list(on_months),
            },
            "wet_threshold_mm_per_h": wet_threshold_mm_per_h,
            "wet_threshold_side": wet_threshold_side,
            "zero_policy": zero_policy,
            "quantile_definition": quantile_definition,
            "quantile_grid": list(quantile_grid),
            "delta_statistics": list(DELTA_STATISTICS),
            "station_elevation_datum": station_elevation_datum,
            "orography_elevation_datum": orography_elevation_datum,
            "output_schema_version": output_schema_version,
            "output_format": output_format,
            "output_dtype": output_dtype,
            "output_encoding": dict(output_encoding),
            "extraction_code_version": extraction_code_version,
        }
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, kw_only=True, slots=True)
class ExtractionManifest:
    """D9's `extraction_manifest.json` — the payload artefacts' sha256s
    ONLY, never its own hash, never the pointer's (D7.1)."""

    orography_identity: str
    extraction_identity: str
    operator_id: str
    coordinate_table_sha256: str
    source_sha256s: tuple[str, ...]
    payload_sha256s: dict[str, str]
    """Filename (relative to this identity directory) -> sha256."""
    orography_spec: dict[str, object]
    orography_source_record: dict[str, object]
    accumulation_diagnostic: dict[str, object]
    generated_at: datetime


class _ExtractionManifestModel(BaseModel):
    orography_identity: str
    extraction_identity: str
    operator_id: str
    coordinate_table_sha256: str
    source_sha256s: list[str]
    payload_sha256s: dict[str, str]
    orography_spec: dict[str, object]
    orography_source_record: dict[str, object]
    accumulation_diagnostic: dict[str, object]
    generated_at: datetime


def write_extraction_manifest(manifest: ExtractionManifest, path: Path) -> None:
    model = _ExtractionManifestModel(
        orography_identity=manifest.orography_identity,
        extraction_identity=manifest.extraction_identity,
        operator_id=manifest.operator_id,
        coordinate_table_sha256=manifest.coordinate_table_sha256,
        source_sha256s=list(manifest.source_sha256s),
        payload_sha256s=dict(manifest.payload_sha256s),
        orography_spec=manifest.orography_spec,
        orography_source_record=manifest.orography_source_record,
        accumulation_diagnostic=manifest.accumulation_diagnostic,
        generated_at=manifest.generated_at,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2))


def read_extraction_manifest(path: Path) -> ExtractionManifest | None:
    try:
        exists = path.exists()
    except OSError as exc:
        raise Era5StorageError(
            f"failed to stat extraction manifest at {path}: {exc}"
        ) from exc
    if not exists:
        return None
    try:
        text = path.read_text()
    except OSError as exc:
        raise Era5StorageError(
            f"failed to read extraction manifest at {path}: {exc}"
        ) from exc
    try:
        model = _ExtractionManifestModel.model_validate_json(text)
    except ValueError as exc:
        raise Era5StorageError(
            f"extraction manifest at {path} is unreadable: {exc}"
        ) from exc
    return ExtractionManifest(
        orography_identity=model.orography_identity,
        extraction_identity=model.extraction_identity,
        operator_id=model.operator_id,
        coordinate_table_sha256=model.coordinate_table_sha256,
        source_sha256s=tuple(model.source_sha256s),
        payload_sha256s=dict(model.payload_sha256s),
        orography_spec=model.orography_spec,
        orography_source_record=model.orography_source_record,
        accumulation_diagnostic=model.accumulation_diagnostic,
        generated_at=model.generated_at,
    )


def checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise Era5StorageError(f"failed to checksum {path}: {exc}") from exc
    return digest.hexdigest()


def _manifest_reconciles(directory: Path) -> bool:
    """A published directory's manifest is re-validated, never assumed good
    (Plan 171 D11's rule) — every listed payload file's sha256 must match
    what is actually on disk."""
    manifest_path = directory / manifest_filename()
    manifest = read_extraction_manifest(manifest_path)
    if manifest is None:
        return False
    for relative_name, expected_sha256 in manifest.payload_sha256s.items():
        candidate = directory / relative_name
        if not candidate.exists() or checksum_file(candidate) != expected_sha256:
            return False
    return True


def prepare_staging_dir(data_root: Path, *, identity: str) -> Path:
    """A staging directory left by a crashed prior run is unreferenced
    garbage — delete it, never resume/publish it."""
    staging = staging_dir(data_root, identity=identity)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    return staging


def _quarantine(directory: Path) -> Path:
    n = 0
    while True:
        candidate = directory.with_name(f"{directory.name}.orphan-{n}")
        if not candidate.exists():
            os.replace(directory, candidate)
            return candidate
        n += 1


def publish_bundle(
    staged_dir: Path, *, data_root: Path, identity: str, clock_now: datetime
) -> Path:
    """D7 — publish the completed staging directory as a unit, then switch
    the `CURRENT` pointer last. If `<identity>/` already exists: adopt it if
    its manifest reconciles (idempotent re-run), otherwise quarantine it and
    publish fresh (D7.3 — `os.replace` cannot swap a non-empty directory for
    another non-empty one)."""
    final_dir = published_dir(data_root, identity=identity)
    if final_dir.exists():
        if _manifest_reconciles(final_dir):
            shutil.rmtree(staged_dir)
        else:
            _quarantine(final_dir)
            os.replace(staged_dir, final_dir)
    else:
        os.replace(staged_dir, final_dir)

    pointer_path = current_pointer_path(data_root)
    tmp_pointer = pointer_path.with_name(pointer_path.name + ".tmp")
    try:
        pointer_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_pointer.write_text(identity)
        os.replace(tmp_pointer, pointer_path)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to publish CURRENT pointer at {pointer_path}: {exc}"
        ) from exc
    return final_dir


def read_current_identity(data_root: Path) -> str | None:
    pointer_path = current_pointer_path(data_root)
    try:
        if not pointer_path.exists():
            return None
        return pointer_path.read_text().strip()
    except OSError as exc:
        raise Era5StorageError(
            f"failed to read CURRENT pointer at {pointer_path}: {exc}"
        ) from exc


def reopen_and_validate_bundle(directory: Path, *, expected_station_count: int) -> None:
    """4a — reopen-and-validate every staged file before it is trusted
    (mirrors M-A4's own reopen-after-write discipline)."""
    import polars as pl
    import xarray as xr

    for name in ("series_nearest.nc", "series_bilinear.nc"):
        path = directory / name
        if not path.exists():
            raise ExtractionPostConditionError(
                f"staged bundle missing {name} at {path}"
            )
        with xr.open_dataset(path, engine="h5netcdf") as reopened:
            loaded = reopened.load()
            if "precipitation_mm_per_h" not in loaded:
                raise ExtractionPostConditionError(
                    f"{name} missing 'precipitation_mm_per_h' on reopen"
                )
            if loaded.sizes.get("station") != expected_station_count:
                raise ExtractionPostConditionError(
                    f"{name} has {loaded.sizes.get('station')} stations on reopen, "
                    f"expected {expected_station_count}"
                )

    elevation_path = directory / "station_grid_elevation.csv"
    if not elevation_path.exists():
        raise ExtractionPostConditionError(
            "staged bundle missing station_grid_elevation.csv"
        )
    elevation = pl.read_csv(elevation_path)
    if elevation.height != expected_station_count:
        raise ExtractionPostConditionError(
            f"station_grid_elevation.csv has {elevation.height} rows on reopen, "
            f"expected {expected_station_count}"
        )

    sensitivity_path = directory / "operator_sensitivity.csv"
    if not sensitivity_path.exists():
        raise ExtractionPostConditionError(
            "staged bundle missing operator_sensitivity.csv"
        )
    pl.read_csv(sensitivity_path)  # readable at all is the post-condition here

    manifest_path = directory / manifest_filename()
    if read_extraction_manifest(manifest_path) is None:
        raise ExtractionPostConditionError(
            f"staged bundle missing {manifest_filename()}"
        )
