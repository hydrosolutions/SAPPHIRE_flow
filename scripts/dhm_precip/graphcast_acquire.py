"""Plan 240 (M-A12) T2 — retrieve GraphCast 00Z precipitation forecasts from
the pinned GFS-initialised NOAA Open Data prefix, extract to the 26 DHM
gauge points at leads 6/12/18/24 h, and emit the SAME seasonal-Parquet
contract `ifs_event_timing.load_ifs_series` already reads
(`station, init_time_utc, ending_lead_hours, valid_time_utc, tigge_mm`) so
that estimator runs UNCHANGED (D3). This module is ordinary boundary
adaptation — an extractor, not an estimator; ⛔ no new statistic, no new
framework (Plan 240 proportionality).

D1 — only `init_hour == 0` and leads 6/12/18/24 are retrieved: four JJAS
seasons x 122 days = 488 forecasts, never 0-240 h and never the 06/12/18Z
runs (all four are published in the bucket — measured, T1 — but only 00Z
feeds the frozen `ifs_event_timing` convention).

D2 — the pinned archive variant is `GRAP_v100_GFS` (GraphCast v1,
GFS-initialised); `GRAP_v100_IFS` also exists in the bucket and must never
be substituted silently, so every retrieved file's own global attributes
are asserted against the pin rather than trusted from its key alone.

D4 — read off T1's real sample, not documentation: `apcp`'s `long_name` is
"6-hr accumulated precipitation" and its `units` is `"m"`. It is already a
genuine 6-hour PERIOD-ENDING total (unlike TIGGE's cumulative-from-init
`tp`, which `tigge_ifs.py` deaccumulates) — no deaccumulation here,
`valid_time = init_time + ending_lead_hours` directly, and the only
conversion is metres -> millimetres. A small amount of negative numerical
noise was measured in the real sample (~-1.4e-4 m); clipped to zero, the
same convention `tigge_ifs.deaccumulate` already applies to its own
negative increments (`np.clip(diff, 0.0, None)`).

D5 — nearest-cell great-circle, the SAME operator `tigge_ifs.py` uses
(`nearest_point_index`, imported and reused rather than reimplemented). The
GraphCast grid is a REGULAR 0.25 deg lat/lon grid (721 x 1440, measured,
T1) rather than TIGGE's irregular reduced-Gaussian point cloud, so the
nearest-cell search runs over the grid's own flattened mesh — same
function, same distance metric, applied to a different point cloud shape.

D6 — retrieved for the SAME four seasons the IFS comparison recomputes on
(`graphcast_ifs_compare.py`); a missing forecast key is a recorded gap,
never filled.

D7 — this archive is GFS-initialised; IFS control is ECMWF-initialised.
This module does not judge the comparison, only produces one side of it —
the confound is stated by the comparison driver.

Route chosen from T1's measurement: neither a whole-file download
(~9.3 GB/forecast) nor `xarray.open_dataset` (which walks EVERY variable's
metadata just to open the file — measured ~1.3 GB for that alone) — both
wasteful for the 4-timestep x 1-variable slice this plan needs. Instead:
open with `h5py` directly against the `apcp` dataset only, then index just
the four needed time steps in one contiguous read
(`dset[lo:hi+1]`) — HDF5's own filter pipeline (gzip + shuffle) decodes it
correctly, and only those chunks' compressed bytes cross the network.
Measured: ~10.16 MB and ~5 s per forecast (T1); ~5 GB projected for 488
forecasts, not ~4.5 TB.
"""

from __future__ import annotations

import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import structlog

from sapphire_flow.exceptions import SapphireError
from scripts.dhm_precip.loader import load_station_coordinates, resolve_coords_path
from scripts.dhm_precip.ma6_pairs import load_gauge_masked_population
from scripts.dhm_precip.tigge_ifs import (
    TIGGE_MONTHS,
    nearest_point_index,
    points_filename,
)

if TYPE_CHECKING:
    import s3fs

    from scripts.dhm_precip.domain_types import Station, StationCoordinateTable

log = structlog.get_logger(__name__)

# --- D2: pinned archive variant, MEASURED 2026-09-04 against the live
# `noaa-oar-mlwp-data` bucket. `GRAP_v100_GFS` is the GFS-initialised
# GraphCast v1 prefix; `GRAP_v100_IFS` also exists and must never be
# substituted silently (D7's confound is stated, not hidden by a
# convenient default).
GRAPHCAST_BUCKET = "noaa-oar-mlwp-data"
GRAPHCAST_PREFIX = "GRAP_v100_GFS"
EXPECTED_MODEL_NAME = "GraphCast"
EXPECTED_MODEL_VERSION = "v1"
EXPECTED_INIT_MODEL = "GFS"

