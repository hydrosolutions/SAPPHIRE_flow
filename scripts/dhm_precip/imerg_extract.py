"""Plan 211 (M-A5b) T2 — hourly aggregation, point extraction and the D9
extraction record for IMERG Early. Rationale lives in Plan 211; the invariants
sit beside the code that enforces them. D3 mean-of-two-rates (mm/h) · D4
complete axis, NaN never synthesised · D5 period-ending · D6 cell centre +
station elevation, no mismatch table · D2 NEAREST primary, BILINEAR
sensitivity, both reusing `era5_extract`'s operators · D9 IMERG's own root,
reader and manifest, published and discovered through ONE predicate.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
import structlog  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from sapphire_flow.logging import configure_cli_logging  # noqa: E402
from scripts.dhm_precip.domain_types import (  # noqa: E402
    AccumulationConvention,
    ExtractionOperator,
    SensitivityDeltaUnit,
    SensitivityScope,
    SensitivityStatistic,
    Station,
    VerticalDatum,
)
from scripts.dhm_precip.era5_errors import (  # noqa: E402
    Era5AcquisitionError,
    Era5ExtractionError,
    Era5StorageError,
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
)
from scripts.dhm_precip.era5_extract import (  # noqa: E402
    ExtractedSeries,
    build_operator_sensitivity_table,
    extract_bilinear_series,
    extract_nearest_series,
    load_expected_station_coordinates,
)
from scripts.dhm_precip.era5_extract_manifest import (  # noqa: E402
    SENSITIVITY_REQUIRED_COLUMNS,
    assert_payload_checksum_matches,
)
from scripts.dhm_precip.era5_manifest import checksum_file  # noqa: E402
from scripts.dhm_precip.era5_request import STUDY_YEARS  # noqa: E402
from scripts.dhm_precip.imerg_acquire import (  # noqa: E402
    MEASURED_ACQUISITION_LATENCY,
    ImergAcquisitionError,
    ImergAcquisitionManifest,
    ImergCredentialsError,
    ImergReadContract,
    ImergStorageError,
    acquisition_completeness_violations,
    acquisition_manifest_path,
    assert_acquisition_manifest_complete,
    assert_contract_consistent,
    contract_from_open_granule,
    parse_granule_filename,
    pinned_provenance_violations,
    read_acquisition_manifest,
    window_accounting_violations,
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

    import xarray as xr

    from scripts.dhm_precip.params import DhmPrecipParams

log = structlog.get_logger(__name__)

# D8 — round every hourly value to 9 decimals, matching `ma7_profiles`.
_HOURLY_MEAN_ROUNDING_DECIMALS = 9

FIRST_HOUR: datetime = datetime(STUDY_YEARS[0], 1, 1, 0, 0, tzinfo=UTC)
LAST_HOUR: datetime = datetime(STUDY_YEARS[-1], 12, 31, 23, 0, tzinfo=UTC)
EXPECTED_HOUR_COUNT: int = int((LAST_HOUR - FIRST_HOUR) / timedelta(hours=1)) + 1
"""52,608 — the six study years' hours, half of `EXPECTED_GRANULE_COUNT`."""

EXPECTED_STATION_COUNT = 26
"""Plan 211's own scope: "extract at the 26 station locations". ⛔ A pin, NOT a
validator parameter: a caller passing its own count could publish a bundle
covering a handful of stations."""

IMERG_OUTPUT_SCHEMA_VERSION = "1"
IMERG_EXTRACTION_CODE_VERSION = "1"

MANIFEST_FILENAME = "extraction_manifest.json"
_NEAREST_SERIES_FILENAME = "series_nearest.csv"
_STATION_CELL_FILENAME = "station_cell_elevation.csv"
_SENSITIVITY_FILENAME = "operator_sensitivity.csv"
IMERG_PAYLOAD_FILES: tuple[str, ...] = (
    _NEAREST_SERIES_FILENAME,
    _STATION_CELL_FILENAME,
    _SENSITIVITY_FILENAME,
)

# Ordered subclass-first (mirrors extract_era5_t2m.py). The IMERG leaves carry
# T1's exit codes, so a mid-extraction D1 violation reports as it would at
# acquisition time.
_EXIT_BY_ERROR: tuple[tuple[type[Exception], int], ...] = (
    (DhmPrecipLoaderError, 2),
    (ExtractionInputAbsentError, 2),
    (ExtractionPostConditionError, 4),
    (Era5StorageError, 5),
    (Era5ExtractionError, 4),
    (ImergCredentialsError, 2),
    (ImergStorageError, 5),
    (ImergAcquisitionError, 3),
    # a raw OSError never reaches the typed hierarchy: it is storage.
    (OSError, 5),
)


def _exit_code_for(exc: Exception) -> int:
    for exc_type, code in _EXIT_BY_ERROR:
        if isinstance(exc, exc_type):
            return code
    return 1


def _default_expected_stations() -> frozenset[Station]:
    """The same workbook-derived usable-station inventory every other
    extraction CLI reads (replicated: it is private to `extract_era5.py`),
    reading the column inventory only, never a value."""
    source_path = resolve_source_path()
    _long_frame, inventory = load_long_frame(
        source_path, expected_sha256=PRODUCTION_SOURCE_SHA256
    )
    return frozenset(
        Station(name)
        for name in inventory.all_columns
        if name not in inventory.empty_columns
    )


# --- D3/D4/D5 — half-hourly -> hourly aggregation ---


def hourly_axis() -> tuple[datetime, ...]:
    """D4/D5 — the COMPLETE pinned output axis, one entry per hour."""
    return tuple(
        FIRST_HOUR + i * timedelta(hours=1) for i in range(EXPECTED_HOUR_COUNT)
    )


