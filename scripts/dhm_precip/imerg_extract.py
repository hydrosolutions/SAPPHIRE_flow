"""Plan 211 (M-A5b) task T2 — hourly aggregation, point extraction, and the
D9 extraction record for IMERG Early.

D3 — aggregation is the MEAN of the two half-hourly rates (mm/h). D4 —
absent hours are NaN on a complete axis, never omitted, never synthesised;
`granule_count` (0/1/2) is a companion column. D5 — period-ending: hour `t`
is built from the two granules whose `E` falls in `(t-1, t]`. D6 — no
elevation-mismatch table; only the selected cell's centre lat/lon and the
station's own elevation (vertical datum `UNKNOWN`) are recorded. D2 — the
NEAREST cell is the primary operator; BILINEAR is D1a's sensitivity
comparand only, reusing `era5_extract.py`'s own implementation verbatim
(nothing about IMERG's grid geometry differs from ERA5-Land's once a
granule is reshaped into the same `(valid_time, latitude, longitude)`
xarray layout `era5_extract`'s functions already expect).

D9 — IMERG gets its OWN root, reader and manifest type (never
`era5_extract_manifest.points_root()`, which hardcodes `era5_land/points`
and requires ERA5-specific accumulation/orography records this bundle does
not have). Discovery reuses the SAME convention (P2/P6: the highest `NNNN`
whose manifest validates), applied by ONE validation predicate shared by
publication and discovery, never two.
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
from dataclasses import dataclass
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
    StationCoordinateTable,
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
    ImergReadContract,
    acquisition_manifest_path,
    assert_acquisition_manifest_complete,
    assert_contract_consistent,
    parse_granule_filename,
    read_acquisition_manifest,
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

# D8 — determinism: round every aggregated hourly value to 9 decimal places,
# matching `ma7_profiles._HOURLY_MEAN_ROUNDING_DECIMALS` exactly.
_HOURLY_MEAN_ROUNDING_DECIMALS = 9

FIRST_HOUR: datetime = datetime(STUDY_YEARS[0], 1, 1, 0, 0, tzinfo=UTC)
LAST_HOUR: datetime = datetime(STUDY_YEARS[-1], 12, 31, 23, 0, tzinfo=UTC)
EXPECTED_HOUR_COUNT = 52_608
"""6 study years * 8760/8784 hours: 2*8784 + 4*8760 = 52,608, the same
hour axis `all_granule_starts()` pairs into (half of `EXPECTED_GRANULE_COUNT`)."""

IMERG_OUTPUT_SCHEMA_VERSION = "1"
IMERG_EXTRACTION_CODE_VERSION = "1"

_NEAREST_SERIES_FILENAME = "series_nearest.csv"
_STATION_CELL_FILENAME = "station_cell_elevation.csv"
_SENSITIVITY_FILENAME = "operator_sensitivity.csv"
IMERG_PAYLOAD_FILES: tuple[str, ...] = (
    _NEAREST_SERIES_FILENAME,
    _STATION_CELL_FILENAME,
    _SENSITIVITY_FILENAME,
)

# Ordered subclass-first (mirrors extract_era5_t2m.py's own `_EXIT_BY_ERROR`
# table): every `Era5ExtractionError` not covered by the two narrower
# entries above it (e.g. `StationOutsideGridError`, `NonFiniteExtractionError`,
# `StationSetMismatchError`, `SourceChecksumMismatchError` — all raised by the
# reused `load_expected_station_coordinates`/`extract_nearest_series`/
# `extract_bilinear_series`) is an extraction post-condition failure. Every
# `ImergAcquisitionError` (D1 read-contract/revision-mismatch violations
# surfaced by `read_granule` at T2's own read boundary, plus the credentials/
# transient/storage leaves) gets T1's own `main()` exit code, so a D1
# violation caught mid-extraction is reported the same way it would be at
# acquisition time rather than crashing with a raw traceback.
_EXIT_BY_ERROR: tuple[tuple[type[Exception], int], ...] = (
    (DhmPrecipLoaderError, 2),
    (ExtractionInputAbsentError, 2),
    (ExtractionPostConditionError, 4),
    (Era5StorageError, 5),
    (Era5ExtractionError, 4),
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
    """The same workbook-derived usable-station inventory (2d's boundary
    decision) every other extraction CLI reads — replicated (not imported,
    it is module-private in `extract_era5.py`), reading the column
    inventory only, never a gauge value (D4's ERA5 analogue, unaffected
    here)."""
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


def hourly_axis(
    *, first_hour: datetime = FIRST_HOUR, last_hour: datetime = LAST_HOUR
) -> tuple[datetime, ...]:
    hours: list[datetime] = []
    current = first_hour
    step = timedelta(hours=1)
    while current <= last_hour:
        hours.append(current)
        current += step
    return tuple(hours)


def aggregate_half_hourly_to_hourly(
    half_hourly: Mapping[datetime, float],
    *,
    first_hour: datetime = FIRST_HOUR,
    last_hour: datetime = LAST_HOUR,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """D3/D4/D5 — one row per hour of a COMPLETE axis. `precip_mm_per_h[i]`
    is the mean of the two half-hourly rates whose `E` falls in
    `(hour-1, hour]`, i.e. the granules starting at `hour - 1h` and
    `hour - 30min` (D5).

    `granule_count[i]` (0/1/2) counts how many of the two granules EXIST for
    this hour — a key present in `half_hourly`, regardless of whether the
    value at this station's cell is finite. `non_finite_cell_count[i]`
    (0/1/2) counts, of the EXISTING granules, how many carried a non-finite
    (fill-value) reading at this station's cell — D4's "a granule that
    exists but whose station cell is non-finite is likewise NaN, counted
    SEPARATELY [from a missing granule]". `precip_mm_per_h[i]` is non-NaN
    IFF `granule_count[i] == 2 and non_finite_cell_count[i] == 0` — never
    synthesised from one granule, and never averaged over a non-finite
    reading. Returns
    `(valid_time, precip_mm_per_h, granule_count, non_finite_cell_count)`."""
    hours = hourly_axis(first_hour=first_hour, last_hour=last_hour)
    values = np.full(len(hours), np.nan, dtype=np.float64)
    counts = np.zeros(len(hours), dtype=np.int64)
    non_finite_counts = np.zeros(len(hours), dtype=np.int64)
    for i, hour in enumerate(hours):
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
        [np.datetime64(h.replace(tzinfo=None)) for h in hours], dtype="datetime64[s]"
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
    """D4's accounting, derived in ONE place — the same function `run()`
    writes with and `validate_imerg_bundle` RE-DERIVES from the published
    `series_nearest.csv`, so swapped or stale totals cannot validate (four
    numbers summing to the hour count accepts any permutation of them).

    The four `n_hours_{complete,partial,missing_granule,non_finite_cell}`
    buckets partition the axis; `n_hours_any_non_finite_cell` and
    `n_granules_non_finite_cell` are NON-EXCLUSIVE totals covering every
    hour/granule with a non-finite cell, including the one-granule hours the
    exclusive bucket cannot reach and which would otherwise vanish."""
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


# --- D6 — the cell/elevation record (no DEM, no mismatch table) ---


@dataclass(frozen=True, kw_only=True, slots=True)
class StationCellRow:
    station: Station
    lat: float
    lon: float
    grid_lat: float
    grid_lon: float
    station_elev_m: float
    station_elevation_datum: VerticalDatum


def build_station_cell_table(
    stations: StationCoordinateTable,
    *,
    nearest_by_station: Mapping[Station, tuple[float, float]],
) -> tuple[StationCellRow, ...]:
    """`nearest_by_station` maps each station to its resolved
    `(grid_lat, grid_lon)` cell centre — the SAME cell for every granule
    (a fixed global grid), captured once."""
    rows: list[StationCellRow] = []
    for station, coord in stations.by_station.items():
        grid_lat, grid_lon = nearest_by_station[station]
        rows.append(
            StationCellRow(
                station=station,
                lat=coord.lat,
                lon=coord.lon,
                grid_lat=grid_lat,
                grid_lon=grid_lon,
                station_elev_m=coord.elev_m,
                station_elevation_datum=VerticalDatum.UNKNOWN,
            )
        )
    return tuple(rows)


# --- per-granule reading, reusing era5_extract's operators verbatim ---


def read_granule(path: Path) -> tuple[xr.Dataset, ImergReadContract]:
    """Reshape one IMERG granule into the `(valid_time, latitude,
    longitude)` xarray layout `era5_extract.extract_nearest_series`/
    `extract_bilinear_series` already implement — no IMERG-specific
    extraction logic is written here (D2). Returns the dataset alongside
    its observed D1 read contract (one HDF5 open, not two)."""
    import h5py
    import xarray as xr

    from scripts.dhm_precip.imerg_acquire import (
        HDF5_VARIABLE_PATH,
        assert_revision_matches_plan_and_header,
        exact_coordinate_vector,
        parse_grid_header_field,
        read_file_header,
    )

    granule, revision = parse_granule_filename(path.name)
    with h5py.File(path, "r") as f:
        file_header = read_file_header(f)
        file_header_product_version = parse_grid_header_field(
            file_header, "ProductVersion"
        )
        assert_revision_matches_plan_and_header(
            filename_revision=revision,
            file_header_filename=parse_grid_header_field(file_header, "FileName"),
        )
        grid = f["Grid"]
        precip_ds = grid["precipitation"]
        dim_names_raw = precip_ds.attrs["DimensionNames"]
        dim_names = tuple(
            (
                dim_names_raw.decode()
                if isinstance(dim_names_raw, bytes)
                else str(dim_names_raw)
            ).split(",")
        )
        units_raw = precip_ds.attrs["units"]
        units = units_raw.decode() if isinstance(units_raw, bytes) else str(units_raw)
        fill_value = float(precip_ds.attrs["_FillValue"])
        grid_header_raw = grid.attrs["GridHeader"]
        grid_header = (
            grid_header_raw.decode()
            if isinstance(grid_header_raw, bytes)
            else str(grid_header_raw)
        )
        registration = parse_grid_header_field(grid_header, "Registration")
        lat = np.asarray(grid["lat"][:], dtype=np.float64)
        lon = np.asarray(grid["lon"][:], dtype=np.float64)
        precip = np.asarray(precip_ds[:], dtype=np.float64)  # (time, lon, lat)
    longitude_convention = "SIGNED_180" if float(lon.min()) < 0.0 else "UNSIGNED_360"
    spacing = round(float(np.median(np.diff(np.sort(lat)))), 6)
    contract = ImergReadContract(
        hdf5_variable_path=HDF5_VARIABLE_PATH,
        dimension_names=dim_names,
        coordinate_registration=registration,
        longitude_convention=longitude_convention,
        units=units,
        fill_value=fill_value,
        grid_shape=tuple(int(n) for n in precip.shape),  # type: ignore[arg-type]
        lat_vector=exact_coordinate_vector(lat),
        lon_vector=exact_coordinate_vector(lon),
        grid_spacing_deg=spacing,
        granule_revision=revision,
        file_header_product_version=file_header_product_version,
    )
    precip = precip[0]  # (lon, lat)
    precip = np.where(np.isclose(precip, fill_value, atol=1e-3), np.nan, precip)
    precip = precip.T  # -> (lat, lon)
    valid_time = np.array([np.datetime64(granule.start.replace(tzinfo=None))])
    ds = xr.Dataset(
        {
            "precipitation": (
                ("valid_time", "latitude", "longitude"),
                precip[np.newaxis, :, :],
            )
        },
        coords={"valid_time": valid_time, "latitude": lat, "longitude": lon},
    )
    return ds, contract


# --- D9 — storage layout, identity, manifest, publisher, discovery ---

_RUN_NUMBER_WIDTH = 4
_MAX_RUN_NUMBER = 10**_RUN_NUMBER_WIDTH - 1


def imerg_points_root(data_root: Path) -> Path:
    """D9 — IMERG's OWN root, never `era5_extract_manifest.points_root()`
    (hardcoded to `era5_land/points`)."""
    return data_root / "imerg_early" / "points"


def _staging_dir(root: Path, *, identity: str, token: str) -> Path:
    return root / ".staging" / f"{identity}--{token}"


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
            if name == ".staging":
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
    """P1a's identity-independent atomic reservation, reused verbatim in
    spirit from `era5_extract_manifest._reserve_run_number` — reimplemented
    here (not imported) because that function is hardwired to
    `era5_land/points` via its `data_root -> points_root` call, exactly the
    coupling D9 says IMERG must not inherit."""
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


def prepare_staging_dir(root: Path, *, identity: str) -> Path:
    token = uuid.uuid4().hex[:16]
    staging = _staging_dir(root, identity=identity, token=token)
    try:
        staging.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise Era5StorageError(
            f"failed to create staging directory {staging}: {exc}"
        ) from exc
    return staging


def manifest_filename() -> str:
    return "extraction_manifest.json"


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _json_normalised(obj: object) -> object:
    """Round-trip through JSON so a tuple compares equal to the list it
    becomes once a manifest has been written and re-read."""
    return json.loads(_canonical_json(obj))


#: D5 — the exact literal every published manifest must carry, mirroring
#: `era5_extract._EXPECTED_PERIOD_ENDING_CONVENTION`'s own shape.
EXPECTED_PERIOD_ENDING_CONVENTION = (
    "hour t covers t-1 -> t (UTC): built from the two half-hourly granules "
    "whose E falls in (t-1, t]"
)


@dataclass(frozen=True, kw_only=True, slots=True)
class ImergIdentityInputs:
    """P7a — the COMPLETE canonical snapshot `imerg_extraction_identity`
    hashes, split into `value_inputs` (everything the computation actually
    reads — including D1a's sensitivity parameter snapshot, which affects
    `operator_sensitivity.csv`'s content) and `invalidation_inputs` (version
    bumps that force regeneration on a behaviour-neutral change). Persisted
    verbatim in `ImergExtractionManifest.identity_inputs` so a reader can
    recompute the digest without having been the writer
    (`recompute_imerg_extraction_identity`) — mirroring
    `era5_extract_manifest.ExtractionIdentityInputs`."""

    operator_id: str
    coordinate_table_sha256: str
    granule_checksums: Mapping[str, str]
    route: str
    collection_short_name: str
    granule_revision: str
    requested_window_start: str
    requested_window_end: str
    box: tuple[int, int, int, int]
    read_contract: Mapping[str, object]
    jjas_months: tuple[int, ...]
    djf_months: tuple[int, ...]
    mam_months: tuple[int, ...]
    on_months: tuple[int, ...]
    wet_threshold_mm_per_h: float
    wet_threshold_side: str
    zero_policy: str
    quantile_definition: str
    quantile_grid: tuple[float, ...]
    output_schema_version: str = IMERG_OUTPUT_SCHEMA_VERSION
    extraction_code_version: str = IMERG_EXTRACTION_CODE_VERSION

    def canonical_payload(self) -> dict[str, object]:
        value_inputs: dict[str, object] = {
            "operator_id": self.operator_id,
            "coordinate_table_sha256": self.coordinate_table_sha256,
            "granule_checksums": dict(sorted(self.granule_checksums.items())),
            "route": self.route,
            "collection_short_name": self.collection_short_name,
            "granule_revision": self.granule_revision,
            "requested_window_start": self.requested_window_start,
            "requested_window_end": self.requested_window_end,
            "box": list(self.box),
            "read_contract": dict(self.read_contract),
            "seasons": {
                "jjas_months": list(self.jjas_months),
                "djf_months": list(self.djf_months),
                "mam_months": list(self.mam_months),
                "on_months": list(self.on_months),
            },
            "wet_threshold_mm_per_h": self.wet_threshold_mm_per_h,
            "wet_threshold_side": self.wet_threshold_side,
            "zero_policy": self.zero_policy,
            "quantile_definition": self.quantile_definition,
            "quantile_grid": list(self.quantile_grid),
        }
        invalidation_inputs: dict[str, object] = {
            "output_schema_version": self.output_schema_version,
            "extraction_code_version": self.extraction_code_version,
        }
        return {
            "value_inputs": value_inputs,
            "invalidation_inputs": invalidation_inputs,
        }

    def digest(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.canonical_payload()).encode()
        ).hexdigest()


def imerg_extraction_identity(inputs: ImergIdentityInputs) -> str:
    """D9/P7a — hashed over exactly what T2 READ, including the D1a
    sensitivity parameters that affect `operator_sensitivity.csv`'s
    content. Never wall-clock time, output paths or hostname (those are
    provenance, recorded but not hashed)."""
    return inputs.digest()


def recompute_imerg_extraction_identity(identity_inputs: Mapping[str, object]) -> str:
    """The recomputation half of P7a: given the canonical `{"value_inputs":
    ..., "invalidation_inputs": ...}` payload a manifest records
    (`ImergExtractionManifest.identity_inputs`), recompute the digest a
    reader can compare against `extraction_identity` WITHOUT having been
    the writer."""
    return hashlib.sha256(_canonical_json(identity_inputs).encode()).hexdigest()


_EXPECTED_VALUE_INPUT_KEYS: frozenset[str] = frozenset(
    {
        "operator_id",
        "coordinate_table_sha256",
        "granule_checksums",
        "route",
        "collection_short_name",
        "granule_revision",
        "requested_window_start",
        "requested_window_end",
        "box",
        "read_contract",
        "seasons",
        "wet_threshold_mm_per_h",
        "wet_threshold_side",
        "zero_policy",
        "quantile_definition",
        "quantile_grid",
    }
)
_EXPECTED_INVALIDATION_INPUT_KEYS: frozenset[str] = frozenset(
    {"output_schema_version", "extraction_code_version"}
)


def assert_identity_inputs_complete(identity_inputs: Mapping[str, object]) -> None:
    """A published bundle must carry the COMPLETE canonical snapshot its
    `extraction_identity` was computed from, not merely the digest string —
    mirroring `era5_extract_manifest.assert_identity_inputs_complete`."""
    if set(identity_inputs) != {"value_inputs", "invalidation_inputs"}:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's identity_inputs has top-level "
            f"key(s) {sorted(identity_inputs)}, expected exactly "
            "{'value_inputs', 'invalidation_inputs'} (D9/P7a)"
        )
    value_inputs = identity_inputs["value_inputs"]
    invalidation_inputs = identity_inputs["invalidation_inputs"]
    if not isinstance(value_inputs, dict) or not isinstance(invalidation_inputs, dict):
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's identity_inputs.value_inputs/"
            "invalidation_inputs must both be objects (D9/P7a)"
        )
    missing_value = _EXPECTED_VALUE_INPUT_KEYS - set(value_inputs)
    if missing_value:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's identity_inputs.value_inputs is "
            f"missing key(s) {sorted(missing_value)} (D9/P7a)"
        )
    missing_invalidation = _EXPECTED_INVALIDATION_INPUT_KEYS - set(invalidation_inputs)
    if missing_invalidation:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's identity_inputs.invalidation_inputs "
            f"is missing key(s) {sorted(missing_invalidation)} (D9/P7a)"
        )


class ImergExtractionManifest(BaseModel):
    """D9's `extraction_manifest.json` — the bundle IS the extraction
    record (no separate report artefact): route, collection, revision,
    window, box, granule counts/gaps, the named operator, the sensitivity
    envelope's location, the cell/elevation record's location, the word
    RETROSPECTIVE (D7) and the measured acquisition latency."""

    extraction_identity: str
    operator_id: str
    coordinate_table_sha256: str
    route: str
    collection_short_name: str
    granule_revision: str
    acquisition_window_start: datetime
    acquisition_window_end: datetime
    """The RETRIEVAL (half-hourly granule) boundary, sourced from the D9
    acquisition manifest T1 wrote — NOT the output axis (D5's finding: the
    two must never be conflated)."""
    granules_requested: int
    granules_retrieved: int
    granules_missing: tuple[str, ...] = ()
    """D9's 'granule counts and gaps', copied from the acquisition manifest
    and reconciled against it: `granules_retrieved + len(granules_missing)
    == granules_requested`, and `granules_retrieved` must equal the number
    of granule checksums the identity was hashed over."""
    acquisition_generated_at: datetime
    """Provenance link to the PERMANENT acquisition record (D10) that
    carries the per-granule checksums and retrieval timestamps. ⛔ Those
    105,216-entry maps are deliberately NOT copied here: duplicating them
    creates two records that can disagree, and the acquisition manifest is
    the one the plan makes permanent."""
    output_axis_start: datetime
    output_axis_end: datetime
    """The hourly OUTPUT axis boundary (D5) — the axis's first hour needs
    granules starting one hour before `acquisition_window_start`, so these
    are deliberately distinct fields from the acquisition window above."""
    timestamp_convention: AccumulationConvention
    period_ending_convention: str
    box: tuple[int, int, int, int]
    read_contract: dict[str, object]
    retrospective: bool
    measured_acquisition_latency: str
    payload_sha256s: dict[str, str] = {}
    station_accounting: dict[str, dict[str, object]] = {}
    identity_inputs: dict[str, object] = {}
    n_stations: int
    n_hours: int
    generated_at: datetime


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
#: D9/P7a — the top-level manifest fields that DUPLICATE a hashed
#: `identity_inputs.value_inputs` entry, and must therefore be reconciled
#: against it. ⛔ Otherwise a manifest's human-readable provenance can
#: contradict the very identity it is published under and still validate.
_IDENTITY_MIRRORED_FIELDS: tuple[tuple[str, str], ...] = (
    ("operator_id", "operator_id"),
    ("coordinate_table_sha256", "coordinate_table_sha256"),
    ("route", "route"),
    ("collection_short_name", "collection_short_name"),
    ("granule_revision", "granule_revision"),
    ("box", "box"),
    ("read_contract", "read_contract"),
    ("acquisition_window_start", "requested_window_start"),
    ("acquisition_window_end", "requested_window_end"),
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
    """EXACT match against one of the two forms a bundle directory takes —
    staged (`<identity>--<token>`, P1a) or published (`NNNN-<identity>`,
    P1). A substring match (the previous check) would wrongly accept a
    directory whose name merely CONTAINS the identity as a fragment of
    something else."""
    name = directory.name
    if "--" in name:
        declared, _, _token = name.partition("--")
        return declared == identity
    prefix, sep, rest = name.partition("-")
    return (
        bool(sep)
        and bool(_PUBLISHED_RUN_NUMBER_RE.fullmatch(prefix))
        and rest == identity
    )


#: The primary series' numeric columns are read under an EXPLICIT schema
#: rather than by inference: a non-numeric cell then fails the read (and is
#: reported as a validation failure), and an all-null column still arrives
#: typed instead of as `String`.
_NEAREST_NUMERIC_SCHEMA: dict[str, pl.DataType] = {
    "precip_mm_per_h": pl.Float64(),
    "granule_count": pl.Int64(),
    "non_finite_cell_count": pl.Int64(),
}


def _read_csv_or_reject(
    path: Path, *, schema_overrides: dict[str, pl.DataType] | None = None
) -> pl.DataFrame:
    """A malformed or unreadable payload is a VALIDATION failure, not a
    crash: D9 requires discovery to SKIP an invalid higher-numbered bundle
    and fall back to the next lower valid one, which it cannot do if polars
    raises its own exception type straight through."""
    try:
        return pl.read_csv(path, schema_overrides=schema_overrides)
    except (pl.exceptions.PolarsError, OSError, ValueError) as exc:
        raise ExtractionPostConditionError(
            f"{path.name} in {path.parent} is not a readable CSV: {exc} (D9)"
        ) from exc


def validate_imerg_bundle(
    directory: Path,
    manifest: ImergExtractionManifest,
    *,
    expected_station_count: int,
    expected_hour_count: int,
) -> None:
    """D9 — ONE validation predicate, applied identically by publication
    and discovery (never two implementations of the same rules — the
    ERA5-Land precedent's own P4 finding)."""
    if not _directory_declares_identity(
        directory, identity=manifest.extraction_identity
    ):
        raise ExtractionPostConditionError(
            f"manifest extraction_identity {manifest.extraction_identity!r} "
            f"does not exactly match its own directory name {directory.name!r} "
            "(D9)"
        )
    if manifest.retrospective is not True:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest must be marked RETROSPECTIVE (D7)"
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
    if len(stations_present) != expected_station_count:
        raise ExtractionPostConditionError(
            f"{_NEAREST_SERIES_FILENAME} has {len(stations_present)} distinct "
            f"stations, expected {expected_station_count} (D9)"
        )
    # D9/D5 — the EXACT expected hourly axis, per station, not merely a row
    # count: a duplicate timestamp and a missing one can share a count.
    expected_axis = [
        str(np.datetime64(h.replace(tzinfo=None), "s"))
        for h in hourly_axis()[:expected_hour_count]
    ]
    for _station_id, group in nearest.sort("timestamp_utc").group_by(
        "station_id", maintain_order=True
    ):
        actual_axis = group.sort("timestamp_utc")["timestamp_utc"].to_list()
        if actual_axis != expected_axis:
            raise ExtractionPostConditionError(
                f"{_NEAREST_SERIES_FILENAME} station {_station_id!r} does not "
                "carry the exact expected hourly axis (D5/D9)"
            )
    # ⛔ Nullness first: `~col.is_in([0,1,2])` evaluates to NULL on a null
    # cell, which `filter` drops — so a null count would pass every range
    # check below without this gate.
    for count_column in ("granule_count", "non_finite_cell_count"):
        if nearest[count_column].null_count():
            raise ExtractionPostConditionError(
                f"{_NEAREST_SERIES_FILENAME}.{count_column} has "
                f"{nearest[count_column].null_count()} null value(s) — a count "
                "is never absent (D4/D9)"
            )
    # 0 <= non_finite_cell_count <= granule_count <= 2 — a granule cannot be
    # non-finite without existing, so the counts are ordered, not merely
    # each in {0, 1, 2}.
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
    # non_finite_cell_count == 0. Both directions checked, and finiteness
    # rather than nullness: a NaN or +/-inf on a nominally complete hour is
    # not a valid rate, and a value present under a partial/non-finite hour
    # would be invented data.
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
    if station_cell.height != expected_station_count:
        raise ExtractionPostConditionError(
            f"{_STATION_CELL_FILENAME} has {station_cell.height} rows, "
            f"expected {expected_station_count} (D9)"
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

    sensitivity = _read_csv_or_reject(directory / _SENSITIVITY_FILENAME)
    missing_sensitivity_cols = set(SENSITIVITY_REQUIRED_COLUMNS) - set(
        sensitivity.columns
    )
    if missing_sensitivity_cols:
        raise ExtractionPostConditionError(
            f"{_SENSITIVITY_FILENAME} is missing column(s) "
            f"{sorted(missing_sensitivity_cols)} (D1a/D9)"
        )
    bad_scope = sensitivity.filter(
        ~pl.col("scope").is_in(list(_VALID_SENSITIVITY_SCOPES))
    )
    if bad_scope.height:
        raise ExtractionPostConditionError(
            f"{_SENSITIVITY_FILENAME}.scope has value(s) outside "
            f"{sorted(_VALID_SENSITIVITY_SCOPES)} (D1a/D9)"
        )
    bad_statistic = sensitivity.filter(
        ~pl.col("statistic").is_in(list(_VALID_SENSITIVITY_STATISTICS))
    )
    if bad_statistic.height:
        raise ExtractionPostConditionError(
            f"{_SENSITIVITY_FILENAME}.statistic has value(s) outside "
            f"{sorted(_VALID_SENSITIVITY_STATISTICS)} (D1a/D9)"
        )
    bad_delta_unit = sensitivity.filter(
        pl.col("delta_unit").is_not_null()
        & ~pl.col("delta_unit").is_in(list(_VALID_SENSITIVITY_DELTA_UNITS))
    )
    if bad_delta_unit.height:
        raise ExtractionPostConditionError(
            f"{_SENSITIVITY_FILENAME}.delta_unit has value(s) outside "
            f"{sorted(_VALID_SENSITIVITY_DELTA_UNITS)} (D1a/D9)"
        )

    if not manifest.station_accounting:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's station_accounting is empty (D4/D9)"
        )
    if set(manifest.station_accounting) != set(str(s) for s in stations_present):
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's station_accounting station set does "
            "not match the published series' station set (D9)"
        )
    # ⛔ RE-DERIVED from the published series, not merely summed: four
    # caller-supplied numbers that add up to the hour count can be swapped,
    # stale or invented and still pass a sum check.
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
        differing = {
            key: (recorded[key], expected_accounting[key])
            for key in _STATION_ACCOUNTING_KEYS
            if int(recorded[key]) != int(expected_accounting[key])  # type: ignore[arg-type,call-overload]
        }
        if differing:
            raise ExtractionPostConditionError(
                f"IMERG extraction manifest's station_accounting[{station_id!r}] "
                f"disagrees with the published series on {sorted(differing)} "
                f"(recorded vs re-derived: {differing}) (D4/D9)"
            )
        if int(expected_accounting["n_hours"]) != expected_hour_count:  # type: ignore[arg-type]
            raise ExtractionPostConditionError(
                f"station {station_id!r} carries "
                f"{expected_accounting['n_hours']} hours, expected "
                f"{expected_hour_count} (D4/D9)"
            )
    if manifest.n_stations != expected_station_count:
        raise ExtractionPostConditionError(
            f"IMERG extraction manifest's n_stations {manifest.n_stations} != "
            f"expected_station_count {expected_station_count} (D9)"
        )
    if manifest.n_hours != expected_hour_count:
        raise ExtractionPostConditionError(
            f"IMERG extraction manifest's n_hours {manifest.n_hours} != "
            f"expected_hour_count {expected_hour_count} (D9)"
        )

    assert_identity_inputs_complete(manifest.identity_inputs)
    recomputed = recompute_imerg_extraction_identity(manifest.identity_inputs)
    if recomputed != manifest.extraction_identity:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's extraction_identity does not match "
            "the digest recomputed from its own identity_inputs (D9/P7a)"
        )
    # D9/P7a — every top-level field that MIRRORS a hashed identity input
    # must equal it exactly. ⛔ Without this, a manifest's readable
    # provenance (route, revision, box, window, read contract, …) can be
    # edited to say anything at all while the digest still recomputes,
    # because only `identity_inputs` feeds the hash.
    value_inputs = manifest.identity_inputs["value_inputs"]
    assert isinstance(value_inputs, dict)  # noqa: S101 - narrowed above
    for field_name, input_key in _IDENTITY_MIRRORED_FIELDS:
        top_level = getattr(manifest, field_name)
        if isinstance(top_level, datetime):
            top_level = top_level.isoformat()
        if _json_normalised(top_level) != _json_normalised(value_inputs[input_key]):
            raise ExtractionPostConditionError(
                f"IMERG extraction manifest's top-level {field_name!r} "
                f"contradicts identity_inputs.value_inputs[{input_key!r}] — "
                "provenance must never disagree with the identity it is "
                "published under (D9/P7a)"
            )
    # The output axis endpoints are a claim about the PAYLOAD, so they are
    # checked against the payload, not merely against each other.
    if [
        str(np.datetime64(manifest.output_axis_start.replace(tzinfo=None), "s")),
        str(np.datetime64(manifest.output_axis_end.replace(tzinfo=None), "s")),
    ] != [expected_axis[0], expected_axis[-1]]:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's output_axis_start/end do not match "
            "the published series' own first/last timestamps (D5/D9)"
        )
    # D9 — the acquisition accounting the bundle carries must add up, and
    # must agree with the granule set the identity was hashed over.
    if manifest.granules_retrieved + len(manifest.granules_missing) != (
        manifest.granules_requested
    ):
        raise ExtractionPostConditionError(
            f"IMERG extraction manifest's granules_retrieved "
            f"{manifest.granules_retrieved} + granules_missing "
            f"{len(manifest.granules_missing)} != granules_requested "
            f"{manifest.granules_requested} (D9)"
        )
    hashed_checksums = value_inputs["granule_checksums"]
    assert isinstance(hashed_checksums, dict)  # noqa: S101 - canonical payload shape
    if manifest.granules_retrieved != len(hashed_checksums):
        raise ExtractionPostConditionError(
            f"IMERG extraction manifest's granules_retrieved "
            f"{manifest.granules_retrieved} != the {len(hashed_checksums)} "
            "granule checksums its identity was hashed over (D9)"
        )