# D1 — only 00Z. ⛔ These leads MUST match
# `ifs_event_timing.CONTINUOUS_DAY_LEADS` exactly; not re-imported (that
# module has no public re-export point for just the leads without pulling
# in the whole estimator), so any drift is caught at the report layer
# instead (`graphcast_ifs_compare.py` imports leads from
# `ifs_event_timing` and passes them to both `build_cells` calls).
GRAPHCAST_INIT_HOUR_UTC = 0
GRAPHCAST_LEAD_STEP_H = 6

# D4 — read off T1's real sample: `apcp` stores metres, already period-
# ending (no deaccumulation).
GRAPHCAST_PRECIP_VAR = "apcp"
EXPECTED_PRECIP_UNITS = "m"
METRES_TO_MM = 1000.0

# D5 — the archive's own published grid, MEASURED (T1): 0.25 deg regular,
# latitude descending 90..-90 (721), longitude ascending 0..359.75 (1440).
# Read from each opened file's own coordinate variables, never assumed
# module-level, but this is what `_assert_grid_shape` checks against.
EXPECTED_GRID_SHAPE = (721, 1440)

DEFAULT_GRAPHCAST_SEASONS: tuple[int, ...] = (2022, 2023, 2024, 2025)
DEFAULT_GRAPHCAST_ROOT = Path("data/dhm_precip/graphcast")
DEFAULT_MAX_WORKERS = 8


class GraphcastAcquisitionError(SapphireError):
    """A retrieved file's identity does not match the pinned D2 archive
    variant, or its grid does not match the expected shape."""


def forecast_key(init_time: datetime, *, prefix: str = GRAPHCAST_PREFIX) -> str:
    """The bucket's own naming convention, MEASURED (T1):
    `<prefix>/<YYYY>/<MMDD>/<prefix>_<YYYYMMDDHH>_f000_f240_06.nc`."""
    return (
        f"{prefix}/{init_time:%Y}/{init_time:%m%d}/"
        f"{prefix}_{init_time:%Y%m%d%H}_f000_f240_06.nc"
    )


def season_init_times(
    year: int,
    *,
    months: tuple[int, ...] = TIGGE_MONTHS,
    init_hour: int = GRAPHCAST_INIT_HOUR_UTC,
) -> tuple[datetime, ...]:
    """Every 00Z init in one JJAS season — D1's 122-day/season schedule."""
    first, last = min(months), max(months)
    times: list[datetime] = []
    for month in range(first, last + 1):
        n_days = calendar.monthrange(year, month)[1]
        times.extend(
            datetime(year, month, day, init_hour)  # noqa: DTZ001 — naive, matching the parquet's naive UTC axis (ifs_event_timing does the same)
            for day in range(1, n_days + 1)
        )
    return tuple(times)