def aggregate_half_hourly_to_hourly(
    half_hourly: Mapping[datetime, float],
    *,
    hours: tuple[datetime, ...] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """D3/D4/D5 — one row per hour of a COMPLETE axis: the mean of the two
    half-hourly rates whose `E` falls in `(hour-1, hour]`. `granule_count`
    (0/1/2) counts how many EXIST; `non_finite_cell_count` how many of those
    read a fill value at this cell (D4 counts them SEPARATELY). Non-NaN IFF
    `granule_count == 2 and non_finite_cell_count == 0` — never synthesised
    from one granule, never averaged over a fill value."""
    axis = hourly_axis() if hours is None else hours
    values = np.full(len(axis), np.nan, dtype=np.float64)
    counts = np.zeros(len(axis), dtype=np.int64)
    non_finite_counts = np.zeros(len(axis), dtype=np.int64)
    for i, hour in enumerate(axis):
        r1 = half_hourly.get(hour - timedelta(hours=1))
        r2 = half_hourly.get(hour - timedelta(minutes=30))
        existing = [r for r in (r1, r2) if r is not None]
        counts[i] = len(existing)
        non_finite = [r for r in existing if not np.isfinite(r)]
        non_finite_counts[i] = len(non_finite)
        if len(existing) == 2 and len(non_finite) == 0:  # noqa: PLR2004
            values[i] = round(
                (existing[0] + existing[1]) / 2.0, _HOURLY_MEAN_ROUNDING_DECIMALS
            )
    valid_time = np.array(
        [np.datetime64(h.replace(tzinfo=None)) for h in axis], dtype="datetime64[s]"
    )
    return valid_time, values, counts, non_finite_counts


_STATION_ACCOUNTING_KEYS: tuple[str, ...] = (
    "n_hours",
    "n_hours_complete",
    "n_hours_partial",
    "n_hours_missing_granule",
    "n_hours_non_finite_cell",
    "n_hours_any_non_finite_cell",
    "n_granules_non_finite_cell",
    "n_nan_hours",
)


def _station_accounting_row(
    *, granule_count: np.ndarray, non_finite_cell_count: np.ndarray
) -> dict[str, object]:
    """D4's accounting, derived in ONE place — `run()` writes with it and
    `validate_imerg_bundle` RE-DERIVES from the published series. The four
    `n_hours_{complete,partial,missing_granule,non_finite_cell}` buckets
    partition the axis; the `*_any_*`/`n_granules_*` totals are NON-EXCLUSIVE,
    covering the one-granule non-finite hours the exclusive bucket misses."""
    complete = (granule_count == 2) & (non_finite_cell_count == 0)  # noqa: PLR2004
    return {
        "n_hours": int(granule_count.size),
        "n_hours_complete": int(np.sum(complete)),
        "n_hours_partial": int(np.sum(granule_count == 1)),
        "n_hours_missing_granule": int(np.sum(granule_count == 0)),
        "n_hours_non_finite_cell": int(
            np.sum((granule_count == 2) & (non_finite_cell_count > 0))  # noqa: PLR2004
        ),
        "n_hours_any_non_finite_cell": int(np.sum(non_finite_cell_count > 0)),
        "n_granules_non_finite_cell": int(np.sum(non_finite_cell_count)),
        "n_nan_hours": int(granule_count.size - np.sum(complete)),
    }


# --- per-granule reading, reusing era5_extract's operators verbatim ---


def read_granule(path: Path) -> tuple[xr.Dataset, ImergReadContract]:
    """Reshape one granule into the `(valid_time, latitude, longitude)` xarray
    layout `era5_extract`'s operators implement — no IMERG-specific extraction
    logic (D2). The D1 contract comes from the ONE parser both read paths
    share, in the same HDF5 open."""
    import h5py
    import xarray as xr

    granule, _revision = parse_granule_filename(path.name)
    with h5py.File(path, "r") as f:
        contract = contract_from_open_granule(f, filename=path.name)
        # (time, lon, lat), the dimension order D1 pins
        precip = np.asarray(f["/Grid/precipitation"][:], dtype=np.float64)  # type: ignore[index]
    lat = np.asarray(contract.lat_vector, dtype=np.float64)
    lon = np.asarray(contract.lon_vector, dtype=np.float64)
    precip = np.where(
        np.isclose(precip[0], contract.fill_value, atol=1e-3), np.nan, precip[0]
    ).T  # -> (lat, lon)
    ds = xr.Dataset(
        {
            "precipitation": (
                ("valid_time", "latitude", "longitude"),
                precip[np.newaxis],
            )
        },
        coords={
            "valid_time": np.array([np.datetime64(granule.start.replace(tzinfo=None))]),
            "latitude": lat,
            "longitude": lon,
        },
    )
    return ds, contract


# --- D9 — storage layout, identity, manifest, publisher, discovery ---

_RUN_NUMBER_WIDTH = 4
_MAX_RUN_NUMBER = 10**_RUN_NUMBER_WIDTH - 1
_STAGING_DIRNAME = ".staging"


def imerg_points_root(data_root: Path) -> Path:
    """D9 — IMERG's OWN root, never `era5_extract_manifest.points_root()`."""
    return data_root / "imerg_early" / "points"


def _published_dir(root: Path, *, run_number: int, identity: str) -> Path:
    return root / f"{run_number:0{_RUN_NUMBER_WIDTH}d}-{identity}"


def _reservation_path(root: Path, *, run_number: int) -> Path:
    return root / f".run-{run_number:0{_RUN_NUMBER_WIDTH}d}.reserve"


def _taken_run_numbers(root: Path) -> list[int]:
    if not root.exists():
        return []
    numbers: list[int] = []
    for child in root.iterdir():
        name = child.name
        if child.is_dir():
            if name == _STAGING_DIRNAME:
                continue
            prefix = name.split("-", 1)[0]
            if prefix.isdigit():
                numbers.append(int(prefix))
        elif name.startswith(".run-") and name.endswith(".reserve"):
            middle = name[len(".run-") : -len(".reserve")]
            if middle.isdigit():
                numbers.append(int(middle))
    return numbers


def _reserve_run_number(root: Path) -> int:
    """D9's allocator: the next free NNNN above the highest existing, valid or
    not — discovery skips an invalid bundle, allocation never REUSES it."""
    root.mkdir(parents=True, exist_ok=True)
    candidate = max(_taken_run_numbers(root), default=-1) + 1
    while candidate <= _MAX_RUN_NUMBER:
        reservation = _reservation_path(root, run_number=candidate)
        try:
            fd = os.open(str(reservation), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            candidate += 1
            continue
        except OSError as exc:
            raise Era5StorageError(
                f"failed to reserve run number {candidate} at {reservation}: {exc}"
            ) from exc
        os.close(fd)
        return candidate
    raise Era5StorageError(f"no free run number <= {_MAX_RUN_NUMBER} under {root}")


def allocate_published_dir(root: Path, *, identity: str) -> Path:
    run_number = _reserve_run_number(root)
    target = _published_dir(root, run_number=run_number, identity=identity)
    try:
        target.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to create reserved published directory {target}: {exc}"
        ) from exc
    return target


def prepare_staging_dir(root: Path) -> Path:
    """A token-named directory under `.staging`. ⛔ The identity is NOT in the
    name: it digests the manifest this run has yet to write."""
    staging = root / _STAGING_DIRNAME / uuid.uuid4().hex[:16]
    try:
        staging.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to create staging directory {staging}: {exc}"
        ) from exc
    return staging


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_of(obj: object) -> str:
    return hashlib.sha256(_canonical_json(obj).encode()).hexdigest()


