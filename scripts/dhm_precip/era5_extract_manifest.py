"""Plan 174 (M-A5) task 4a — D7's two identities, the identity-addressed
bundle publication, and the extraction manifest.

**Publication model — P1-P6 (redesigned 2026-08-17, grill-me).** Four
independent review rounds each found a blocker in an "adopt if the manifest
reconciles" / `CURRENT`-pointer / quarantine design, and every fix relocated
the defect one layer down instead of removing it. The redesign removes
possibilities instead of adding checks:

- **P1/P1a** — the published directory is per-run UNIQUE:
  `<NNNN>-<extraction_identity>/`, with `NNNN` allocated by
  `mkdir(exist_ok=False)` (never scan-then-create, which races). `os.replace`
  therefore never faces a non-empty target — there is no rename, no
  quarantine, no `.orphan-<n>`, no window.
- **P2** — there is no `CURRENT` pointer. Nothing in production reads one.
  Discovery is "the highest `NNNN` whose manifest validates" — a documented
  convention (P6), not code here.
- **P3** — `extraction_identity` is a LABEL (directory name + manifest
  provenance), never a lookup key.
- **P4/P4a** — validation moves INSIDE `publish_bundle`, applying EXACTLY
  the predicate discovery would apply (including `payload_sha256s`
  reconciliation), so publication and discovery can never drift apart.
- **P5** — the manifest hashes the payload artefacts only, never itself.
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
from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 - pydantic must resolve this at runtime
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel

from scripts.dhm_precip.era5_errors import (
    Era5StorageError,
    ExtractionPostConditionError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

log = structlog.get_logger(__name__)

# D7: the SET of delta statistics D1a computes — part of the identity so a
# future change to WHAT is computed forces regeneration even without a
# version bump, mirroring `transform_identity`'s own inputs.
DELTA_STATISTICS: tuple[str, ...] = ("absolute", "ratio", "sign_agreement")
EXTRACTION_CODE_VERSION = "1"

# D9's payload set, EXACTLY — the artefacts the extraction manifest hashes
# beside itself (P5: never its own hash).
D9_PAYLOAD_FILES: tuple[str, ...] = (
    "series_nearest.nc",
    "series_bilinear.nc",
    "station_grid_elevation.csv",
    "operator_sensitivity.csv",
)


_RUN_NUMBER_WIDTH = 4
_MAX_RUN_NUMBER = 10**_RUN_NUMBER_WIDTH - 1


def points_root(data_root: Path) -> Path:
    return data_root / "era5_land" / "points"


def staging_dir(data_root: Path, *, identity: str) -> Path:
    return points_root(data_root) / ".staging" / identity


def published_dir(data_root: Path, *, run_number: int, identity: str) -> Path:
    """P1 — the per-run unique published directory name: a zero-padded run
    number joined to the (label-only, P3) identity. Two different inputs
    always sort by run order; the SAME inputs re-run still get a fresh
    number (P1a), so this can never collide with an existing directory."""
    return points_root(data_root) / f"{run_number:0{_RUN_NUMBER_WIDTH}d}-{identity}"


def _existing_run_numbers(data_root: Path) -> list[int]:
    root = points_root(data_root)
    if not root.exists():
        return []
    numbers: list[int] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name == ".staging":
            continue
        prefix = child.name.split("-", 1)[0]
        if prefix.isdigit():
            numbers.append(int(prefix))
    return numbers


def allocate_published_dir(data_root: Path, *, identity: str) -> Path:
    """P1a — allocate the next free run number by `mkdir(exist_ok=False)`,
    which is ATOMIC, never by "scan for a free number, then create it": two
    runs can otherwise both observe the same number as free. The scan below
    is only an optimisation (start just past the highest number seen); the
    actual race is resolved by retrying on `FileExistsError`, so the winner
    of any race owns the number by construction and the loser simply moves
    on to the next candidate. No lock, no reservation file, no single-writer
    precondition is required."""
    points_root(data_root).mkdir(parents=True, exist_ok=True)
    candidate = max(_existing_run_numbers(data_root), default=-1) + 1
    while candidate <= _MAX_RUN_NUMBER:
        target = published_dir(data_root, run_number=candidate, identity=identity)
        try:
            target.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            candidate += 1
            continue
        except OSError as exc:
            raise Era5StorageError(
                f"failed to reserve published directory {target}: {exc}"
            ) from exc
        return target
    raise Era5StorageError(
        f"no free run number <= {_MAX_RUN_NUMBER} under {points_root(data_root)}"
    )


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
    ONLY, never its own hash (P5)."""

    orography_identity: str
    extraction_identity: str
    operator_id: str
    coordinate_table_sha256: str
    source_sha256s: tuple[str, ...]
    payload_sha256s: dict[str, str]
    """Filename (relative to this identity directory) -> sha256."""
    orography_spec: dict[str, object]
    """EVERY `OrographySpec` field (D3a), not a four-field excerpt — the
    route is provenance, and a partially serialised route cannot be
    reconstructed."""
    orography_source_record: dict[str, object]
    """The materialised record: every downloaded file's path, sha256 and
    byte size, the derived raster's path/sha256, and both identities."""
    accumulation_diagnostic: dict[str, object]
    """The whole cited record (D5.2), including `terminal_hour`,
    `sample_size_days` and the injected-clock `recorded_at`."""
    station_accounting: dict[str, dict[str, dict[str, object]]] = field(
        default_factory=dict[str, dict[str, dict[str, object]]]
    )
    """D11 — operator id -> station -> {n_hours, n_finite, n_nan,
    first_nan_valid_time, last_nan_valid_time}. "The counts are emitted in
    the manifest either way" (D11.2), so they are reported for BOTH
    operators, including bilinear's counted missing-neighbour NaNs
    (D11.3)."""
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
    station_accounting: dict[str, dict[str, dict[str, object]]] = {}
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
        station_accounting=manifest.station_accounting,
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
        station_accounting=model.station_accounting,
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


