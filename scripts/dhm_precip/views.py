"""D6 — the two named views, built once from the canonical long frame (D3).

Pure functions, no I/O (D4). `RAW` is every delivered cell; `ON_GRID` is
`RAW` restricted to `minute == params.on_grid_minute` — the on-the-hour
subset the vision states its statistics use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.domain_types import ViewCounts

if TYPE_CHECKING:
    from scripts.dhm_precip.params import DhmPrecipParams


def raw_view(long_frame: pl.DataFrame) -> pl.DataFrame:
    return long_frame


def on_grid_view(long_frame: pl.DataFrame, params: DhmPrecipParams) -> pl.DataFrame:
    return long_frame.filter(pl.col("timestamp").dt.minute() == params.on_grid_minute)


def compute_view_counts(view: pl.DataFrame) -> ViewCounts:
    """D6b — the three grain counts for one view's long frame."""
    source_timestamp_rows = view.select("source_row_index").n_unique()
    station_timestamp_cells = view.height
    non_null_observations = view.filter(pl.col("value_mm").is_not_null()).height
    return ViewCounts(
        source_timestamp_rows=source_timestamp_rows,
        station_timestamp_cells=station_timestamp_cells,
        non_null_observations=non_null_observations,
    )