def acquisition_record_digest(manifest: ImergAcquisitionManifest) -> str:
    """The canonical digest of the PERMANENT acquisition record's
    identity-bearing content (wall-clock provenance excluded — D9 never hashes
    time). ⛔ Hashing THIS is what lets the bundle stop carrying its own copies
    of the 105,216 checksums and the 5,400-float coordinate vectors: D10 makes
    the record their ONE owner, and two copies could only ever disagree."""
    return _sha256_of(
        manifest.model_dump(
            mode="json", exclude={"generated_at", "granule_retrieved_at"}
        )
    )


#: D5 — the exact literal every published manifest must carry.
EXPECTED_PERIOD_ENDING_CONVENTION = (
    "hour t covers t-1 -> t (UTC): built from the two half-hourly granules "
    "whose E falls in (t-1, t]"
)


class ImergExtractionManifest(BaseModel):
    """D9's `extraction_manifest.json` — the bundle IS the extraction record.
    The fields down to `extraction_code_version` are what
    `imerg_extraction_identity` hashes; everything below is provenance, never
    hashed (D9: not wall-clock time, not output paths)."""

    extraction_identity: str
    operator_id: str
    coordinate_table_sha256: str
    acquisition_record_sha256: str
    """`acquisition_record_digest` of the permanent T1 record (D10) that owns
    the per-granule checksums and the full D1 read contract; both digests are
    RECOMPUTED from it by `validate_imerg_bundle`."""
    read_contract_sha256: str
    route: str
    collection_short_name: str
    granule_revision: str
    acquisition_window_start: datetime
    acquisition_window_end: datetime
    """The half-hourly RETRIEVAL boundary from T1's record — never the output
    axis, which starts one hour later (D5)."""
    granules_requested: int
    granules_retrieved: int
    granules_missing: tuple[str, ...] = ()
    """D9's gaps, re-derived against the pinned window by the predicate."""
    box: tuple[int, int, int, int]
    sensitivity_params: dict[str, object] = {}
    """Plan 174 D1a's pinned definition — hashed: it determines the CSV."""
    output_schema_version: str = IMERG_OUTPUT_SCHEMA_VERSION
    extraction_code_version: str = IMERG_EXTRACTION_CODE_VERSION

    acquisition_generated_at: datetime
    output_axis_start: datetime
    output_axis_end: datetime
    timestamp_convention: AccumulationConvention
    period_ending_convention: str
    retrospective: bool
    measured_acquisition_latency: str
    payload_sha256s: dict[str, str] = {}
    station_accounting: dict[str, dict[str, object]] = {}
    n_stations: int
    n_hours: int
    generated_at: datetime


#: Everything `extraction_identity` digests (P7a: exactly what was READ).
_IDENTITY_FIELDS: set[str] = {
    "operator_id",
    "coordinate_table_sha256",
    "acquisition_record_sha256",
    "read_contract_sha256",
    "route",
    "collection_short_name",
    "granule_revision",
    "acquisition_window_start",
    "acquisition_window_end",
    "granules_requested",
    "granules_retrieved",
    "granules_missing",
    "box",
    "sensitivity_params",
    "output_schema_version",
    "extraction_code_version",
}


def imerg_extraction_identity(manifest: ImergExtractionManifest) -> str:
    """D9/P7a — the digest over exactly what T2 read, off the manifest's own
    fields so a reader recomputes it without having been the writer."""
    return _sha256_of(manifest.model_dump(mode="json", include=_IDENTITY_FIELDS))


def _write_manifest(manifest: ImergExtractionManifest, path: Path) -> None:
    path.write_text(manifest.model_dump_json(indent=2))


def _read_manifest(path: Path) -> ImergExtractionManifest | None:
    if not path.exists():
        return None
    try:
        text = path.read_text()
    except OSError as exc:
        raise Era5StorageError(
            f"failed to read IMERG extraction manifest at {path}: {exc}"
        ) from exc
    try:
        return ImergExtractionManifest.model_validate_json(text)
    except ValueError as exc:
        raise Era5StorageError(
            f"IMERG extraction manifest at {path} is unreadable: {exc}"
        ) from exc


_REQUIRED_NEAREST_COLUMNS: tuple[str, ...] = (
    "station_id",
    "timestamp_utc",
    "precip_mm_per_h",
    "granule_count",
    "non_finite_cell_count",
    "grid_lat",
    "grid_lon",
    "station_elev_m",
    "station_elevation_datum",
)
_REQUIRED_STATION_CELL_COLUMNS: tuple[str, ...] = (
    "station",
    "lat",
    "lon",
    "grid_lat",
    "grid_lon",
    "station_elev_m",
    "station_elevation_datum",
)

#: D6 — the per-station facts the series restates and the cell table owns.
_D6_STATION_COLUMNS: tuple[str, ...] = (
    "grid_lat",
    "grid_lon",
    "station_elev_m",
    "station_elevation_datum",
)

_PUBLISHED_RUN_NUMBER_RE = re.compile(rf"^\d{{{_RUN_NUMBER_WIDTH}}}$")

_VALID_SENSITIVITY_SCOPES: frozenset[str] = frozenset(m.value for m in SensitivityScope)
_VALID_SENSITIVITY_STATISTICS: frozenset[str] = frozenset(
    m.value for m in SensitivityStatistic
)
_VALID_SENSITIVITY_DELTA_UNITS: frozenset[str] = frozenset(
    m.value for m in SensitivityDeltaUnit
)