def prepare_staging_dir(data_root: Path, *, identity: str) -> Path:
    """A staging directory left by a crashed prior run is unreferenced
    garbage — delete it, never resume/publish it."""
    staging = staging_dir(data_root, identity=identity)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    return staging


def publish_bundle(
    staged_dir: Path,
    *,
    data_root: Path,
    identity: str,
    expected_station_count: int,
) -> Path:
    """P1/P4 — validate the staged bundle FIRST, refusing to publish on
    failure (P4/P4a: this call applies EXACTLY the predicate discovery
    would apply, including `payload_sha256s` reconciliation — publication
    and discovery share one predicate, never two that can drift). Only on a
    passing validation does it allocate a per-run-unique numbered directory
    (P1a) and move the validated content into it.

    `os.replace` here never faces a non-empty target: `allocate_published_dir`
    reserves the target via `mkdir(exist_ok=False)` immediately before the
    move, so the directory is freshly created and empty, owned by this call
    alone. There is no `CURRENT` pointer (P2), no quarantine and no
    `.orphan-<n>` (P1 — a fresh numbered directory can never collide with a
    prior bundle, so there is nothing to displace), and no adoption of an
    existing bundle (D7.3, cut before this redesign for the same reason:
    three review rounds each found a blocker inside that one branch).
    Every previous bundle is left untouched.
    """
    reopen_and_validate_bundle(
        staged_dir, expected_station_count=expected_station_count
    )
    final_dir = allocate_published_dir(data_root, identity=identity)
    try:
        os.replace(staged_dir, final_dir)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to publish bundle from {staged_dir} to {final_dir}: {exc}"
        ) from exc
    log.info(
        "era5_extract.publish.published",
        identity=identity,
        published_dir=str(final_dir),
    )
    return final_dir


def reopen_and_validate_bundle(directory: Path, *, expected_station_count: int) -> None:
    """4a/P4a — reopen-and-validate every staged file before it is trusted
    (mirrors M-A4's own reopen-after-write discipline), INCLUDING a full
    `payload_sha256s` reconciliation against the manifest: a payload
    modified after its hash was computed must fail HERE, not silently pass
    publication and only fail a later discovery read (P4a)."""
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
    manifest = read_extraction_manifest(manifest_path)
    if manifest is None:
        raise ExtractionPostConditionError(
            f"staged bundle missing {manifest_filename()}"
        )

    # P4a — the same predicate discovery would apply: every D9 payload
    # file's sha256 must reconcile against what the manifest recorded.
    for name in D9_PAYLOAD_FILES:
        expected = manifest.payload_sha256s.get(name)
        if expected is None:
            raise ExtractionPostConditionError(
                f"extraction manifest at {manifest_path} records no sha256 "
                f"for {name!r} (P4a)"
            )
        actual = checksum_file(directory / name)
        if actual != expected:
            raise ExtractionPostConditionError(
                f"{name} sha256 {actual} does not match the manifest's "
                f"recorded {expected} — payload was modified after its "
                "hash was computed (P4a)"
            )
