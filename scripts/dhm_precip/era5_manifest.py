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
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise Era5StorageError(f"failed to checksum {path}: {exc}") from exc
    return digest.hexdigest()


def publish_atomic(tmp_path: Path, final_path: Path) -> None:
    """The D5 ordering's final step: `os.replace` onto the final path. The
    caller is responsible for having already reopened-and-validated
    `tmp_path` and computed its checksum before calling this."""
    try:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_path, final_path)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to publish {tmp_path} -> {final_path}: {exc}"
        ) from exc


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
    conservation_tolerance_m: float,
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
    snapshot (1b verification). `output_encoding` must be the FULL, actual
    on-disk encoding spec (compression, chunks, fill value, time units/dtype
    — everything D9 declares identity-relevant) — a partial spec here would
    let a changed encoding silently resume a stale product."""
    canonical = _canonical_json(
        {
            "raw_sha256s": list(raw_sha256s),
            "accumulation_rule_id": accumulation_rule_id,
            "packing_tolerance_mm": packing_tolerance_mm,
            "conservation_tolerance_m": conservation_tolerance_m,
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

    def __post_init__(self) -> None:
        # A "complete" provenance record whose fields are blank/whitespace
        # is not actually complete — pydantic's boundary validation only
        # checks the KEYS are present, not that the values carry real
        # content, so a `{"cds_portal_url": "  ", ...}` file would otherwise
        # pass silently.
        for field_name in (
            "cds_portal_url",
            "dataset_landing_page_url",
            "licence_name",
            "licence_version",
        ):
            value = getattr(self, field_name)
            if not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        for url_field in ("cds_portal_url", "dataset_landing_page_url"):
            value = getattr(self, url_field)
            if not (value.startswith("https://") or value.startswith("http://")):
                raise ValueError(f"{url_field} is not a URL: {value!r}")
        if self.licence_accepted_at.tzinfo is None:
            raise ValueError("licence_accepted_at must be timezone-aware")


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
class AccumulationDiagnosticRecord:
    """Plan 174 (M-A5) task 1c / D5.2 — `--stage diagnose`'s persisted
    result: the empirical accumulation-convention diagnostic run against a
    real acquired window, keyed by window id so multiple windows can each
    carry their own record. M-A5's real-data publish path (4a) cites this
    rather than re-establishing the semantics itself."""

    window_id: str
    source_sha256: str
    reset_hour: int
    terminal_hour: int
    monotone_within_day: bool
    sample_size_days: int
    recorded_at: datetime


@dataclass(frozen=True, kw_only=True, slots=True)
class Era5ProvenanceManifest:
    """D11 — the whole acquisition's provenance, one file, updated
    atomically after every completed stage."""

    dataset: str
    client_package_version: str
    operator_provenance: OperatorProvenance
    raw_windows: dict[str, RawWindowRecord] = field(
        default_factory=dict[str, "RawWindowRecord"]
    )
    transformed_years: dict[str, TransformYearRecord] = field(
        default_factory=dict[str, "TransformYearRecord"]
    )
    accumulation_diagnostics: dict[str, AccumulationDiagnosticRecord] = field(
        default_factory=dict[str, "AccumulationDiagnosticRecord"]
    )
    """Keyed by `window_id`. Absent on any manifest written before Plan 174
    task 1c — `read_manifest` must load such a manifest with this defaulting
    to `{}`, never raising."""


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


def with_accumulation_diagnostic(
    manifest: Era5ProvenanceManifest, record: AccumulationDiagnosticRecord
) -> Era5ProvenanceManifest:
    updated = dict(manifest.accumulation_diagnostics)
    updated[record.window_id] = record
    return replace(manifest, accumulation_diagnostics=updated)


MIN_DIAGNOSTIC_SAMPLE_DAYS = 28
"""B5/M-7 — the minimum number of whole accumulation days a diagnostic
sample must cover before its record may gate a publish. Plan 171's approved
sample window (`2021-10`) is a whole calendar month; the shortest calendar
month is 28 days, so anything below that is not a real window — a one-hour
boundary-context window yields 0 and used to satisfy the gate."""


def passing_accumulation_diagnostic(
    manifest: Era5ProvenanceManifest,
    *,
    expected_reset_hour: int,
    min_sample_size_days: int = MIN_DIAGNOSTIC_SAMPLE_DAYS,
) -> AccumulationDiagnosticRecord | None:
    """1c — "does this manifest carry a passing real-data diagnostic?"

    B5 (CORRECTED 2026-08-16) — the predicate used to test only
    `reset_hour` and `monotone_within_day`, ignoring the three fields that
    make the record TRUSTWORTHY rather than merely present. A passing record
    must now satisfy ALL of:

    * `reset_hour == expected_reset_hour` — D6's assumed `DAY_START_HOUR`,
      passed in by the caller to avoid importing `era5_deaccumulate`, which
      already imports this module;
    * `monotone_within_day` — no violation at the chosen reset;
    * `terminal_hour == (expected_reset_hour - 1) % 24` — the record's own
      fields must be mutually consistent, which catches a hand-edited or
      stale record written by a different diagnostic;
    * `sample_size_days >= min_sample_size_days` — a one-hour boundary
      window reports 0 whole days and cannot establish a daily convention;
    * `source_sha256` equal to the manifest's OWN raw-window record for the
      same window — the diagnostic must have been run against the bytes
      this manifest records, not against some other file of that name.

    Any real window that satisfies all five qualifies — M-A5 needs one.
    Iteration is ordered by window id so the answer is deterministic.
    """
    expected_terminal_hour = (expected_reset_hour - 1) % 24
    for _window_id, record in sorted(manifest.accumulation_diagnostics.items()):
        raw = manifest.raw_windows.get(record.window_id)
        if (
            record.reset_hour == expected_reset_hour
            and record.monotone_within_day
            and record.terminal_hour == expected_terminal_hour
            and record.sample_size_days >= min_sample_size_days
            and raw is not None
            and bool(record.source_sha256)
            and record.source_sha256 == raw.sha256
        ):
            return record
    return None


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


class _AccumulationDiagnosticRecordModel(BaseModel):
    window_id: str
    source_sha256: str
    reset_hour: int
    terminal_hour: int
    monotone_within_day: bool
    sample_size_days: int
    recorded_at: datetime


class _Era5ProvenanceManifestModel(BaseModel):
    dataset: str
    client_package_version: str
    operator_provenance: _OperatorProvenanceModel
    raw_windows: dict[str, _RawWindowRecordModel] = {}
    transformed_years: dict[str, _TransformYearRecordModel] = {}
    accumulation_diagnostics: dict[str, _AccumulationDiagnosticRecordModel] = {}


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
        accumulation_diagnostics={
            key: _AccumulationDiagnosticRecordModel(**asdict(record))
            for key, record in manifest.accumulation_diagnostics.items()
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
                product_year=record.product_year,
                transform_identity=record.transform_identity,
                sha256=record.sha256,
                accumulation_convention=record.accumulation_convention,
                units_conversion=record.units_conversion,
                packing=PackingAccounting(**record.packing.model_dump()),
                non_finite_cell_count=record.non_finite_cell_count,
                dropped_boundary_stamp=record.dropped_boundary_stamp,
                transformed_at=record.transformed_at,
            )
            for key, record in model.transformed_years.items()
        },
        accumulation_diagnostics={
            key: AccumulationDiagnosticRecord(**record.model_dump())
            for key, record in model.accumulation_diagnostics.items()
        },
    )


def write_manifest_atomic(manifest: Era5ProvenanceManifest, path: Path) -> None:
    """D11 — serialise to an adjacent temp file, then `os.replace`. A crash
    mid-serialisation never touches `path`: the previous manifest (if any)
    stays intact and readable."""
    payload = _to_model(manifest).model_dump_json(indent=2)
    tmp = tmp_path_for(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload)
        os.replace(tmp, path)
    except OSError as exc:
        raise Era5StorageError(f"failed to write manifest at {path}: {exc}") from exc


def read_manifest(path: Path) -> Era5ProvenanceManifest | None:
    try:
        exists = path.exists()
    except OSError as exc:
        raise Era5StorageError(f"failed to stat manifest at {path}: {exc}") from exc
    if not exists:
        return None
    try:
        text = path.read_text()
    except OSError as exc:
        raise Era5StorageError(f"failed to read manifest at {path}: {exc}") from exc
    try:
        model = _Era5ProvenanceManifestModel.model_validate_json(text)
        return _to_domain(model)
    except ValueError as exc:
        # `_to_domain` reconstructs frozen domain dataclasses (including
        # `OperatorProvenance`, whose `__post_init__` enforces non-blank
        # fields/URL shape/tz-aware timestamp) — a `ValueError` from THAT
        # step is exactly as much "the manifest is unreadable" as a pydantic
        # parse failure, and must be wrapped the same way rather than
        # escaping unwrapped.
        raise Era5StorageError(f"manifest at {path} is unreadable: {exc}") from exc


def load_operator_provenance(path: Path) -> OperatorProvenance:
    """D15 — the gitignored operator-provenance input (`--provenance`). A
    missing or incomplete file blocks manifest completion with a typed
    error; it never silently produces empty licence fields."""
    try:
        exists = path.exists()
    except OSError as exc:
        raise Era5StorageError(
            f"failed to stat operator provenance file at {path}: {exc}"
        ) from exc
    if not exists:
        raise Era5StorageError(f"operator provenance file not found: {path}")
    try:
        text = path.read_text()
    except OSError as exc:
        raise Era5StorageError(
            f"failed to read operator provenance file at {path}: {exc}"
        ) from exc
    try:
        model = _OperatorProvenanceModel.model_validate_json(text)
        return OperatorProvenance(**model.model_dump())
    except ValueError as exc:
        # Covers BOTH a pydantic parse failure (missing/mistyped keys) and a
        # `ValueError` from `OperatorProvenance.__post_init__` (blank
        # fields, a non-URL, a naive timestamp) — both mean the file is not
        # actually a complete, usable provenance record.
        raise Era5StorageError(
            f"operator provenance file at {path} is missing or incomplete: {exc}"
        ) from exc