def _directory_declares_identity(directory: Path, *, identity: str) -> bool:
    """EXACT match against `NNNN-<identity>`: a substring match would accept a
    directory merely CONTAINING the identity."""
    prefix, sep, rest = directory.name.partition("-")
    return (
        bool(sep)
        and bool(_PUBLISHED_RUN_NUMBER_RE.fullmatch(prefix))
        and rest == identity
    )


#: An EXPLICIT schema: a non-numeric cell then fails the read (as a validation
#: failure) and an all-null column arrives typed instead of as `String`.
_NEAREST_NUMERIC_SCHEMA: dict[str, pl.DataType] = {
    "precip_mm_per_h": pl.Float64(),
    "granule_count": pl.Int64(),
    "non_finite_cell_count": pl.Int64(),
}


def _read_csv_or_reject(
    path: Path, *, schema_overrides: dict[str, pl.DataType] | None = None
) -> pl.DataFrame:
    """A malformed payload is a VALIDATION failure, not a crash: discovery
    SKIPS an invalid higher bundle, a raw polars exception prevents that."""
    try:
        return pl.read_csv(path, schema_overrides=schema_overrides)
    except (pl.exceptions.PolarsError, OSError, ValueError) as exc:
        raise ExtractionPostConditionError(
            f"{path.name} in {path.parent} is not a readable CSV: {exc} (D9)"
        ) from exc


def _acquisition_agreement_violations(
    manifest: ImergExtractionManifest, record: ImergAcquisitionManifest
) -> tuple[str, ...]:
    """D9/D10/P7a — RECOMPUTE the bundle's two digests from the permanent
    record they name and reconcile every acquisition field the bundle restates
    against it. ⛔ A digest accepted as a caller-supplied string authenticates
    nothing: the identity would be self-consistent while describing granules
    nobody acquired. Keyed by the BUNDLE's names, valued by the record's."""
    expected: dict[str, object] = {
        "acquisition_record_sha256": acquisition_record_digest(record),
        "read_contract_sha256": _sha256_of(record.read_contract),
        "route": record.route,
        "collection_short_name": record.collection_short_name,
        "granule_revision": record.granule_revision,
        "acquisition_window_start": record.requested_window_start,
        "acquisition_window_end": record.requested_window_end,
        "granules_requested": record.requested,
        "granules_retrieved": record.retrieved,
        "granules_missing": tuple(record.missing),
        "box": tuple(record.box),
    }
    return tuple(
        f"{field} disagrees with the permanent acquisition record"
        for field, value in expected.items()
        if getattr(manifest, field) != value
    )


