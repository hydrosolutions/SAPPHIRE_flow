"""Shared reporting-resolution inference, used by both 2a (elevation overlap)
and 2c (reporting precision). Not one of the six named statistic families —
a pure-function support module so 2a needs no dependency on 2c's task output,
only on this shared library call (D4: no statistic task reads a file; this
one reads nothing either, it only classifies an already-loaded view)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from scripts.dhm_precip.params import DhmPrecipParams


def infer_reporting_resolution(
    on_grid: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """Per-station inferred reporting resolution & group.

    A station whose non-null, non-zero ON_GRID values are ALL integer
    multiples of `group_b_resolution_mm` (0.2 mm) within
    `resolution_epsilon_mm` is classified Group B; otherwise Group A
    (0.01 mm) — Group B's coarser grid cannot represent Group A's finer one.
    Returns columns `(station, group, resolution_mm)`.
    """
    nonzero = on_grid.filter(
        pl.col("value_mm").is_not_null() & (pl.col("value_mm") != 0.0)
    )
    ratio = pl.col("value_mm") / params.group_b_resolution_mm
    deviation = (ratio - ratio.round(0)).abs()
    flagged = nonzero.with_columns(deviation.alias("_deviation"))
    per_station = flagged.group_by("station").agg(
        (pl.col("_deviation") > params.resolution_epsilon_mm)
        .sum()
        .alias("_non_multiple_count"),
    )
    return per_station.with_columns(
        pl.when(pl.col("_non_multiple_count") == 0)
        .then(pl.lit("B"))
        .otherwise(pl.lit("A"))
        .alias("group"),
        pl.when(pl.col("_non_multiple_count") == 0)
        .then(pl.lit(params.group_b_resolution_mm))
        .otherwise(pl.lit(params.group_a_resolution_mm))
        .alias("resolution_mm"),
    ).select("station", "group", "resolution_mm")
