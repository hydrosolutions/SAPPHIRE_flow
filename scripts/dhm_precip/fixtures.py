"""Dev-only synthetic workbook fixture builder (constraint 3, D1).

Reproduces each defect *signature* — a pinned-value block, a sentinel run —
never real DHM values (constraint 1). Depends on the dev-only `xlsxwriter`
group; imported by tests only, never by `run.py` or `evaluate.py`.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# Precedent: src/sapphire_flow/adapters/meteoswiss_nwp.py:1 — xarray ships
# partial type stubs; the same three rules are relaxed repo-wide for every
# adapter that touches it (Plan 174 M-A5's ERA5-Land fixture builder here).
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Literal

import numpy as np
import polars as pl
import xarray as xr

from scripts.dhm_precip.loader import EXPECTED_WORKBOOK_COLUMNS, TIME_COLUMN

if TYPE_CHECKING:
    import random
    from datetime import datetime
    from pathlib import Path

DEFAULT_SENTINEL_STATION = EXPECTED_WORKBOOK_COLUMNS[3]  # "Lukla Airport (mm)"
DEFAULT_STUCK_HIGH_STATION = EXPECTED_WORKBOOK_COLUMNS[24]  # "Sindhuli Madhi (mm)"


def build_synthetic_workbook_frame(
    *,
    start: datetime,
    n_hours: int,
    rng: random.Random,
    sentinel_value: float = -9999999.0,
    sentinel_station: str = DEFAULT_SENTINEL_STATION,
    sentinel_count: int = 5,
    stuck_high_station: str = DEFAULT_STUCK_HIGH_STATION,
    stuck_high_value: float = 72.0,
    stuck_high_run_hours: int = 6,
    zero_run_station: str | None = None,
    zero_run_hours: int = 0,
    empty_stations: tuple[str, ...] = (),
) -> pl.DataFrame:
    """A full 38-column synthetic frame matching `EXPECTED_WORKBOOK_COLUMNS`,
    with named defect signatures injected at known offsets — never real values.
    """
    timestamps = [start + timedelta(hours=i) for i in range(n_hours)]
    data: dict[str, list[float | None]] = {}
    for index, col in enumerate(EXPECTED_WORKBOOK_COLUMNS):
        if col in empty_stations:
            data[col] = [None] * n_hours
            continue
        # Alternate reporting resolution by station index so a synthetic
        # frame always contains both Group A (0.01 mm) and Group B (0.2 mm)
        # stations — real data always has both; a same-resolution-only
        # fixture would silently exercise a narrower code path than reality.
        resolution = 0.01 if index % 2 == 0 else 0.2
        data[col] = [
            round(round(rng.uniform(0.0, 5.0) / resolution) * resolution, 2)
            if rng.random() < 0.3
            else 0.0
            for _ in range(n_hours)
        ]

    if sentinel_station not in empty_stations:
        for i in range(min(sentinel_count, n_hours)):
            data[sentinel_station][i] = sentinel_value

    if stuck_high_station not in empty_stations:
        offset = n_hours // 2
        for i in range(offset, min(offset + stuck_high_run_hours, n_hours)):
            data[stuck_high_station][i] = stuck_high_value

    if zero_run_station is not None and zero_run_station not in empty_stations:
        for i in range(min(zero_run_hours, n_hours)):
            data[zero_run_station][i] = 0.0

    frame = pl.DataFrame(data)
    return frame.with_columns(pl.Series(TIME_COLUMN, timestamps)).select(
        [TIME_COLUMN, *EXPECTED_WORKBOOK_COLUMNS]
    )


def write_synthetic_workbook(path: Path, frame: pl.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_excel(path)


def write_synthetic_coordinates(path: Path, *, usable_columns: tuple[str, ...]) -> None:
    """A D12 coordinate table for exactly the given (non-empty) workbook
    columns — synthetic lat/lon spread over a small Nepal-like grid, never
    real station coordinates."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = pl.DataFrame(
        {
            "station": [col.removesuffix(" (mm)") for col in usable_columns],
            "excel_col": list(usable_columns),
            "lat": [27.0 + 0.1 * i for i in range(len(usable_columns))],
            "lon": [85.0 + 0.1 * i for i in range(len(usable_columns))],
            "elev": [500.0 + 100.0 * i for i in range(len(usable_columns))],
        }
    )
    rows.write_csv(path)


# --- Plan 174 (M-A5) task 2a — synthetic ERA5-Land D9-schema NetCDF fixture ---
#
# Conforms exactly to M-A4's (Plan 171) `validate_output_schema` /
# `validate_output_encoding` (`scripts/dhm_precip/era5_deaccumulate.py`,
# `scripts/dhm_precip/era5_transform.py`) — extraction (M-A5) reads real M-A4
# products, so its own fixtures must satisfy the SAME schema, over a small
# sub-box rather than the full acquired study area (constraint 1: never real
# coordinates or real data in a committed test).