def validate_imerg_bundle(
    directory: Path, manifest: ImergExtractionManifest, *, data_root: Path
) -> None:
    """D9 — ONE validation predicate, applied identically by publication and
    discovery. ⛔ It OWNS the plan's invariants — the pinned
    route/collection/revision/box/operator, the whole D5 window, the 26
    stations, the complete hourly axis and the permanent acquisition record —
    rather than accepting them as parameters: a caller passing its own
    expectations could publish an under-complete bundle by bypassing `run()`,
    and discovery would accept it too."""
    if directory.parent.name != _STAGING_DIRNAME and not _directory_declares_identity(
        directory, identity=manifest.extraction_identity
    ):
        raise ExtractionPostConditionError(
            f"manifest extraction_identity {manifest.extraction_identity!r} "
            f"does not exactly match its own directory name {directory.name!r} (D9)"
        )
    pinned = pinned_provenance_violations(
        route=manifest.route,
        collection_short_name=manifest.collection_short_name,
        granule_revision=manifest.granule_revision,
        box=manifest.box,
        retrospective=manifest.retrospective,
    )
    if pinned:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest contradicts the plan's pins (D1/D7): "
            + "; ".join(pinned)
        )
    # D9 — the SAME accounting invariant the permanent record must satisfy. ⛔
    # Here, not only in `run()`: else a hand-built bundle claiming one
    # requested granule publishes, and discovery accepts it.
    accounting = window_accounting_violations(
        requested=manifest.granules_requested,
        retrieved=manifest.granules_retrieved,
        missing=manifest.granules_missing,
        window_start=manifest.acquisition_window_start,
        window_end=manifest.acquisition_window_end,
    )
    if accounting:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's acquisition accounting does not cover "
            "the pinned D5 window (D9/D5): " + "; ".join(accounting)
        )
    # D9/D10 — the bundle carries only DIGESTS of the acquisition record and
    # the D1 read contract, so the predicate RESOLVES the permanent record and
    # recomputes them; otherwise they are caller-supplied strings.
    try:
        record = read_acquisition_manifest(acquisition_manifest_path(data_root))
    except ImergStorageError as exc:
        # ⛔ Typed, not foreign: `ImergStorageError` is a different hierarchy,
        # so an unreadable record ABORTED discovery instead of being skipped.
        raise ExtractionPostConditionError(
            "the permanent IMERG acquisition record at "
            f"{acquisition_manifest_path(data_root)} is unreadable: {exc} (D9/D10)"
        ) from exc
    if record is None:
        raise ExtractionPostConditionError(
            "no permanent IMERG acquisition record at "
            f"{acquisition_manifest_path(data_root)} — the bundle's "
            "acquisition_record_sha256 addresses nothing (D9/D10)"
        )
    provenance = acquisition_completeness_violations(
        record
    ) + _acquisition_agreement_violations(manifest, record)
    if provenance:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest is not backed by the permanent "
            "acquisition record it names (D9/D10): " + "; ".join(provenance)
        )
    # D2 — operator and sensitivity definition are PINS, both hashed into the
    # identity: a bundle naming its own would publish statistics nobody
    # computed, under a self-consistent digest.
    if manifest.operator_id != str(ExtractionOperator.NEAREST):
        raise ExtractionPostConditionError(
            f"IMERG extraction manifest's operator_id {manifest.operator_id!r} "
            f"!= the D2-pinned {str(ExtractionOperator.NEAREST)!r} (D2/D9)"
        )
    if manifest.sensitivity_params != _sensitivity_params(DEFAULT_PARAMS):
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's sensitivity_params != Plan 174 D1a's "
            "pinned sensitivity definition (D2/D9)"
        )
    if manifest.timestamp_convention is not AccumulationConvention.PERIOD_ENDING:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's timestamp_convention must be "
            "PERIOD_ENDING (D5)"
        )
    if manifest.period_ending_convention != EXPECTED_PERIOD_ENDING_CONVENTION:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's period_ending_convention != the "
            f"expected literal {EXPECTED_PERIOD_ENDING_CONVENTION!r} (D5)"
        )
    for name in IMERG_PAYLOAD_FILES:
        try:
            assert_payload_checksum_matches(directory, manifest, name)
        except OSError as exc:
            raise ExtractionPostConditionError(
                f"payload {name!r} in {directory} cannot be read: {exc} (P4a)"
            ) from exc

    nearest = _read_csv_or_reject(
        directory / _NEAREST_SERIES_FILENAME,
        schema_overrides=_NEAREST_NUMERIC_SCHEMA,
    )
    missing_cols = set(_REQUIRED_NEAREST_COLUMNS) - set(nearest.columns)
    if missing_cols:
        raise ExtractionPostConditionError(
            f"{_NEAREST_SERIES_FILENAME} is missing column(s) "
            f"{sorted(missing_cols)} (D9)"
        )
    stations_present = nearest["station_id"].unique().to_list()
    if len(stations_present) != EXPECTED_STATION_COUNT:
        raise ExtractionPostConditionError(
            f"{_NEAREST_SERIES_FILENAME} has {len(stations_present)} distinct "
            f"stations, expected the pinned {EXPECTED_STATION_COUNT} (D9)"
        )
    # D9/D5 — the EXACT expected hourly axis, per station, not merely a row
    # count: a duplicate timestamp and a missing one can share a count.
    expected_axis = [
        str(np.datetime64(h.replace(tzinfo=None), "s")) for h in hourly_axis()
    ]
    for _station_id, group in nearest.sort("timestamp_utc").group_by(
        "station_id", maintain_order=True
    ):
        if group.sort("timestamp_utc")["timestamp_utc"].to_list() != expected_axis:
            raise ExtractionPostConditionError(
                f"{_NEAREST_SERIES_FILENAME} station {_station_id!r} does not "
                "carry the exact expected hourly axis (D5/D9)"
            )
    # ⛔ Nullness first: `~col.is_in([0,1,2])` is NULL on a null cell, which
    # `filter` drops — a null count would pass every range check below.
    for count_column in ("granule_count", "non_finite_cell_count"):
        if nearest[count_column].null_count():
            raise ExtractionPostConditionError(
                f"{_NEAREST_SERIES_FILENAME}.{count_column} has "
                f"{nearest[count_column].null_count()} null value(s) — a count "
                "is never absent (D4/D9)"
            )
    # 0 <= non_finite_cell_count <= granule_count <= 2: a granule cannot be
    # non-finite without existing, so the counts are ordered.
    bad_counts = nearest.filter(
        ~pl.col("granule_count").is_in([0, 1, 2])
        | ~pl.col("non_finite_cell_count").is_in([0, 1, 2])
        | (pl.col("non_finite_cell_count") > pl.col("granule_count"))
    )
    if bad_counts.height:
        raise ExtractionPostConditionError(
            f"{_NEAREST_SERIES_FILENAME} has {bad_counts.height} row(s) violating "
            "0 <= non_finite_cell_count <= granule_count <= 2 (D4/D9)"
        )
    # D4 — precip_mm_per_h is FINITE IFF granule_count == 2 AND
    # non_finite_cell_count == 0. Both directions, and finiteness not nullness:
    # a NaN on a complete hour is not a rate, a value on a partial one is
    # invented.
    complete_and_finite = (pl.col("granule_count") == 2) & (  # noqa: PLR2004
        pl.col("non_finite_cell_count") == 0
    )
    value_is_finite = pl.col("precip_mm_per_h").is_finite().fill_null(value=False)
    inconsistent = nearest.filter(complete_and_finite != value_is_finite)
    if inconsistent.height:
        raise ExtractionPostConditionError(
            f"{_NEAREST_SERIES_FILENAME} has {inconsistent.height} row(s) whose "
            "precip_mm_per_h/granule_count/non_finite_cell_count disagree — "
            "a complete, cell-finite hour must carry a FINITE rate and no "
            "other hour may carry one (D4)"
        )

    station_cell = _read_csv_or_reject(directory / _STATION_CELL_FILENAME)
    missing_cell_cols = set(_REQUIRED_STATION_CELL_COLUMNS) - set(station_cell.columns)
    if missing_cell_cols:
        raise ExtractionPostConditionError(
            f"{_STATION_CELL_FILENAME} is missing column(s) "
            f"{sorted(missing_cell_cols)} (D6/D9)"
        )
    station_cell_stations = station_cell["station"].to_list()
    if len(set(station_cell_stations)) != len(station_cell_stations):
        raise ExtractionPostConditionError(
            f"{_STATION_CELL_FILENAME} has duplicate station(s) (D9)"
        )
    if set(station_cell_stations) != set(stations_present):
        raise ExtractionPostConditionError(
            f"{_STATION_CELL_FILENAME}'s station set does not match "
            f"{_NEAREST_SERIES_FILENAME}'s (D9)"
        )
    # ⛔ Nullness first, in BOTH payloads: a null datum is not `!= "UNKNOWN"`,
    # and two matching nulls satisfy the agreement check below — so a bundle
    # recording no cell/elevation at all would pass every check that follows.
    for frame, filename in (
        (nearest, _NEAREST_SERIES_FILENAME),
        (station_cell, _STATION_CELL_FILENAME),
    ):
        null_columns = [c for c in _D6_STATION_COLUMNS if frame[c].null_count()]
        if null_columns:
            raise ExtractionPostConditionError(
                f"{filename} has null value(s) in {null_columns} — a station's "
                "cell and elevation are recorded, never absent (D6/D9)"
            )
    # D6 — the datum is UNKNOWN (DHM has stated none), and the series' per-hour
    # cell/elevation metadata must be CONSTANT per station and equal to that
    # station's own row here. ⛔ Two records of one fact can only ever disagree.
    if station_cell.filter(
        pl.col("station_elevation_datum") != str(VerticalDatum.UNKNOWN)
    ).height:
        raise ExtractionPostConditionError(
            f"{_STATION_CELL_FILENAME}.station_elevation_datum must be "
            f"{str(VerticalDatum.UNKNOWN)!r} (D6/D9)"
        )
    per_station = nearest.select("station_id", *_D6_STATION_COLUMNS).unique()
    if per_station.height != len(stations_present):
        raise ExtractionPostConditionError(
            f"{_NEAREST_SERIES_FILENAME} varies {list(_D6_STATION_COLUMNS)} within a "
            "station — a station's cell and elevation are one fact (D6/D9)"
        )
    if (
        per_station.sort("station_id").rows()
        != station_cell.select(
            pl.col("station").alias("station_id"), *_D6_STATION_COLUMNS
        )
        .sort("station_id")
        .rows()
    ):
        raise ExtractionPostConditionError(
            f"{_NEAREST_SERIES_FILENAME}'s per-station cell/elevation metadata "
            f"disagrees with {_STATION_CELL_FILENAME}'s (D6/D9)"
        )

    sensitivity = _read_csv_or_reject(directory / _SENSITIVITY_FILENAME)
    missing_sensitivity_cols = set(SENSITIVITY_REQUIRED_COLUMNS) - set(
        sensitivity.columns
    )
    if missing_sensitivity_cols:
        raise ExtractionPostConditionError(
            f"{_SENSITIVITY_FILENAME} is missing column(s) "
            f"{sorted(missing_sensitivity_cols)} (D1a/D9)"
        )
    for column, allowed in (
        ("scope", _VALID_SENSITIVITY_SCOPES),
        ("statistic", _VALID_SENSITIVITY_STATISTICS),
        ("delta_unit", _VALID_SENSITIVITY_DELTA_UNITS),
    ):
        bad = sensitivity.filter(
            pl.col(column).is_not_null() & ~pl.col(column).is_in(list(allowed))
        )
        if bad.height:
            raise ExtractionPostConditionError(
                f"{_SENSITIVITY_FILENAME}.{column} has value(s) outside "
                f"{sorted(allowed)} (D1a/D9)"
            )

    if set(manifest.station_accounting) != {str(s) for s in stations_present}:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's station_accounting station set does "
            "not match the published series' station set (D4/D9)"
        )
    # ⛔ RE-DERIVED from the series, not merely summed: numbers that add up to
    # the hour count can still be swapped, stale or invented.
    for group_key, group in nearest.group_by("station_id", maintain_order=True):
        station_id = str(group_key[0])
        expected_accounting = _station_accounting_row(
            granule_count=group["granule_count"].to_numpy(),
            non_finite_cell_count=group["non_finite_cell_count"].to_numpy(),
        )
        recorded = manifest.station_accounting[station_id]
        if set(recorded) != set(_STATION_ACCOUNTING_KEYS):
            raise ExtractionPostConditionError(
                f"IMERG extraction manifest's station_accounting[{station_id!r}] "
                f"has key(s) {sorted(recorded)}, expected exactly "
                f"{sorted(_STATION_ACCOUNTING_KEYS)} (D4/D9)"
            )
        try:
            differing = {
                key: (recorded[key], expected_accounting[key])
                for key in _STATION_ACCOUNTING_KEYS
                if int(recorded[key]) != int(expected_accounting[key])  # type: ignore[arg-type,call-overload]
            }
        except (TypeError, ValueError) as exc:
            # ⛔ Typed, not raw: D9's discovery SKIPS an invalid higher bundle,
            # which an escaping ValueError prevents.
            raise ExtractionPostConditionError(
                f"IMERG extraction manifest's station_accounting[{station_id!r}] "
                f"holds a non-integer count: {exc} (D4/D9)"
            ) from exc
        if differing:
            raise ExtractionPostConditionError(
                f"IMERG extraction manifest's station_accounting[{station_id!r}] "
                f"disagrees with the published series on {sorted(differing)} "
                f"(recorded vs re-derived: {differing}) (D4/D9)"
            )
    if manifest.n_stations != EXPECTED_STATION_COUNT:
        raise ExtractionPostConditionError(
            f"IMERG extraction manifest's n_stations {manifest.n_stations} != "
            f"the pinned {EXPECTED_STATION_COUNT} (D9)"
        )
    if manifest.n_hours != EXPECTED_HOUR_COUNT:
        raise ExtractionPostConditionError(
            f"IMERG extraction manifest's n_hours {manifest.n_hours} != the "
            f"pinned {EXPECTED_HOUR_COUNT} (D5/D9)"
        )
    if imerg_extraction_identity(manifest) != manifest.extraction_identity:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's extraction_identity does not match "
            "the digest recomputed from its own identity-bearing fields (D9/P7a)"
        )
    # The axis endpoints are a claim about the PAYLOAD, so they are checked
    # against it, not merely against each other.
    if [
        str(np.datetime64(manifest.output_axis_start.replace(tzinfo=None), "s")),
        str(np.datetime64(manifest.output_axis_end.replace(tzinfo=None), "s")),
    ] != [expected_axis[0], expected_axis[-1]]:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's output_axis_start/end do not match "
            "the published series' own first/last timestamps (D5/D9)"
        )