def publish_imerg_bundle(
    staged_dir: Path,
    *,
    data_root: Path,
    identity: str,
    expected_station_count: int,
    expected_hour_count: int,
) -> Path:
    manifest = _read_manifest(staged_dir / manifest_filename())
    if manifest is None:
        raise ExtractionPostConditionError(
            f"staged IMERG bundle at {staged_dir} is missing a readable "
            f"{manifest_filename()}"
        )
    if identity != manifest.extraction_identity:
        raise ExtractionPostConditionError(
            f"publish identity {identity!r} != the staged manifest's own "
            f"extraction_identity {manifest.extraction_identity!r} (D9) — "
            "refusing to publish a bundle under a label that disagrees with "
            "its own manifest"
        )
    validate_imerg_bundle(
        staged_dir,
        manifest,
        expected_station_count=expected_station_count,
        expected_hour_count=expected_hour_count,
    )
    root = imerg_points_root(data_root)
    final_dir = allocate_published_dir(root, identity=identity)
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


def discover_imerg_bundle(
    data_root: Path, *, expected_station_count: int, expected_hour_count: int
) -> tuple[Path, ImergExtractionManifest]:
    """P2/P6 — the highest `NNNN` whose manifest validates against
    `validate_imerg_bundle` (the SAME predicate publication applies)."""
    root = imerg_points_root(data_root)
    if not root.exists():
        raise ExtractionInputAbsentError(f"no IMERG extraction points root at {root}")
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name != ".staging"),
        key=lambda p: p.name,
    )
    last_error: Era5AcquisitionError | None = None
    for candidate in reversed(candidates):
        # ⛔ The manifest READ is inside the guard too: a malformed
        # `extraction_manifest.json` raises `Era5StorageError`, which would
        # otherwise abort discovery at the highest NNNN instead of falling
        # back to the next lower valid bundle (D9).
        try:
            manifest = _read_manifest(candidate / manifest_filename())
            if manifest is None:
                continue
            validate_imerg_bundle(
                candidate,
                manifest,
                expected_station_count=expected_station_count,
                expected_hour_count=expected_hour_count,
            )
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


