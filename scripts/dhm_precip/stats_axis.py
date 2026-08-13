"""Task 2b — time-axis diagnostics. `RAW`/`RAW_AXIS_DIAGNOSTIC` only (D6) —
these describe the raw axis itself, so `ON_GRID` would beg the question."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.domain_types import AxisStatus, View
from scripts.dhm_precip.numeric import as_datetime

if TYPE_CHECKING:
    from scripts.dhm_precip.params import DhmPrecipParams


def _tag(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        pl.lit(View.RAW.value).alias("view"),
        pl.lit(AxisStatus.RAW_AXIS_DIAGNOSTIC.value).alias("axis_status"),
    )


def row_count_diagnostics(raw: pl.DataFrame, params: DhmPrecipParams) -> pl.DataFrame:
    """Total rows, clean hourly slots (spanning the data's own min/max
    timestamp), off-grid row count, duplicate count, monotonicity."""
    timestamps = raw.select("timestamp").unique().sort("timestamp")
    total_rows = raw.select("source_row_index").n_unique()
    n_unique_timestamps = timestamps.height
    duplicate_row_count = total_rows - n_unique_timestamps

    lo = as_datetime(timestamps["timestamp"].min())
    hi = as_datetime(timestamps["timestamp"].max())
    clean_hourly_slots = pl.datetime_range(lo, hi, interval="1h", eager=True).len()

    off_grid_row_count = (
        raw.select("source_row_index", "timestamp")
        .unique()
        .filter(pl.col("timestamp").dt.minute() != params.on_grid_minute)
        .height
    )
    # Monotonicity must be checked against the ORIGINAL delivery order, not a
    # sorted copy — `timestamps` above is `.sort()`-ed for the span
    # computation, so `timestamps["timestamp"].is_sorted()` would trivially
    # always be True regardless of how the source file actually arrived.
    # One row per `source_row_index` shares one timestamp across all 37
    # station columns (the melt's per-original-row grain); dedup on
    # `source_row_index` and order by it to recover the file's own row order.
    original_order = (
        raw.select("source_row_index", "timestamp")
        .unique(subset=["source_row_index"])
        .sort("source_row_index")
    )
    monotonic = bool(original_order["timestamp"].is_sorted())

    return _tag(
        pl.DataFrame(
            {
                "total_rows": [total_rows],
                "clean_hourly_slot_count": [clean_hourly_slots],
                "off_grid_row_count": [off_grid_row_count],
                "duplicate_timestamp_count": [duplicate_row_count],
                "timestamp_monotonic": [monotonic],
            }
        )
    )


def off_grid_observation_diagnostics(
    raw: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """Off-grid *observation* count/fraction (D6b: sparser than the row fraction)."""
    total_non_null = raw.filter(pl.col("value_mm").is_not_null()).height
    off_grid_non_null = raw.filter(
        (pl.col("timestamp").dt.minute() != params.on_grid_minute)
        & pl.col("value_mm").is_not_null()
    ).height
    fraction = off_grid_non_null / total_non_null if total_non_null else 0.0
    return _tag(
        pl.DataFrame(
            {
                "off_grid_observation_count": [off_grid_non_null],
                "total_non_null_observations": [total_non_null],
                "off_grid_observation_fraction": [fraction],
            }
        )
    )


def off_grid_minute_distribution(
    raw: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """The distinct off-grid minute values present, with row counts each."""
    dist = (
        raw.select("source_row_index", "timestamp")
        .unique()
        .filter(pl.col("timestamp").dt.minute() != params.on_grid_minute)
        .with_columns(pl.col("timestamp").dt.minute().alias("minute"))
        .group_by("minute")
        .agg(pl.len().alias("row_count"))
        .sort("minute")
    )
    return _tag(dist)


def per_station_off_grid_attribution(
    raw: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """Which stations' non-null cells fall in off-grid rows, ranked descending."""
    off_grid_cells = raw.filter(
        (pl.col("timestamp").dt.minute() != params.on_grid_minute)
        & pl.col("value_mm").is_not_null()
    )
    per_station = (
        off_grid_cells.group_by("station")
        .agg(pl.len().alias("off_grid_observation_count"))
        .sort("off_grid_observation_count", descending=True)
    )
    return _tag(per_station)
