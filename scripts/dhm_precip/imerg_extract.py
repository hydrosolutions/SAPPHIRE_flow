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
    ExtractionOperator,
    Station,
    StationCoordinateTable,
    VerticalDatum,
)
from scripts.dhm_precip.era5_errors import (  # noqa: E402
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
    assert_payload_checksum_matches,
)
from scripts.dhm_precip.era5_manifest import checksum_file  # noqa: E402
from scripts.dhm_precip.era5_request import STUDY_YEARS  # noqa: E402
from scripts.dhm_precip.imerg_acquire import (  # noqa: E402
    COLLECTION_SHORT_NAME,
    FIRST_GRANULE_START,
    MEASURED_ACQUISITION_LATENCY,
    ROUTE,
    STUDY_BOX,
    ImergReadContract,
    assert_contract_consistent,
    parse_granule_filename,
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
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """D3/D4/D5 — one row per hour of a COMPLETE axis. `precip_mm_per_h[i]`
    is the mean of the two half-hourly rates whose `E` falls in
    `(hour-1, hour]`, i.e. the granules starting at `hour - 1h` and
    `hour - 30min` (D5). Fewer than two FINITE contributing granules ->
    NaN, never synthesised from one (D4); `granule_count[i]` is exactly how
    many of the two were present and finite. Returns
    `(valid_time, precip_mm_per_h, granule_count)`."""
    hours = hourly_axis(first_hour=first_hour, last_hour=last_hour)
    values = np.full(len(hours), np.nan, dtype=np.float64)
    counts = np.zeros(len(hours), dtype=np.int64)
    for i, hour in enumerate(hours):
        r1 = half_hourly.get(hour - timedelta(hours=1))
        r2 = half_hourly.get(hour - timedelta(minutes=30))
        finite = [r for r in (r1, r2) if r is not None and np.isfinite(r)]
        counts[i] = len(finite)
        if len(finite) == 2:  # noqa: PLR2004 - exactly D4's "two" threshold
            values[i] = round(
                (finite[0] + finite[1]) / 2.0, _HOURLY_MEAN_ROUNDING_DECIMALS
            )
    valid_time = np.array(
        [np.datetime64(h.replace(tzinfo=None)) for h in hours], dtype="datetime64[s]"
    )
    return valid_time, values, counts


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
        parse_grid_header_field,
    )

    granule, revision = parse_granule_filename(path.name)
    with h5py.File(path, "r") as f:
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
        lat_vector=tuple(round(float(v), 6) for v in lat),
        lon_vector=tuple(round(float(v), 6) for v in lon),
        grid_spacing_deg=spacing,
        granule_revision=revision,
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