def publish_imerg_bundle(staged_dir: Path, *, data_root: Path, identity: str) -> Path:
    manifest = _read_manifest(staged_dir / MANIFEST_FILENAME)
    if manifest is None:
        raise ExtractionPostConditionError(
            f"staged IMERG bundle at {staged_dir} is missing a readable "
            f"{MANIFEST_FILENAME}"
        )
    if identity != manifest.extraction_identity:
        raise ExtractionPostConditionError(
            f"publish identity {identity!r} != the staged manifest's own "
            f"extraction_identity {manifest.extraction_identity!r} (D9) — "
            "refusing to publish a bundle under a label that disagrees with "
            "its own manifest"
        )
    validate_imerg_bundle(staged_dir, manifest, data_root=data_root)
    final_dir = allocate_published_dir(imerg_points_root(data_root), identity=identity)
    try:
        os.replace(staged_dir, final_dir)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to publish IMERG bundle from {staged_dir} to {final_dir}: {exc}"
        ) from exc
    log.info(
        "imerg_extract.publish.published",
        identity=identity,
        published_dir=str(final_dir),
    )
    return final_dir


def discover_imerg_bundle(data_root: Path) -> tuple[Path, ImergExtractionManifest]:
    """P2/P6 — the highest `NNNN` validating against publication's predicate."""
    root = imerg_points_root(data_root)
    if not root.exists():
        raise ExtractionInputAbsentError(f"no IMERG extraction points root at {root}")
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name != _STAGING_DIRNAME),
        key=lambda p: p.name,
    )
    last_error: Era5AcquisitionError | None = None
    for candidate in reversed(candidates):
        # ⛔ The manifest READ is inside the guard too: a malformed one would
        # otherwise abort discovery instead of falling back to the next valid.
        try:
            manifest = _read_manifest(candidate / MANIFEST_FILENAME)
            if manifest is None:
                continue
            validate_imerg_bundle(candidate, manifest, data_root=data_root)
        except (ExtractionPostConditionError, Era5StorageError) as exc:
            log.warning(
                "imerg_extract.discover.validation_failed_skipped",
                candidate=str(candidate),
            )
            last_error = exc
            continue
        return candidate, manifest
    if last_error is not None:
        raise last_error
    raise ExtractionInputAbsentError(
        f"no published IMERG extraction bundle with a validating manifest under {root}"
    )