Era5FixtureDefect = Literal[
    "nan_patch", "gap", "duplicate_stamp", "non_hourly_stride", "truncated_year"
]

_HOUR = np.timedelta64(1, "h")
_FIXTURE_PERIOD_ENDING_CONVENTION = "hour t covers t-1 -> t (UTC)"


def build_era5_land_product_fixture(
    *,
    year: int = 2021,
    area: tuple[float, float, float, float] = (26.2, 85.0, 26.0, 85.2),
    ramp_intercept: float = 0.5,
    ramp_lat_coeff: float = 1.0,
    ramp_lon_coeff: float = 0.1,
    defect: Era5FixtureDefect | None = None,
    defect_cell: tuple[int, int] = (1, 1),
) -> xr.Dataset:
    """A D9-schema-conformant `precipitation` (mm) product over `area`
    (north, west, south, east — D2 order; a small 3x3-node sub-box at the
    real 0.1 deg spacing, cell-centre registered, by default), one full
    calendar `year`, filled with a KNOWN ANALYTIC linear ramp
    `intercept + lat_coeff*i + lon_coeff*j` (time-invariant — both nearest
    and bilinear are then hand-computable at any station). `defect` mutates
    the clean base to the named invalid variant (2a: "each defective variant
    fails [validation] for the expected reason")."""
    from scripts.dhm_precip.era5_deaccumulate import (
        ACCUMULATION_RULE_ID,
        OUTPUT_SCHEMA_VERSION,
    )
    from scripts.dhm_precip.era5_request import expected_grid_shape
    from scripts.dhm_precip.era5_transform import TRANSFORM_VERSION

    north, west, south, east = area
    lat_count, lon_count = expected_grid_shape(area)
    lat = np.round(np.linspace(south, north, lat_count), 10)
    lon = np.round(np.linspace(west, east, lon_count), 10)

    start = np.datetime64(f"{year:04d}-01-01T00:00:00")
    end = np.datetime64(f"{year:04d}-12-31T23:00:00")
    n_hours = int((end - start) / _HOUR) + 1
    valid_time = start + np.arange(n_hours) * _HOUR

    ramp = (
        ramp_intercept
        + ramp_lat_coeff * np.arange(lat_count)[:, None]
        + (ramp_lon_coeff * np.arange(lon_count)[None, :])
    )
    values = (
        np.broadcast_to(ramp, (n_hours, lat_count, lon_count)).astype(np.float32).copy()
    )

    if defect == "nan_patch":
        i, j = defect_cell
        values[:, i, j] = np.nan

    if defect == "duplicate_stamp":
        valid_time = valid_time.copy()
        valid_time[1] = valid_time[0]
    elif defect == "non_hourly_stride":
        valid_time = valid_time.copy()
        valid_time[len(valid_time) // 2] += np.timedelta64(30, "m")
    elif defect == "gap":
        keep = np.ones(n_hours, dtype=bool)
        keep[len(valid_time) // 2] = False
        valid_time = valid_time[keep]
        values = values[keep]
    elif defect == "truncated_year":
        valid_time = valid_time[:-1]
        values = values[:-1]

    ds = xr.Dataset(
        {"precipitation": (["valid_time", "latitude", "longitude"], values)},
        coords={"valid_time": valid_time, "latitude": lat, "longitude": lon},
    )
    ds["precipitation"].attrs["units"] = "mm"
    ds.attrs.update(
        {
            "period_ending_convention": _FIXTURE_PERIOD_ENDING_CONVENTION,
            "accumulation_rule": ACCUMULATION_RULE_ID,
            "transform_version": TRANSFORM_VERSION,
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "source_dataset": "reanalysis-era5-land",
        }
    )
    return ds


def write_era5_land_product_fixture(ds: xr.Dataset, path: Path) -> None:
    """Writes `ds` with `valid_time` pinned to the SAME CF epoch encoding
    M-A4's real transform writes (`hours since 1970-01-01`, `int64` —
    `scripts/dhm_precip/era5_transform.py:_TIME_ENCODING_UNITS`), so a
    reopened extraction-side epoch/dtype check (D5.0) passes exactly as it
    would for a real product. Compression/chunking are NOT pinned here —
    extraction (M-A5) never re-asserts M-A4's own storage-encoding contract
    (`validate_output_encoding`), only the UTC-epoch semantics it reads."""
    encoding = {
        "valid_time": {
            "units": "hours since 1970-01-01 00:00:00",
            "dtype": "int64",
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path, engine="h5netcdf", encoding=encoding)