def imerg_extraction_identity(
    *,
    operator_id: str,
    coordinate_table_sha256: str,
    granule_checksums: Mapping[str, str],
    window_start: str,
    window_end: str,
    box: tuple[int, int, int, int],
    read_contract: Mapping[str, object],
    output_schema_version: str = IMERG_OUTPUT_SCHEMA_VERSION,
    extraction_code_version: str = IMERG_EXTRACTION_CODE_VERSION,
) -> str:
    """D9/P7a — hashed over exactly what T2 READ: the acquisition
    manifest's granule checksums, the station table, the window, the box,
    the named operator and the frozen read contract. Never wall-clock time,
    output paths or hostname (those are provenance, recorded but not
    hashed)."""
    canonical = _canonical_json(
        {
            "operator_id": operator_id,
            "coordinate_table_sha256": coordinate_table_sha256,
            "granule_checksums": dict(sorted(granule_checksums.items())),
            "window_start": window_start,
            "window_end": window_end,
            "box": list(box),
            "read_contract": dict(read_contract),
            "output_schema_version": output_schema_version,
            "extraction_code_version": extraction_code_version,
        }
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


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
    window_start: datetime
    window_end: datetime
    box: tuple[int, int, int, int]
    read_contract: dict[str, object]
    retrospective: bool
    measured_acquisition_latency: str
    payload_sha256s: dict[str, str] = {}
    station_accounting: dict[str, dict[str, object]] = {}
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
    if manifest.extraction_identity not in directory.name:
        raise ExtractionPostConditionError(
            f"manifest extraction_identity {manifest.extraction_identity!r} "
            f"is not present in its own directory name {directory.name!r} (D9)"
        )
    if manifest.retrospective is not True:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest must be marked RETROSPECTIVE (D7)"
        )
    for name in IMERG_PAYLOAD_FILES:
        assert_payload_checksum_matches(directory, manifest, name)

    nearest = pl.read_csv(directory / _NEAREST_SERIES_FILENAME)
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
    per_station_rows = nearest.group_by("station_id").len()
    bad_counts = per_station_rows.filter(pl.col("len") != expected_hour_count)
    if bad_counts.height:
        raise ExtractionPostConditionError(
            f"{_NEAREST_SERIES_FILENAME} has station(s) with a row count != "
            f"expected_hour_count {expected_hour_count}: {bad_counts.to_dicts()} (D9)"
        )
    bad_granule_count = nearest.filter(~pl.col("granule_count").is_in([0, 1, 2]))
    if bad_granule_count.height:
        raise ExtractionPostConditionError(
            f"{_NEAREST_SERIES_FILENAME}.granule_count has value(s) outside "
            "{0, 1, 2} (D4/D9)"
        )
    # D4 — an hour with granule_count < 2 must be NaN; granule_count == 2
    # must be finite. Both directions checked: a value present under a
    # partial count would be silently invented data.
    inconsistent = nearest.filter(
        ((pl.col("granule_count") < 2) & pl.col("precip_mm_per_h").is_not_null())
        | ((pl.col("granule_count") == 2) & pl.col("precip_mm_per_h").is_null())
    )
    if inconsistent.height:
        raise ExtractionPostConditionError(
            f"{_NEAREST_SERIES_FILENAME} has {inconsistent.height} row(s) whose "
            "precip_mm_per_h/granule_count disagree (D4)"
        )

    station_cell = pl.read_csv(directory / _STATION_CELL_FILENAME)
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

    if not manifest.station_accounting:
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's station_accounting is empty (D4/D9)"
        )
    if set(manifest.station_accounting) != set(str(s) for s in stations_present):
        raise ExtractionPostConditionError(
            "IMERG extraction manifest's station_accounting station set does "
            "not match the published series' station set (D9)"
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
    last_error: ExtractionPostConditionError | None = None
    for candidate in reversed(candidates):
        manifest = _read_manifest(candidate / manifest_filename())
        if manifest is None:
            continue
        try:
            validate_imerg_bundle(
                candidate,
                manifest,
                expected_station_count=expected_station_count,
                expected_hour_count=expected_hour_count,
            )
        except ExtractionPostConditionError as exc:
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
    parser.add_argument("--out", type=Path, default=None)
    return parser


def run(
    args: argparse.Namespace,
    *,
    clock: Callable[[], datetime] | None = None,
    coords_path: Path | None = None,
    expected_stations: frozenset[Station] | None = None,
    params: DhmPrecipParams | None = None,
    granule_paths: list[Path] | None = None,
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

    resolved_granule_paths = (
        granule_paths
        if granule_paths is not None
        else sorted(imerg_raw_dir(data_root).glob("3B-HHR-E.*.HDF5"))
    )
    if not resolved_granule_paths:
        raise ExtractionInputAbsentError(
            f"no IMERG granule files found under {imerg_raw_dir(data_root)}"
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
        ds, contract = read_granule(path)
        if frozen_contract is None:
            frozen_contract = contract
        else:
            assert_contract_consistent(contract, frozen=frozen_contract)
        granule_checksums[path.name] = checksum_file(path)
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
    station_accounting: dict[str, dict[str, object]] = {}
    for station in stations.stations:
        grid_lat, grid_lon = grid_cell_by_station[station]
        valid_time, values, counts = aggregate_half_hourly_to_hourly(
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

        b_valid_time, b_values, _b_counts = aggregate_half_hourly_to_hourly(
            half_hourly_bilinear[station]
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

        station_accounting[str(station)] = {
            "n_hours": int(counts.size),
            "n_hours_complete": int(np.sum(counts == 2)),  # noqa: PLR2004
            "n_hours_partial": int(np.sum(counts == 1)),
            "n_hours_missing_granule": int(np.sum(counts == 0)),
            "n_nan_hours": int(np.sum(counts < 2)),  # noqa: PLR2004
        }

    station_cell_rows = build_station_cell_table(
        stations, nearest_by_station=grid_cell_by_station
    )
    sensitivity = build_operator_sensitivity_table(
        nearest_hourly, bilinear_hourly, params=resolved_params
    )

    identity = imerg_extraction_identity(
        operator_id=str(ExtractionOperator.NEAREST),
        coordinate_table_sha256=coordinate_table_sha256,
        granule_checksums=granule_checksums,
        window_start=FIRST_GRANULE_START.isoformat(),
        window_end=nearest_hourly[stations.stations[0]].valid_time[-1].astype(str),
        box=STUDY_BOX,
        read_contract=frozen_contract.as_manifest_dict(),
    )

    root = imerg_points_root(data_root)
    staged_dir = prepare_staging_dir(root, identity=identity)
    try:
        nearest_frame = pl.concat(
            [
                pl.DataFrame(
                    {
                        "station_id": [str(station)] * series.values.size,
                        "timestamp_utc": [str(t) for t in series.valid_time],
                        "precip_mm_per_h": series.values,
                        "granule_count": granule_counts_by_station[station],
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
            route=ROUTE,
            collection_short_name=COLLECTION_SHORT_NAME,
            granule_revision=frozen_contract.granule_revision,
            window_start=FIRST_GRANULE_START,
            window_end=nearest_hourly[stations.stations[0]]
            .valid_time[-1]
            .astype("datetime64[s]")
            .item()
            .replace(tzinfo=UTC),
            box=STUDY_BOX,
            read_contract=frozen_contract.as_manifest_dict(),
            retrospective=True,
            measured_acquisition_latency=MEASURED_ACQUISITION_LATENCY,
            payload_sha256s=payload_sha256s,
            station_accounting=station_accounting,
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
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "extraction_identity": identity,
                    "published_dir": str(final_dir),
                },
                indent=2,
            )
        )
    log.info("imerg_extract.cli.published", extraction_identity=identity)
    return 0


def main(argv: list[str] | None = None, **kwargs: object) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_cli_logging()
    try:
        return run(args, **kwargs)  # type: ignore[arg-type]
    except (DhmPrecipLoaderError, ExtractionInputAbsentError) as exc:
        log.error(
            "imerg_extract.cli.failed", error=str(exc), error_type=type(exc).__name__
        )
        return 2
    except ExtractionPostConditionError as exc:
        log.error(
            "imerg_extract.cli.failed", error=str(exc), error_type=type(exc).__name__
        )
        return 4
    except (Era5StorageError, OSError) as exc:
        log.error(
            "imerg_extract.cli.failed", error=str(exc), error_type=type(exc).__name__
        )
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
