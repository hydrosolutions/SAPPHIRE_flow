"""M-A4 (Plan 171) task 1b — storage layout, the two D5 identity functions,
the atomic tmp -> os.replace writer, and the D11 provenance manifest.

Pydantic sits at the JSON boundary only (`_*Model` classes); every domain
type above that line is a frozen dataclass (CLAUDE.md "parse, don't
validate"), mirroring the precedent in `scripts/dhm_precip/manifest_io.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime  # noqa: TC003 - pydantic must resolve this at runtime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from scripts.dhm_precip.era5_errors import Era5StorageError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# --- storage layout (D1: under scripts/dhm_precip/, data under data/dhm_precip/) ---

DEFAULT_DATA_ROOT = Path("data/dhm_precip")
_MANIFEST_FILENAME = "era5_land_manifest.json"


def raw_dir(data_root: Path) -> Path:
    return data_root / "era5_land" / "raw"


def hourly_mm_dir(data_root: Path) -> Path:
    return data_root / "era5_land" / "hourly_mm"


def manifest_path_for(data_root: Path) -> Path:
    return data_root / "era5_land" / _MANIFEST_FILENAME


def raw_artifact_path(window_id: str, data_root: Path) -> Path:
    return raw_dir(data_root) / f"era5_land_tp_raw_{window_id}.nc"


def product_artifact_path(year: int, data_root: Path) -> Path:
    return hourly_mm_dir(data_root) / f"era5_land_tp_mm_{year:04d}.nc"


def tmp_path_for(final_path: Path) -> Path:
    return final_path.with_name(final_path.name + ".tmp")


# --- atomic primitives (D5, D11) ---


def checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_atomic(tmp_path: Path, final_path: Path) -> None:
    """The D5 ordering's final step: `os.replace` onto the final path. The
    caller is responsible for having already reopened-and-validated
    `tmp_path` and computed its checksum before calling this."""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_path, final_path)


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def raw_request_identity(dataset: str, payload: Mapping[str, object]) -> str:
    """D5 — `sha256(canonical-JSON of {dataset id, exact literal payload})`.
    Bounding-box and dataset-id changes are both captured because both live
    inside `dataset`/`payload`; key ordering does not affect the digest."""
    canonical = _canonical_json({"dataset": dataset, "payload": dict(payload)})
    return hashlib.sha256(canonical.encode()).hexdigest()


def transform_identity(
    *,
    raw_sha256s: Sequence[str],
    accumulation_rule_id: str,
    packing_tolerance_mm: float,
    units_factor: float,
    output_schema_version: str,
    transform_version: str,
    output_format: str,
    output_dtype: str,
    output_encoding: Mapping[str, object],
) -> str:
    """D5 — `sha256(canonical-JSON of {raw artifact sha256s consumed,
    transform parameter snapshot, output_schema_version, final
    format/dtype/encoding})`. `transform_version` is included so a version
    bump alone forces a re-transform even with an unchanged parameter
    snapshot (1b verification)."""
    canonical = _canonical_json(
        {
            "raw_sha256s": list(raw_sha256s),
            "accumulation_rule_id": accumulation_rule_id,
            "packing_tolerance_mm": packing_tolerance_mm,
            "units_factor": units_factor,
            "output_schema_version": output_schema_version,
            "transform_version": transform_version,
            "output_format": output_format,
            "output_dtype": output_dtype,
            "output_encoding": dict(output_encoding),
        }
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- domain types (D11) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class OperatorProvenance:
    """P0/D15 — supplied via `--provenance`, never committed, no secrets."""

    cds_portal_url: str
    dataset_landing_page_url: str
    licence_name: str
    licence_version: str
    licence_accepted_at: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class RawWindowRecord:
    window_id: str
    dataset: str
    request_payload: dict[str, object]
    raw_request_identity: str
    sha256: str
    client_package_version: str
    downloaded_at: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class PackingAccounting:
    """D7 — per-transform packing-correction accounting, in mm."""

    packing_corrected_cells: int
    max_correction_mm: float
    mass_adjustment_mm: float


@dataclass(frozen=True, kw_only=True, slots=True)
class TransformYearRecord:
    product_year: int
    transform_identity: str
    sha256: str
    accumulation_convention: str
    units_conversion: str
    packing: PackingAccounting
    non_finite_cell_count: int
    dropped_boundary_stamp: str | None
    transformed_at: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class Era5ProvenanceManifest:
    """D11 — the whole acquisition's provenance, one file, updated
    atomically after every completed stage."""

    dataset: str
    client_package_version: str
    operator_provenance: OperatorProvenance
    raw_windows: dict[str, RawWindowRecord] = field(default_factory=dict)
    transformed_years: dict[str, TransformYearRecord] = field(default_factory=dict)


def with_raw_window(
    manifest: Era5ProvenanceManifest, record: RawWindowRecord
) -> Era5ProvenanceManifest:
    updated = dict(manifest.raw_windows)
    updated[record.window_id] = record
    return replace(manifest, raw_windows=updated)


def with_transform_year(
    manifest: Era5ProvenanceManifest, record: TransformYearRecord
) -> Era5ProvenanceManifest:
    updated = dict(manifest.transformed_years)
    updated[str(record.product_year)] = record
    return replace(manifest, transformed_years=updated)


def raw_window_is_current(
    manifest: Era5ProvenanceManifest,
    *,
    window_id: str,
    expected_identity: str,
    final_path: Path,
) -> bool:
    """D5's four-way resume check for the acquisition stage."""
    record = manifest.raw_windows.get(window_id)
    if record is None:
        return False
    if record.raw_request_identity != expected_identity:
        return False
    if not final_path.exists():
        return False
    return checksum_file(final_path) == record.sha256


