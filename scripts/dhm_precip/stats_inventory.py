"""Task 2a — inventory and coverage.

Pure functions of `(view, params, stations)` (D4): usable-vs-empty stations,
per-station span/coverage, hourly reporting simultaneity, and the Group A/B
elevation-overlap statistic (D12). Every result carries `view` and
`axis_status` columns (never a table attribute, D7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.domain_types import (
    AxisStatus,
    LongFrameInventory,
    StationCoordinateTable,
    View,
)
from scripts.dhm_precip.resolution import infer_reporting_resolution

if TYPE_CHECKING:
    from scripts.dhm_precip.params import DhmPrecipParams


def usable_station_inventory(inventory: LongFrameInventory) -> pl.DataFrame:
    """`AXIS_INDEPENDENT` — the workbook column inventory and empty-column list."""
    empty = set(inventory.empty_columns)
    rows = [
        {"station": station, "is_usable": station not in empty}
        for station in inventory.all_columns
    ]
    return pl.DataFrame(rows).with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.AXIS_INDEPENDENT.value).alias("axis_status"),
    )


def hourly_reporting_simultaneity(on_grid: pl.DataFrame) -> pl.DataFrame:
    """RAW_PROVISIONAL — count of stations reporting non-null per ON_GRID hour."""
    per_hour = (
        on_grid.filter(pl.col("value_mm").is_not_null())
        .group_by("timestamp")
        .agg(pl.len().alias("reporting_station_count"))
    )
    return per_hour.with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.RAW_PROVISIONAL.value).alias("axis_status"),
    )


def station_span_and_coverage(on_grid: pl.DataFrame) -> pl.DataFrame:
    """RAW_PROVISIONAL — per-station first/last non-null timestamp and coverage
    fraction (non-null ON_GRID cells / ON_GRID slots spanned)."""
    non_null = on_grid.filter(pl.col("value_mm").is_not_null())
    span = non_null.group_by("station").agg(
        pl.col("timestamp").min().alias("first_timestamp"),
        pl.col("timestamp").max().alias("last_timestamp"),
        pl.len().alias("non_null_count"),
    )
    total_slots = on_grid.select("timestamp").n_unique()
    return span.with_columns(
        (pl.col("non_null_count") / pl.lit(total_slots)).alias("coverage_fraction"),
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.RAW_PROVISIONAL.value).alias("axis_status"),
    )


def group_elevation_overlap(
    on_grid: pl.DataFrame, stations: StationCoordinateTable, params: DhmPrecipParams
) -> pl.DataFrame:
    """`AXIS_INDEPENDENT` — D12's Group A/B elevation-overlap statistic.

    Coordinate-only once group membership is fixed — the *elevation* values
    are unaffected by M-A2, even though `group` itself is inferred from
    ON_GRID values.
    """
    resolution = infer_reporting_resolution(on_grid, params)
    elevations = pl.DataFrame(
        [{"station": s, "elev_m": c.elev_m} for s, c in stations.by_station.items()]
    )
    joined = resolution.join(elevations, on="station", how="inner")
    per_group = joined.group_by("group").agg(
        pl.col("elev_m").min().alias("elev_min_m"),
        pl.col("elev_m").max().alias("elev_max_m"),
    )
    a_min = per_group.filter(pl.col("group") == "A")["elev_min_m"].item()
    b_max = per_group.filter(pl.col("group") == "B")["elev_max_m"].item()
    gap = per_group.with_columns(
        pl.lit(a_min - b_max).alias("group_a_min_minus_group_b_max_m"),
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.AXIS_INDEPENDENT.value).alias("axis_status"),
    )
    return gap
