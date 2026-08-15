"""Task 2e — climatology and missingness.

Monthly climatology normalised for coverage, DJF share, per-year totals under
`period_completeness` (no rescaling), and the wet-biased-missingness ratio
with the tested station excluded from the regional wet indicator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.domain_types import AxisStatus, View

if TYPE_CHECKING:
    from scripts.dhm_precip.domain_types import StationCoordinateTable
    from scripts.dhm_precip.params import DhmPrecipParams


def _tag(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        pl.lit(View.ON_GRID.value).alias("view"),
        pl.lit(AxisStatus.RAW_PROVISIONAL.value).alias("axis_status"),
    )


def _wet_mask(params: DhmPrecipParams) -> pl.Expr:
    threshold = pl.lit(params.wet_threshold_mm_per_h)
    if params.wet_threshold_side == ">=":
        return pl.col("value_mm") >= threshold
    return pl.col("value_mm") > threshold


def monthly_climatology(on_grid: pl.DataFrame) -> pl.DataFrame:
    """Per-station, per-calendar-month mean hourly intensity — a mean, not a
    sum, is inherently coverage-normalised (unequal non-null counts per
    month/year do not bias it the way a raw monthly sum would)."""
    with_month = on_grid.filter(pl.col("value_mm").is_not_null()).with_columns(
        pl.col("timestamp").dt.month().alias("month")
    )
    climatology = with_month.group_by(["station", "month"]).agg(
        pl.col("value_mm").mean().alias("mean_hourly_intensity_mm"),
        pl.len().alias("non_null_count"),
    )
    return _tag(climatology)


def djf_share_of_total(on_grid: pl.DataFrame, params: DhmPrecipParams) -> pl.DataFrame:
    """Per-station DJF share of the whole-record total (vision's reported figure)."""
    with_month = on_grid.filter(pl.col("value_mm").is_not_null()).with_columns(
        pl.col("timestamp").dt.month().alias("month")
    )
    per_station = with_month.group_by("station").agg(
        pl.col("value_mm")
        .filter(pl.col("month").is_in(params.djf_months))
        .sum()
        .alias("djf_total_mm"),
        pl.col("value_mm").sum().alias("annual_total_mm"),
    )
    return _tag(
        per_station.with_columns(
            (pl.col("djf_total_mm") / pl.col("annual_total_mm")).alias(
                "djf_share_of_annual_total"
            )
        )
    )


def per_year_totals_with_completeness(
    on_grid: pl.DataFrame, params: DhmPrecipParams
) -> pl.DataFrame:
    """Per-station-per-year total, with the retained-hour fraction that year —
    `period_completeness_min_fraction` flags which years are usable. No
    rescaling of incomplete totals (D8b)."""
    with_year = on_grid.with_columns(pl.col("timestamp").dt.year().alias("year"))
    slots_per_year = (
        with_year.select(["year", "timestamp"])
        .unique()
        .group_by("year")
        .agg(pl.len().alias("possible_hours"))
    )
    per_year = (
        with_year.group_by(["station", "year"])
        .agg(
            pl.col("value_mm").sum().alias("annual_total_mm"),
            pl.col("value_mm").is_not_null().sum().alias("retained_hours"),
        )
        .join(slots_per_year, on="year")
    )
    # D2b (Plan 172) — Polars' .sum() over an all-null group returns 0.0,
    # not null; a station-year with zero retained hours must report a
    # null total, never a fabricated 0.0 mm (D2's null-vs-zero guarantee
    # surviving aggregation).
    per_year = per_year.with_columns(
        pl.when(pl.col("retained_hours") == 0)
        .then(None)
        .otherwise(pl.col("annual_total_mm"))
        .alias("annual_total_mm")
    )
    return _tag(
        per_year.with_columns(
            (pl.col("retained_hours") / pl.col("possible_hours")).alias(
                "retained_fraction"
            ),
            (
                pl.col("retained_hours") / pl.col("possible_hours")
                >= params.period_completeness_min_fraction
            ).alias("period_complete"),
        )
    )


def wet_biased_missingness_ratio(
    on_grid: pl.DataFrame, stations: StationCoordinateTable, params: DhmPrecipParams
) -> pl.DataFrame:
    """Per-station `P(missing | region wet, station excluded) /
    P(missing | region dry, station excluded)`. The regional wet indicator
    for station i is computed from every OTHER usable station's ON_GRID
    values at that hour — re-tested after review to exclude the endogenous
    signal (vision Findings).

    `stations` (D12's validated 26-station table) restricts the OUTPUT rows
    to usable stations only — the 11 permanently-empty columns are always
    missing regardless of region wet/dry, so including them as output rows
    would pollute the population's median/max with trivial degenerate ratios
    (D4: never inferred from the raw 37-column population without saying so)."""
    is_wet = _wet_mask(params) & pl.col("value_mm").is_not_null()
    is_reporting = pl.col("value_mm").is_not_null()
    per_hour_totals = on_grid.group_by("timestamp").agg(
        is_wet.sum().alias("_total_wet"),
        is_reporting.sum().alias("_total_reporting"),
    )
    joined = on_grid.join(per_hour_totals, on="timestamp").with_columns(
        is_wet.alias("_self_wet"),
        is_reporting.alias("_self_reporting"),
    )
    excl = (
        joined.with_columns(
            (pl.col("_total_wet") - pl.col("_self_wet").cast(pl.Int64)).alias(
                "_wet_excl"
            ),
            (
                pl.col("_total_reporting") - pl.col("_self_reporting").cast(pl.Int64)
            ).alias("_reporting_excl"),
        )
        .with_columns(
            (pl.col("_reporting_excl") > 0).alias("_region_has_data"),
            (pl.col("_wet_excl") > 0).alias("_region_wet"),
            pl.col("value_mm").is_null().alias("_self_missing"),
        )
        .filter(pl.col("_region_has_data"))
    )

    per_station = (
        excl.filter(pl.col("station").is_in(stations.stations))
        .group_by("station")
        .agg(
            pl.col("_self_missing")
            .filter(pl.col("_region_wet"))
            .mean()
            .alias("p_missing_given_wet"),
            pl.col("_self_missing")
            .filter(~pl.col("_region_wet"))
            .mean()
            .alias("p_missing_given_dry"),
        )
    )
    return _tag(
        per_station.with_columns(
            (pl.col("p_missing_given_wet") / pl.col("p_missing_given_dry")).alias(
                "wet_biased_missingness_ratio"
            )
        )
    )