def transform_year_is_current(
    manifest: Era5ProvenanceManifest,
    *,
    year: int,
    expected_identity: str,
    final_path: Path,
) -> bool:
    """D5's four-way resume check for the transform stage."""
    record = manifest.transformed_years.get(str(year))
    if record is None:
        return False
    if record.transform_identity != expected_identity:
        return False
    if not final_path.exists():
        return False
    return checksum_file(final_path) == record.sha256


# --- pydantic boundary (JSON I/O) ---


class _OperatorProvenanceModel(BaseModel):
    cds_portal_url: str
    dataset_landing_page_url: str
    licence_name: str
    licence_version: str
    licence_accepted_at: datetime


class _RawWindowRecordModel(BaseModel):
    window_id: str
    dataset: str
    request_payload: dict[str, object]
    raw_request_identity: str
    sha256: str
    client_package_version: str
    downloaded_at: datetime


class _PackingAccountingModel(BaseModel):
    packing_corrected_cells: int
    max_correction_mm: float
    mass_adjustment_mm: float


class _TransformYearRecordModel(BaseModel):
    product_year: int
    transform_identity: str
    sha256: str
    accumulation_convention: str
    units_conversion: str
    packing: _PackingAccountingModel
    non_finite_cell_count: int
    dropped_boundary_stamp: str | None
    transformed_at: datetime


class _Era5ProvenanceManifestModel(BaseModel):
    dataset: str
    client_package_version: str
    operator_provenance: _OperatorProvenanceModel
    raw_windows: dict[str, _RawWindowRecordModel] = {}
    transformed_years: dict[str, _TransformYearRecordModel] = {}


def _to_model(manifest: Era5ProvenanceManifest) -> _Era5ProvenanceManifestModel:
    return _Era5ProvenanceManifestModel(
        dataset=manifest.dataset,
        client_package_version=manifest.client_package_version,
        operator_provenance=_OperatorProvenanceModel(
            **asdict(manifest.operator_provenance)
        ),
        raw_windows={
            key: _RawWindowRecordModel(**asdict(record))
            for key, record in manifest.raw_windows.items()
        },
        transformed_years={
            key: _TransformYearRecordModel(**asdict(record))
            for key, record in manifest.transformed_years.items()
        },
    )


def _to_domain(model: _Era5ProvenanceManifestModel) -> Era5ProvenanceManifest:
    return Era5ProvenanceManifest(
        dataset=model.dataset,
        client_package_version=model.client_package_version,
        operator_provenance=OperatorProvenance(
            **model.operator_provenance.model_dump()
        ),
        raw_windows={
            key: RawWindowRecord(**record.model_dump())
            for key, record in model.raw_windows.items()
        },
        transformed_years={
            key: TransformYearRecord(
                **{
                    **record.model_dump(exclude={"packing"}),
                    "packing": PackingAccounting(**record.packing.model_dump()),
                }
            )
            for key, record in model.transformed_years.items()
        },
    )


def write_manifest_atomic(manifest: Era5ProvenanceManifest, path: Path) -> None:
    """D11 — serialise to an adjacent temp file, then `os.replace`. A crash
    mid-serialisation never touches `path`: the previous manifest (if any)
    stays intact and readable."""
    payload = _to_model(manifest).model_dump_json(indent=2)
    tmp = tmp_path_for(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(payload)
    os.replace(tmp, path)


def read_manifest(path: Path) -> Era5ProvenanceManifest | None:
    if not path.exists():
        return None
    try:
        model = _Era5ProvenanceManifestModel.model_validate_json(path.read_text())
    except ValueError as exc:
        raise Era5StorageError(f"manifest at {path} is unreadable: {exc}") from exc
    return _to_domain(model)


def load_operator_provenance(path: Path) -> OperatorProvenance:
    """D15 — the gitignored operator-provenance input (`--provenance`). A
    missing or incomplete file blocks manifest completion with a typed
    error; it never silently produces empty licence fields."""
    if not path.exists():
        raise Era5StorageError(f"operator provenance file not found: {path}")
    try:
        model = _OperatorProvenanceModel.model_validate_json(path.read_text())
    except ValueError as exc:
        raise Era5StorageError(
            f"operator provenance file at {path} is missing or incomplete: {exc}"
        ) from exc
    return OperatorProvenance(**model.model_dump())