# --- the run() orchestrator ---


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="imerg_extract", description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data/dhm_precip"))
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Copy the published bundle (manifest + the three payload CSVs) to "
            "this NEW OR EMPTY directory, so two runs can be diffed for D8 "
            "determinism (excluding generated_at); the bundle publishes to its "
            "identity-addressed directory regardless (D9). ⛔ An existing "
            "non-empty directory is REFUSED, never erased."
        ),
    )
    return parser


def _sensitivity_params(params: DhmPrecipParams) -> dict[str, object]:
    """Plan 174 D1a's pinned sensitivity definition — seasons, wet-hour policy
    and quantiles, reused rather than re-chosen (D2), and validated by the
    publish/discovery predicate against exactly this snapshot."""
    return {
        "seasons": {
            "jjas_months": list(params.jjas_months),
            "djf_months": list(params.djf_months),
            "mam_months": list(params.mam_months),
            "on_months": list(params.on_months),
        },
        "wet_threshold_mm_per_h": params.wet_threshold_mm_per_h,
        "wet_threshold_side": params.wet_threshold_side,
        "zero_policy": params.zero_policy,
        "quantile_definition": params.quantile_definition,
        "quantile_grid": list(params.quantile_grid),
    }


def run(
    args: argparse.Namespace,
    *,
    clock: Callable[[], datetime] | None = None,
    coords_path: Path | None = None,
    expected_stations: frozenset[Station] | None = None,
) -> int:
    from scripts.dhm_precip.imerg_acquire import imerg_raw_dir

    data_root: Path = args.data_root
    resolved_clock: Callable[[], datetime] = (
        clock if clock is not None else (lambda: datetime.now(UTC))
    )
    resolved_coords_path = (
        coords_path if coords_path is not None else resolve_coords_path()
    )
    stations = load_expected_station_coordinates(
        resolved_coords_path,
        expected_stations=(
            expected_stations
            if expected_stations is not None
            else _default_expected_stations()
        ),
    )
    coordinate_table_sha256 = checksum_file(resolved_coords_path)

    # D9 — the handoff IS the manifest: T2 never re-lists raw/, and DERIVES
    # completeness from the record's contents before opening any granule.
    acquisition = read_acquisition_manifest(acquisition_manifest_path(data_root))
    if acquisition is None:
        raise ExtractionInputAbsentError(
            f"no IMERG acquisition manifest at "
            f"{acquisition_manifest_path(data_root)} — T1 must run first (D9)"
        )
    assert_acquisition_manifest_complete(acquisition)

    granule_paths = [
        imerg_raw_dir(data_root) / name
        for name in sorted(acquisition.granule_checksums)
    ]
    half_hourly_nearest: dict[Station, dict[datetime, float]] = {
        s: {} for s in stations.stations
    }
    half_hourly_bilinear: dict[Station, dict[datetime, float]] = {
        s: {} for s in stations.stations
    }
    grid_cell_by_station: dict[Station, tuple[float, float]] = {}
    frozen_contract: ImergReadContract | None = None

    for path in granule_paths:
        if not path.exists():
            raise ExtractionInputAbsentError(
                f"granule {path.name} listed in the acquisition manifest is "
                "absent from disk (D9)"
            )
        actual_checksum = checksum_file(path)
        if actual_checksum != acquisition.granule_checksums[path.name]:
            raise ExtractionPostConditionError(
                f"granule {path.name} checksum {actual_checksum} != the "
                f"acquisition manifest's recorded "
                f"{acquisition.granule_checksums[path.name]} (D9) — the file "
                "was modified after T1 acquired it"
            )
        ds, contract = read_granule(path)
        if frozen_contract is None:
            frozen_contract = contract
            # D1/D9 — the WHOLE contract must be the one T1 recorded; anything
            # else means the bytes drifted between acquisition and read.
            if _sha256_of(contract.as_manifest_dict()) != _sha256_of(
                acquisition.read_contract
            ):
                raise ExtractionPostConditionError(
                    "the first read granule's D1 read contract differs from "
                    "the one T1 recorded in the acquisition manifest (D1/D9)"
                )
        else:
            assert_contract_consistent(contract, frozen=frozen_contract)
        granule_start = (
            ds["valid_time"]
            .values[0]
            .astype("datetime64[s]")
            .item()
            .replace(tzinfo=UTC)
        )
        for station, coord in stations.by_station.items():
            nearest = extract_nearest_series(ds, coord, variable="precipitation")
            bilinear = extract_bilinear_series(ds, coord)
            half_hourly_nearest[station][granule_start] = float(nearest.values[0])
            half_hourly_bilinear[station][granule_start] = float(bilinear.values[0])
            if station not in grid_cell_by_station:
                grid_cell_by_station[station] = (nearest.grid_lat, nearest.grid_lon)

    if frozen_contract is None:
        raise ExtractionInputAbsentError("no granule was successfully read")

    axis = hourly_axis()
    nearest_hourly: dict[Station, ExtractedSeries] = {}
    bilinear_hourly: dict[Station, ExtractedSeries] = {}
    counts_by_station: dict[Station, np.ndarray] = {}
    non_finite_by_station: dict[Station, np.ndarray] = {}
    station_accounting: dict[str, dict[str, object]] = {}
    for station in stations.stations:
        grid_lat, grid_lon = grid_cell_by_station[station]
        for operator, half_hourly, sink in (
            (ExtractionOperator.NEAREST, half_hourly_nearest, nearest_hourly),
            (ExtractionOperator.BILINEAR, half_hourly_bilinear, bilinear_hourly),
        ):
            valid_time, values, counts, non_finite = aggregate_half_hourly_to_hourly(
                half_hourly[station], hours=axis
            )
            n_finite = int(np.isfinite(values).sum())
            sink[station] = ExtractedSeries(
                station=station,
                operator=operator,
                valid_time=valid_time,
                values=values,
                grid_lat=grid_lat,
                grid_lon=grid_lon,
                grid_i=0,
                grid_j=0,
                n_finite=n_finite,
                n_nan=int(values.size - n_finite),
            )
            if operator is ExtractionOperator.NEAREST:
                counts_by_station[station] = counts
                non_finite_by_station[station] = non_finite
                station_accounting[str(station)] = _station_accounting_row(
                    granule_count=counts, non_finite_cell_count=non_finite
                )

    sensitivity = build_operator_sensitivity_table(
        nearest_hourly, bilinear_hourly, params=DEFAULT_PARAMS
    )

    root = imerg_points_root(data_root)
    staged_dir = prepare_staging_dir(root)
    try:
        nearest_frame = pl.concat(
            [
                pl.DataFrame(
                    {
                        "station_id": [str(station)] * series.values.size,
                        "timestamp_utc": [str(t) for t in series.valid_time],
                        "precip_mm_per_h": pl.Series(
                            values=[
                                None if not np.isfinite(v) else float(v)
                                for v in series.values
                            ],
                            dtype=pl.Float64,
                        ),
                        "granule_count": counts_by_station[station],
                        "non_finite_cell_count": non_finite_by_station[station],
                        "grid_lat": [series.grid_lat] * series.values.size,
                        "grid_lon": [series.grid_lon] * series.values.size,
                        "station_elev_m": [stations.by_station[station].elev_m]
                        * series.values.size,
                        "station_elevation_datum": [str(VerticalDatum.UNKNOWN)]
                        * series.values.size,
                    }
                )
                for station, series in nearest_hourly.items()
            ]
        )
        nearest_frame.write_csv(staged_dir / _NEAREST_SERIES_FILENAME)

        # D6 — the selected cell's CENTRE and the station's own elevation with
        # its datum. ⛔ No DEM, no mismatch table, never grid indices.
        pl.DataFrame(
            [
                {
                    "station": str(station),
                    "lat": coord.lat,
                    "lon": coord.lon,
                    "grid_lat": grid_cell_by_station[station][0],
                    "grid_lon": grid_cell_by_station[station][1],
                    "station_elev_m": coord.elev_m,
                    "station_elevation_datum": str(VerticalDatum.UNKNOWN),
                }
                for station, coord in stations.by_station.items()
            ]
        ).write_csv(staged_dir / _STATION_CELL_FILENAME)
        sensitivity.write_csv(staged_dir / _SENSITIVITY_FILENAME)

        manifest = ImergExtractionManifest(
            extraction_identity="",
            operator_id=str(ExtractionOperator.NEAREST),
            coordinate_table_sha256=coordinate_table_sha256,
            acquisition_record_sha256=acquisition_record_digest(acquisition),
            read_contract_sha256=_sha256_of(frozen_contract.as_manifest_dict()),
            route=acquisition.route,
            collection_short_name=acquisition.collection_short_name,
            granule_revision=frozen_contract.granule_revision,
            acquisition_window_start=acquisition.requested_window_start,
            acquisition_window_end=acquisition.requested_window_end,
            granules_requested=acquisition.requested,
            granules_retrieved=acquisition.retrieved,
            granules_missing=acquisition.missing,
            box=acquisition.box,
            sensitivity_params=_sensitivity_params(DEFAULT_PARAMS),
            acquisition_generated_at=acquisition.generated_at,
            output_axis_start=axis[0],
            output_axis_end=axis[-1],
            timestamp_convention=AccumulationConvention.PERIOD_ENDING,
            period_ending_convention=EXPECTED_PERIOD_ENDING_CONVENTION,
            retrospective=True,
            measured_acquisition_latency=MEASURED_ACQUISITION_LATENCY,
            payload_sha256s={
                name: checksum_file(staged_dir / name) for name in IMERG_PAYLOAD_FILES
            },
            station_accounting=station_accounting,
            n_stations=len(stations.stations),
            n_hours=len(axis),
            generated_at=resolved_clock(),
        )
        identity = imerg_extraction_identity(manifest)
        manifest = manifest.model_copy(update={"extraction_identity": identity})
        _write_manifest(manifest, staged_dir / MANIFEST_FILENAME)
        final_dir = publish_imerg_bundle(
            staged_dir, data_root=data_root, identity=identity
        )
    finally:
        shutil.rmtree(staged_dir, ignore_errors=True)

    if args.out is not None:
        _copy_bundle_to_out(final_dir, args.out)
    log.info("imerg_extract.cli.published", extraction_identity=identity)
    return 0


def _copy_bundle_to_out(final_dir: Path, out: Path) -> None:
    """⛔ A NEW or EMPTY directory. An earlier version passed any existing
    caller path to `shutil.rmtree`, so a mistyped `--out` deleted it."""
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise Era5StorageError(
            f"--out {out} already exists and is not an empty directory — "
            "refusing to erase it; pass a new or empty destination"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(final_dir, out, dirs_exist_ok=True)


def main(argv: list[str] | None = None, **kwargs: object) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_cli_logging()
    try:
        return run(args, **kwargs)  # type: ignore[arg-type]
    except (
        DhmPrecipLoaderError,
        Era5ExtractionError,
        Era5StorageError,
        ImergAcquisitionError,
        OSError,
    ) as exc:
        log.error(
            "imerg_extract.cli.failed", error=str(exc), error_type=type(exc).__name__
        )
        return _exit_code_for(exc)


if __name__ == "__main__":
    raise SystemExit(main())