def _attr_str(value: object) -> str:
    """h5py returns fixed-length HDF5 string attributes as `bytes`; decode
    so a pin comparison never fails on an encoding artefact rather than a
    real identity mismatch."""
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _assert_pinned_identity(attrs: dict[str, object], *, key: str) -> None:
    """D2 — the file's OWN global attributes must state the pinned variant,
    never assumed from its key alone."""
    model_name = _attr_str(attrs.get("model_name", ""))
    model_version = _attr_str(attrs.get("model_version", ""))
    init_model = _attr_str(attrs.get("initialization_model", ""))
    if (model_name, model_version, init_model) != (
        EXPECTED_MODEL_NAME,
        EXPECTED_MODEL_VERSION,
        EXPECTED_INIT_MODEL,
    ):
        raise GraphcastAcquisitionError(
            f"{key}: attributes (model_name={model_name!r}, "
            f"model_version={model_version!r}, "
            f"initialization_model={init_model!r}) do not match the pinned "
            f"({EXPECTED_MODEL_NAME!r}, {EXPECTED_MODEL_VERSION!r}, "
            f"{EXPECTED_INIT_MODEL!r})"
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class NearestCellIndex:
    """D5 — one station's (row, col) into the GraphCast regular grid,
    computed ONCE per run against the grid the first successfully opened
    file reports (never per-file — the grid is the archive's fixed
    0.25 deg publication grid, so re-deriving it 488 times would spend
    network bytes on something that does not change)."""

    row: int
    col: int


def nearest_cell_indices(
    coords: StationCoordinateTable,
    *,
    lat: np.ndarray,
    lon: np.ndarray,
    expected_shape: tuple[int, int] | None = EXPECTED_GRID_SHAPE,
) -> dict[Station, NearestCellIndex]:
    """D5 — reuses `tigge_ifs.nearest_point_index` (the SAME haversine
    argmin operator IFS extraction uses) against the flattened regular-grid
    mesh, so a point-cloud operator and a regular-grid operator are never
    two different implementations. `expected_shape` defaults to the
    archive's own published grid (production safety net); tests pass
    `None` or a smaller synthetic shape."""
    if expected_shape is not None and (lat.size, lon.size) != expected_shape:
        raise GraphcastAcquisitionError(
            f"grid shape ({lat.size}, {lon.size}) != expected "
            f"{expected_shape} — the archive's publication grid has "
            "changed since T1 measured it"
        )
    lat_mesh, lon_mesh = np.meshgrid(lat, lon, indexing="ij")
    lat_flat = lat_mesh.ravel()
    lon_flat = lon_mesh.ravel()
    n_lon = lon.size
    result: dict[Station, NearestCellIndex] = {}
    for station, coord in coords.by_station.items():
        flat_idx = nearest_point_index(
            lat=lat_flat, lon=lon_flat, station_lat=coord.lat, station_lon=coord.lon
        )
        row, col = divmod(flat_idx, n_lon)
        result[station] = NearestCellIndex(row=row, col=col)
    return result


def load_dhm_coordinates() -> StationCoordinateTable:
    """The same 26-station population `ifs_event_timing.build_cells` masks
    to, loaded independently here because the extractor runs before any
    cell-building step."""
    population = load_gauge_masked_population()
    live_stations = frozenset(population.by_station.keys())
    return load_station_coordinates(
        resolve_coords_path(), expected_stations=live_stations
    )


def extract_box_apcp(
    *,
    fs: s3fs.S3FileSystem,
    init_time: datetime,
    nearest: dict[Station, NearestCellIndex],
    lead_hours: tuple[int, ...],
) -> pl.DataFrame | None:
    """One forecast's precipitation at `lead_hours`, nearest-cell (D5) to
    every station in `nearest`. Returns `None` — a GAP, never filled (D6)
    — if the key does not exist in the bucket. Route: T1's measured cheap
    path — h5py directly, indexing only the needed time steps in one
    contiguous read, never `xarray.open_dataset` and never a whole-file
    download."""
    import h5py  # noqa: PLC0415 — lazy: unit tests must not require network/h5py

    key = forecast_key(init_time)
    path = f"{GRAPHCAST_BUCKET}/{key}"
    step_indices = sorted(h // GRAPHCAST_LEAD_STEP_H for h in lead_hours)
    lo, hi = step_indices[0], step_indices[-1]
    if step_indices != list(range(lo, hi + 1)):
        raise GraphcastAcquisitionError(
            f"lead_hours {lead_hours} do not map to a contiguous step "
            "range — the single-block read assumes contiguity"
        )

    try:
        fh = fs.open(path, mode="rb", cache_type="none")
    except FileNotFoundError:
        return None

    try:
        with h5py.File(fh, "r") as h5:
            _assert_pinned_identity(dict(h5.attrs), key=key)
            dset = h5[GRAPHCAST_PRECIP_VAR]
            units = _attr_str(dset.attrs.get("units", ""))
            if units != EXPECTED_PRECIP_UNITS:
                raise GraphcastAcquisitionError(
                    f"{key}: {GRAPHCAST_PRECIP_VAR} units {units!r} != "
                    f"expected {EXPECTED_PRECIP_UNITS!r} (D4)"
                )
            n_steps = dset.shape[0]
            if hi >= n_steps:
                raise GraphcastAcquisitionError(
                    f"{key}: only {n_steps} steps present, need index {hi} "
                    f"for lead {lead_hours[-1]} h"
                )
            block = np.asarray(dset[lo : hi + 1])  # (n_leads, lat, lon), metres
    finally:
        fh.close()

    stations = list(nearest.keys())
    rows = np.array([nearest[s].row for s in stations])
    cols = np.array([nearest[s].col for s in stations])
    station_mm = block[:, rows, cols] * METRES_TO_MM  # (n_leads, n_stations)
    station_mm = np.clip(
        station_mm, 0.0, None
    )  # D4 — clip numerical noise, never negative

    n_leads = len(step_indices)
    lead_col = np.repeat(
        np.array(step_indices, dtype=np.int64) * GRAPHCAST_LEAD_STEP_H, len(stations)
    )
    station_col = np.tile(np.array(stations, dtype=object), n_leads)
    init_col = np.full(n_leads * len(stations), np.datetime64(init_time, "ns"))
    valid_col = init_col + (lead_col.astype("timedelta64[h]"))
    mm_col = station_mm.ravel().astype(np.float64)  # match tigge_ifs's own f64 column

    return pl.DataFrame(
        {
            "station": [str(s) for s in station_col],
            "init_time_utc": init_col,
            "ending_lead_hours": lead_col,
            "valid_time_utc": valid_col,
            "tigge_mm": mm_col,
        }
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class SeasonRetrieval:
    """One season's extracted rows plus which inits were gaps (D6)."""

    frame: pl.DataFrame
    gaps: tuple[datetime, ...]
    n_requested: int


def retrieve_season(
    *,
    fs: s3fs.S3FileSystem,
    year: int,
    nearest: dict[Station, NearestCellIndex],
    lead_hours: tuple[int, ...],
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> SeasonRetrieval:
    """D1/D6 — every 00Z init in one JJAS season, concurrently (I/O-bound:
    each forecast is an independent set of HTTP range reads). A missing
    key is recorded as a gap, never filled or interpolated."""
    inits = season_init_times(year)
    frames: list[pl.DataFrame] = []
    gaps: list[datetime] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                extract_box_apcp,
                fs=fs,
                init_time=init_time,
                nearest=nearest,
                lead_hours=lead_hours,
            ): init_time
            for init_time in inits
        }
        for future in as_completed(futures):
            init_time = futures[future]
            result = future.result()
            if result is None:
                gaps.append(init_time)
            else:
                frames.append(result)
    frame = (
        pl.concat(frames).sort(["station", "init_time_utc", "ending_lead_hours"])
        if frames
        else pl.DataFrame(
            schema={
                "station": pl.Utf8,
                "init_time_utc": pl.Datetime("ns"),
                "ending_lead_hours": pl.Int64,
                "valid_time_utc": pl.Datetime("ns"),
                "tigge_mm": pl.Float64,
            }
        )
    )
    return SeasonRetrieval(
        frame=frame, gaps=tuple(sorted(gaps)), n_requested=len(inits)
    )


def write_points_parquet(frame: pl.DataFrame, *, out_root: Path, year: int) -> Path:
    """D3 — writes the SAME contract path shape `ifs_event_timing.tigge_series_paths`
    reads (`<root>/points/<points_filename(year)>`), under a NEW `graphcast/`
    root — never under the production `tigge/` tree."""
    out_dir = out_root / "points"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / points_filename(year)
    frame.write_parquet(out_path)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "M-A12 (Plan 240) — retrieve GraphCast 00Z precipitation, "
            "leads 6/12/18/24 h, extract to the 26 DHM gauge points"
        )
    )
    parser.add_argument(
        "--seasons", type=int, nargs="+", default=list(DEFAULT_GRAPHCAST_SEASONS)
    )
    parser.add_argument("--leads", type=int, nargs="+", default=[6, 12, 18, 24])
    parser.add_argument("--out-root", type=Path, default=DEFAULT_GRAPHCAST_ROOT)
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    args = parser.parse_args()

    import s3fs  # noqa: PLC0415 — lazy: unit tests must not require network/s3fs

    fs = s3fs.S3FileSystem(anon=True)
    coords = load_dhm_coordinates()

    # D5 — derive the nearest-cell mapping ONCE, from the first season's
    # first available forecast, then reuse it for every subsequent file.
    seasons = tuple(sorted(set(args.seasons)))
    leads = tuple(sorted(set(args.leads)))
    first_init = season_init_times(seasons[0])[0]
    import h5py  # noqa: PLC0415

    probe_key = forecast_key(first_init)
    with (
        fs.open(f"{GRAPHCAST_BUCKET}/{probe_key}", mode="rb", cache_type="none") as fh,
        h5py.File(fh, "r") as h5,
    ):
        _assert_pinned_identity(dict(h5.attrs), key=probe_key)
        lat = np.asarray(h5["latitude"][:])
        lon = np.asarray(h5["longitude"][:])
    nearest = nearest_cell_indices(coords, lat=lat, lon=lon)

    for year in seasons:
        result = retrieve_season(
            fs=fs,
            year=year,
            nearest=nearest,
            lead_hours=leads,
            max_workers=args.max_workers,
        )
        out_path = write_points_parquet(result.frame, out_root=args.out_root, year=year)
        log.info(
            "graphcast_acquire.season_written",
            year=year,
            out_path=str(out_path),
            n_requested=result.n_requested,
            n_gaps=len(result.gaps),
            n_rows=result.frame.height,
        )
        if result.gaps:
            log.warning(
                "graphcast_acquire.season_gaps",
                year=year,
                gaps=[g.isoformat() for g in result.gaps],
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