DEFAULT_IMERG_DATA_ROOT = Path("data/dhm_precip/imerg_early")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="imerg_extract", description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data/dhm_precip"))
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Copy the published bundle's full contents (manifest.json + the "
            "three payload CSVs) here — a plain directory copy, so two runs' "
            "--out directories can be diffed directly for D8 determinism "
            "(excluding the manifest's generated_at field, which is "
            "time-bearing by construction). The bundle ALWAYS publishes to "
            "the identity-addressed directory under --data-root regardless "
            "of --out (D9) — this is a copy, never the publish location "
            "itself, and no separate report format is written."
        ),
    )
    return parser


def run(
    args: argparse.Namespace,
    *,
    clock: Callable[[], datetime] | None = None,
    coords_path: Path | None = None,
    expected_stations: frozenset[Station] | None = None,
    params: DhmPrecipParams | None = None,
    granule_paths: list[Path] | None = None,
    acquisition_manifest: ImergAcquisitionManifest | None = None,
) -> int:
    from scripts.dhm_precip.imerg_acquire import imerg_raw_dir

    data_root: Path = args.data_root
    resolved_params = params if params is not None else DEFAULT_PARAMS
    resolved_clock: Callable[[], datetime] = (
        clock if clock is not None else (lambda: datetime.now(UTC))
    )

    resolved_coords_path = (
        coords_path if coords_path is not None else resolve_coords_path()
    )
    resolved_expected_stations = (
        expected_stations
        if expected_stations is not None
        else _default_expected_stations()
    )
    stations = load_expected_station_coordinates(
        resolved_coords_path, expected_stations=resolved_expected_stations
    )
    coordinate_table_sha256 = checksum_file(resolved_coords_path)

    # D9 — the T1->T2 handoff IS the acquisition manifest. T2 must not
    # re-derive the granule set, the revision or the route by re-listing
    # the raw directory: the manifest is the single source of truth for
    # what was actually retrieved.
    resolved_acquisition_manifest = (
        acquisition_manifest
        if acquisition_manifest is not None
        else read_acquisition_manifest(acquisition_manifest_path(data_root))
    )
    if resolved_acquisition_manifest is None:
        raise ExtractionInputAbsentError(
            f"no IMERG acquisition manifest at "
            f"{acquisition_manifest_path(data_root)} — T1 must run first (D9)"
        )
    # ⛔ COMPLETE is DERIVED from the manifest's own contents, never trusted
    # from its `completeness` label — and derived BEFORE any granule file is
    # opened. A value published under an identity supplied alongside it
    # rather than derived from it is this track's identity defect class.
    assert_acquisition_manifest_complete(resolved_acquisition_manifest)

    resolved_granule_paths = (
        granule_paths
        if granule_paths is not None
        else [
            imerg_raw_dir(data_root) / name
            for name in sorted(resolved_acquisition_manifest.granule_checksums)
        ]
    )
    if not resolved_granule_paths:
        raise ExtractionInputAbsentError(
            "the IMERG acquisition manifest records no retrieved granules (D9)"
        )

    half_hourly_nearest: dict[Station, dict[datetime, float]] = {
        s: {} for s in stations.stations
    }
    half_hourly_bilinear: dict[Station, dict[datetime, float]] = {
        s: {} for s in stations.stations
    }
    grid_cell_by_station: dict[Station, tuple[float, float]] = {}
    frozen_contract: ImergReadContract | None = None
    granule_checksums: dict[str, str] = {}

    for path in resolved_granule_paths:
        if not path.exists():
            raise ExtractionInputAbsentError(
                f"granule {path.name} listed in the acquisition manifest is "
                "absent from disk (D9)"
            )
        expected_checksum = resolved_acquisition_manifest.granule_checksums.get(
            path.name
        )
        if expected_checksum is None:
            raise ExtractionPostConditionError(
                f"granule {path.name} on disk is not recorded in the "
                "acquisition manifest's granule_checksums (D9) — refusing to "
                "extract an unaccounted-for file"
            )
        actual_checksum = checksum_file(path)
        if actual_checksum != expected_checksum:
            raise ExtractionPostConditionError(
                f"granule {path.name} checksum {actual_checksum} != the "
                f"acquisition manifest's recorded {expected_checksum} (D9) — "
                "the file was modified after T1 acquired it"
            )
        ds, contract = read_granule(path)
        if frozen_contract is None:
            frozen_contract = contract
            # D1/D9 — the WHOLE contract (revision included) must be the one
            # T1 recorded, not merely a matching revision letter: anything
            # else means the bytes drifted between acquisition and read.
            if _json_normalised(contract.as_manifest_dict()) != _json_normalised(
                resolved_acquisition_manifest.read_contract
            ):
                raise ExtractionPostConditionError(
                    "the first read granule's D1 read contract differs from "
                    "the one T1 recorded in the acquisition manifest (D1/D9)"
                )
        else:
            assert_contract_consistent(contract, frozen=frozen_contract)
        granule_checksums[path.name] = actual_checksum
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

    nearest_hourly: dict[Station, ExtractedSeries] = {}
    bilinear_hourly: dict[Station, ExtractedSeries] = {}
    granule_counts_by_station: dict[Station, np.ndarray] = {}
    non_finite_counts_by_station: dict[Station, np.ndarray] = {}
    station_accounting: dict[str, dict[str, object]] = {}
    for station in stations.stations:
        grid_lat, grid_lon = grid_cell_by_station[station]
        valid_time, values, counts, non_finite_counts = aggregate_half_hourly_to_hourly(
            half_hourly_nearest[station]
        )
        n_finite = int(np.isfinite(values).sum())
        nearest_hourly[station] = ExtractedSeries(
            station=station,
            operator=ExtractionOperator.NEAREST,
            valid_time=valid_time,
            values=values,
            grid_lat=grid_lat,
            grid_lon=grid_lon,
            grid_i=0,
            grid_j=0,
            n_finite=n_finite,
            n_nan=int(values.size - n_finite),
        )
        granule_counts_by_station[station] = counts
        non_finite_counts_by_station[station] = non_finite_counts

        b_valid_time, b_values, _b_counts, _b_nf_counts = (
            aggregate_half_hourly_to_hourly(half_hourly_bilinear[station])
        )
        b_n_finite = int(np.isfinite(b_values).sum())
        bilinear_hourly[station] = ExtractedSeries(
            station=station,
            operator=ExtractionOperator.BILINEAR,
            valid_time=b_valid_time,
            values=b_values,
            grid_lat=grid_lat,
            grid_lon=grid_lon,
            grid_i=0,
            grid_j=0,
            n_finite=b_n_finite,
            n_nan=int(b_values.size - b_n_finite),
        )

        station_accounting[str(station)] = _station_accounting_row(
            granule_count=counts, non_finite_cell_count=non_finite_counts
        )

    station_cell_rows = build_station_cell_table(
        stations, nearest_by_station=grid_cell_by_station
    )
    station_cell_by_station = {row.station: row for row in station_cell_rows}
    sensitivity = build_operator_sensitivity_table(
        nearest_hourly, bilinear_hourly, params=resolved_params
    )

    first_station = stations.stations[0]
    output_axis_start = (
        nearest_hourly[first_station]
        .valid_time[0]
        .astype("datetime64[s]")
        .item()
        .replace(tzinfo=UTC)
    )
    output_axis_end = (
        nearest_hourly[first_station]
        .valid_time[-1]
        .astype("datetime64[s]")
        .item()
        .replace(tzinfo=UTC)
    )

    identity_inputs = ImergIdentityInputs(
        operator_id=str(ExtractionOperator.NEAREST),
        coordinate_table_sha256=coordinate_table_sha256,
        granule_checksums=granule_checksums,
        route=resolved_acquisition_manifest.route,
        collection_short_name=resolved_acquisition_manifest.collection_short_name,
        granule_revision=frozen_contract.granule_revision,
        requested_window_start=resolved_acquisition_manifest.requested_window_start.isoformat(),
        requested_window_end=resolved_acquisition_manifest.requested_window_end.isoformat(),
        box=resolved_acquisition_manifest.box,
        read_contract=frozen_contract.as_manifest_dict(),
        jjas_months=resolved_params.jjas_months,
        djf_months=resolved_params.djf_months,
        mam_months=resolved_params.mam_months,
        on_months=resolved_params.on_months,
        wet_threshold_mm_per_h=resolved_params.wet_threshold_mm_per_h,
        wet_threshold_side=resolved_params.wet_threshold_side,
        zero_policy=resolved_params.zero_policy,
        quantile_definition=resolved_params.quantile_definition,
        quantile_grid=resolved_params.quantile_grid,
    )
    identity = imerg_extraction_identity(identity_inputs)

    root = imerg_points_root(data_root)
    staged_dir = prepare_staging_dir(root, identity=identity)
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
                        "granule_count": granule_counts_by_station[station],
                        "non_finite_cell_count": non_finite_counts_by_station[station],
                        "grid_lat": [station_cell_by_station[station].grid_lat]
                        * series.values.size,
                        "grid_lon": [station_cell_by_station[station].grid_lon]
                        * series.values.size,
                        "station_elev_m": [
                            station_cell_by_station[station].station_elev_m
                        ]
                        * series.values.size,
                        "station_elevation_datum": [
                            str(
                                station_cell_by_station[station].station_elevation_datum
                            )
                        ]
                        * series.values.size,
                    }
                )
                for station, series in nearest_hourly.items()
            ]
        )
        nearest_frame.write_csv(staged_dir / _NEAREST_SERIES_FILENAME)

        station_cell_frame = pl.DataFrame(
            [
                {
                    "station": str(row.station),
                    "lat": row.lat,
                    "lon": row.lon,
                    "grid_lat": row.grid_lat,
                    "grid_lon": row.grid_lon,
                    "station_elev_m": row.station_elev_m,
                    "station_elevation_datum": str(row.station_elevation_datum),
                }
                for row in station_cell_rows
            ]
        )
        station_cell_frame.write_csv(staged_dir / _STATION_CELL_FILENAME)
        sensitivity.write_csv(staged_dir / _SENSITIVITY_FILENAME)

        payload_sha256s = {
            name: checksum_file(staged_dir / name) for name in IMERG_PAYLOAD_FILES
        }
        manifest = ImergExtractionManifest(
            extraction_identity=identity,
            operator_id=str(ExtractionOperator.NEAREST),
            coordinate_table_sha256=coordinate_table_sha256,
            route=resolved_acquisition_manifest.route,
            collection_short_name=resolved_acquisition_manifest.collection_short_name,
            granule_revision=frozen_contract.granule_revision,
            acquisition_window_start=resolved_acquisition_manifest.requested_window_start,
            acquisition_window_end=resolved_acquisition_manifest.requested_window_end,
            granules_requested=resolved_acquisition_manifest.requested,
            granules_retrieved=resolved_acquisition_manifest.retrieved,
            granules_missing=resolved_acquisition_manifest.missing,
            acquisition_generated_at=resolved_acquisition_manifest.generated_at,
            output_axis_start=output_axis_start,
            output_axis_end=output_axis_end,
            timestamp_convention=AccumulationConvention.PERIOD_ENDING,
            period_ending_convention=EXPECTED_PERIOD_ENDING_CONVENTION,
            box=resolved_acquisition_manifest.box,
            read_contract=frozen_contract.as_manifest_dict(),
            retrospective=True,
            measured_acquisition_latency=MEASURED_ACQUISITION_LATENCY,
            payload_sha256s=payload_sha256s,
            station_accounting=station_accounting,
            identity_inputs=identity_inputs.canonical_payload(),
            n_stations=len(stations.stations),
            n_hours=EXPECTED_HOUR_COUNT,
            generated_at=resolved_clock(),
        )
        _write_manifest(manifest, staged_dir / manifest_filename())
        final_dir = publish_imerg_bundle(
            staged_dir,
            data_root=data_root,
            identity=identity,
            expected_station_count=len(stations.stations),
            expected_hour_count=EXPECTED_HOUR_COUNT,
        )
    finally:
        shutil.rmtree(staged_dir, ignore_errors=True)

    if args.out is not None:
        if args.out.exists():
            shutil.rmtree(args.out)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(final_dir, args.out)
    log.info("imerg_extract.cli.published", extraction_identity=identity)
    return 0


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
