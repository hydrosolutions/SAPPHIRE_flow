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

from scripts.dhm_precip.domain_types import (
    DatumReconciliationStatus,
    OrographySource,
    SensitivityDeltaUnit,
    SensitivityScope,
    SensitivityStatistic,
    VerticalDatum,
)
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
    `era5_manifest.transform_identity`.

    P7a (major, slim review 2026-08-17) — the payload is split into two
    explicitly labelled halves. `value_inputs` are inputs the computation
    actually reads (P7's standing obligation: a test must change the input
    AND change an output); `invalidation_inputs`
    (`output_schema_version`/`extraction_code_version`) exist ONLY to force
    regeneration on a behaviour-neutral bump, so "changing this input must
    change an output" is unsatisfiable for them by design — labelling them
    makes that exemption visible instead of silently smuggling a
    value-determining field past P7's rule."""
    value_inputs: dict[str, object] = {
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
        "output_format": output_format,
        "output_dtype": output_dtype,
        "output_encoding": dict(output_encoding),
    }
    invalidation_inputs: dict[str, object] = {
        "output_schema_version": output_schema_version,
        "extraction_code_version": extraction_code_version,
    }
    canonical = _canonical_json(
        {"value_inputs": value_inputs, "invalidation_inputs": invalidation_inputs}
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
        staged_dir, expected_station_count=expected_station_count, identity=identity
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


# --- D9 payload schema — required columns and legal enum values, shared by
# publication (P4a) and any future discovery reader (P6), so the two can
# never apply a different predicate to the same bundle. ---
#
# BLOCKER (2026-08-17) — the previous validator checked only variable
# presence, station COUNTS and CSV readability: it never checked station
# uniqueness/equality across the two series and the elevation table, the
# elevation/sensitivity CSVs' required columns or enum values, the series'
# dims/dtype/time-axis/encoding/attributes, or that the manifest's own
# `extraction_identity` matches the identity it is being published under. A
# writer that emitted only `{station, marker}` columns still published
# successfully, hashing a malformed payload rather than refusing it.

ELEVATION_REQUIRED_COLUMNS: tuple[str, ...] = (
    "station",
    "lat",
    "lon",
    "grid_lat",
    "grid_lon",
    "grid_i",
    "grid_j",
    "offset_km",
    "station_elev_m",
    "station_elevation_datum",
    "orography_elev_m",
    "orography_elevation_datum",
    "orography_source",
    "orography_product_id",
    "orography_product_version",
    "elev_mismatch_m",
    "datum_reconciled",
    "shared_cell_id",
    "stations_in_cell",
)

SENSITIVITY_REQUIRED_COLUMNS: tuple[str, ...] = (
    "scope",
    "station",
    "season",
    "statistic",
    "quantile",
    "nearest_value",
    "bilinear_value",
    "delta_absolute",
    "delta_unit",
    "ratio",
    "n_hours_common_finite",
    "n_hours_excluded",
    "n_wet_nearest",
    "n_wet_bilinear",
    "sign_agreement_fraction",
)

_VALID_OROGRAPHY_SOURCES = {m.value for m in OrographySource}
_VALID_VERTICAL_DATA = {m.value for m in VerticalDatum}
_VALID_DATUM_RECONCILED = {m.value for m in DatumReconciliationStatus}
_VALID_SENSITIVITY_SCOPES = {m.value for m in SensitivityScope}
_VALID_SENSITIVITY_STATISTICS = {m.value for m in SensitivityStatistic}
_VALID_SENSITIVITY_DELTA_UNITS = {m.value for m in SensitivityDeltaUnit}


def _assert_columns_present(
    frame: object, *, required: tuple[str, ...], label: str
) -> None:
    missing = [c for c in required if c not in frame.columns]  # type: ignore[attr-defined]
    if missing:
        raise ExtractionPostConditionError(
            f"{label} is missing required column(s) {missing} (D9)"
        )


def _assert_enum_column(
    frame: object, *, column: str, valid: set[str], label: str
) -> None:
    observed = {v for v in frame[column].drop_nulls().to_list() if v is not None}  # type: ignore[index]
    bad = observed - valid
    if bad:
        raise ExtractionPostConditionError(
            f"{label}.{column} has value(s) {sorted(bad)} outside the "
            f"declared enum {sorted(valid)} (D9)"
        )


def _assert_series_schema(path: Path, *, expected_station_count: int) -> set[str]:
    """Reopen a D9 series file and validate its dims/dtype/time-axis/
    encoding/attributes; returns the declared station set for the
    cross-file station-equality check in `reopen_and_validate_bundle`."""
    import numpy as np
    import xarray as xr

    if not path.exists():
        raise ExtractionPostConditionError(
            f"staged bundle missing {path.name} at {path}"
        )
    with xr.open_dataset(path, engine="h5netcdf") as reopened:
        loaded = reopened.load()
        if "precipitation_mm_per_h" not in loaded:
            raise ExtractionPostConditionError(
                f"{path.name} missing 'precipitation_mm_per_h' on reopen"
            )
        var = loaded["precipitation_mm_per_h"]
        if tuple(var.dims) != ("station", "valid_time"):
            raise ExtractionPostConditionError(
                f"{path.name} 'precipitation_mm_per_h' has dims {tuple(var.dims)}, "
                "expected ('station', 'valid_time') (D9)"
            )
        if str(var.dtype) != "float32":
            raise ExtractionPostConditionError(
                f"{path.name} 'precipitation_mm_per_h' has dtype {var.dtype}, "
                "expected float32 (D9)"
            )
        stations = [str(s) for s in loaded["station"].values]
        if len(stations) != expected_station_count:
            raise ExtractionPostConditionError(
                f"{path.name} has {len(stations)} stations on reopen, "
                f"expected {expected_station_count}"
            )
        if len(set(stations)) != len(stations):
            raise ExtractionPostConditionError(
                f"{path.name} has duplicate station entries: {stations} (D8/D9)"
            )
        valid_time = loaded["valid_time"].values
        if valid_time.size > 1:
            diffs = np.diff(valid_time.astype("datetime64[s]"))
            if not bool(np.all(diffs > np.timedelta64(0, "s"))):
                raise ExtractionPostConditionError(
                    f"{path.name} 'valid_time' is not strictly increasing (D9)"
                )
        if loaded["valid_time"].attrs.get("timezone") != "UTC":
            raise ExtractionPostConditionError(
                f"{path.name} 'valid_time' is missing the semantic UTC "
                "attribute (D9/D5.0)"
            )
        if not var.encoding.get("zlib"):
            raise ExtractionPostConditionError(
                f"{path.name} 'precipitation_mm_per_h' is not zlib-compressed "
                "on reopen (D9's frozen encoding spec)"
            )
    return set(stations)


def reopen_and_validate_bundle(
    directory: Path, *, expected_station_count: int, identity: str
) -> None:
    """4a/P4a — reopen-and-validate every staged file before it is trusted
    (mirrors M-A4's own reopen-after-write discipline): station
    uniqueness/equality across both series and the elevation table, every
    D9-required CSV column and enum value, each series' dims/dtype/
    time-axis/encoding/attributes, the sensitivity schema, the manifest's
    own `extraction_identity` matching the identity this bundle is being
    published under, and (P4a) a full `payload_sha256s` reconciliation — a
    payload modified after its hash was computed must fail HERE, not
    silently pass publication and only fail a later discovery read."""
    import polars as pl

    nearest_stations = _assert_series_schema(
        directory / "series_nearest.nc", expected_station_count=expected_station_count
    )
    bilinear_stations = _assert_series_schema(
        directory / "series_bilinear.nc", expected_station_count=expected_station_count
    )
    if nearest_stations != bilinear_stations:
        raise ExtractionPostConditionError(
            "series_nearest.nc and series_bilinear.nc declare different "
            f"station sets: {sorted(nearest_stations)} vs "
            f"{sorted(bilinear_stations)} (D8/D9)"
        )

    elevation_path = directory / "station_grid_elevation.csv"
    if not elevation_path.exists():
        raise ExtractionPostConditionError(
            "staged bundle missing station_grid_elevation.csv"
        )
    elevation = pl.read_csv(elevation_path)
    _assert_columns_present(
        elevation,
        required=ELEVATION_REQUIRED_COLUMNS,
        label="station_grid_elevation.csv",
    )
    if elevation.height != expected_station_count:
        raise ExtractionPostConditionError(
            f"station_grid_elevation.csv has {elevation.height} rows on reopen, "
            f"expected {expected_station_count}"
        )
    elevation_stations = elevation["station"].to_list()
    if len(set(elevation_stations)) != len(elevation_stations):
        raise ExtractionPostConditionError(
            "station_grid_elevation.csv has duplicate station rows: "
            f"{elevation_stations} (D8/D9)"
        )
    if set(elevation_stations) != nearest_stations:
        raise ExtractionPostConditionError(
            "station_grid_elevation.csv's station set does not equal the "
            f"series' station set: {sorted(set(elevation_stations))} vs "
            f"{sorted(nearest_stations)} (D8/D9)"
        )
    _assert_enum_column(
        elevation,
        column="orography_source",
        valid=_VALID_OROGRAPHY_SOURCES,
        label="station_grid_elevation.csv",
    )
    _assert_enum_column(
        elevation,
        column="station_elevation_datum",
        valid=_VALID_VERTICAL_DATA,
        label="station_grid_elevation.csv",
    )
    _assert_enum_column(
        elevation,
        column="orography_elevation_datum",
        valid=_VALID_VERTICAL_DATA,
        label="station_grid_elevation.csv",
    )
    _assert_enum_column(
        elevation,
        column="datum_reconciled",
        valid=_VALID_DATUM_RECONCILED,
        label="station_grid_elevation.csv",
    )

    sensitivity_path = directory / "operator_sensitivity.csv"
    if not sensitivity_path.exists():
        raise ExtractionPostConditionError(
            "staged bundle missing operator_sensitivity.csv"
        )
    sensitivity = pl.read_csv(sensitivity_path)
    _assert_columns_present(
        sensitivity,
        required=SENSITIVITY_REQUIRED_COLUMNS,
        label="operator_sensitivity.csv",
    )
    _assert_enum_column(
        sensitivity,
        column="scope",
        valid=_VALID_SENSITIVITY_SCOPES,
        label="operator_sensitivity.csv",
    )
    _assert_enum_column(
        sensitivity,
        column="statistic",
        valid=_VALID_SENSITIVITY_STATISTICS,
        label="operator_sensitivity.csv",
    )
    _assert_enum_column(
        sensitivity,
        column="delta_unit",
        valid=_VALID_SENSITIVITY_DELTA_UNITS,
        label="operator_sensitivity.csv",
    )
    sensitivity_stations = {
        v
        for v in sensitivity.filter(pl.col("scope") == "STATION")["station"]
        .drop_nulls()
        .to_list()
    }
    if not sensitivity_stations <= nearest_stations:
        raise ExtractionPostConditionError(
            "operator_sensitivity.csv references station(s) outside the "
            f"series' station set: "
            f"{sorted(sensitivity_stations - nearest_stations)} (D8/D9)"
        )

    manifest_path = directory / manifest_filename()
    manifest = read_extraction_manifest(manifest_path)
    if manifest is None:
        raise ExtractionPostConditionError(
            f"staged bundle missing {manifest_filename()}"
        )
    if manifest.extraction_identity != identity:
        raise ExtractionPostConditionError(
            "extraction_manifest.json's extraction_identity "
            f"{manifest.extraction_identity!r} does not match the identity "
            f"this bundle is being published under {identity!r} (manifest "
            "identity consistency)"
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
