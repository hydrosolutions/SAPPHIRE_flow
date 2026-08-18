"""Plan 182 (M-A10) D5 — circular bootstrap on the diurnal peak hour, by
whole monsoon season.

Resamples SEASON-YEARS (never individual hours — Rule: the unit of
resampling is the thing whose independence the adequacy claim rests on),
recomputes each resample's normalised profile, and reports the CIRCULAR
spread of the resulting peak hours (`circular.circular_range_hours`).
`adequate_sample` applies D5's small-sample rule: **fewer than
`min_season_years_for_adequacy` season-years cannot on their own establish
adequacy, regardless of how narrow the spread looks** — a caller must check
`adequate_sample`, never `spread_hours` alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from scripts.dhm_precip.circular import circular_range_hours
from scripts.dhm_precip.numeric import as_float

if TYPE_CHECKING:
    import random


class NoSeasonYearsError(ValueError):
    """`bootstrap_peak_hour_spread` was given zero season-years to resample."""


class EmptyBootstrapResultError(ValueError):
    """Every resample produced a zero grand mean (no usable peak hour) —
    should not occur on real wet-season data, but never silently reported
    as a spread of 0.0 if it does."""


def per_season_hourly_means(retained_frame: pl.DataFrame) -> pl.DataFrame:
    """`(station, timestamp, value_mm)` -> `(season_year, hour,
    mean_value_mm)`, JJAS never crossing a year boundary (the calendar year
    of a JJAS timestamp IS its season-year, unlike DJF)."""
    with_hour = retained_frame.filter(pl.col("value_mm").is_not_null()).with_columns(
        pl.col("timestamp").dt.hour().alias("hour"),
        pl.col("timestamp").dt.year().alias("season_year"),
    )
    return with_hour.group_by(["season_year", "hour"]).agg(
        pl.col("value_mm").mean().alias("mean_value_mm")
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class BootstrapPeakSpread:
    peak_hours: tuple[int, ...]
    spread_hours: float
    n_season_years: int
    adequate_sample: bool
    """D5 — `n_season_years >= min_season_years_for_adequacy`. A caller
    must gate on THIS, never on `spread_hours` alone — a narrow spread from
    too few season-years is indicative, not adequate (round-2 fix)."""


def bootstrap_peak_hour_spread(
    per_season_hourly: pl.DataFrame,
    *,
    rng: random.Random,
    n_resamples: int,
    min_season_years_for_adequacy: int,
) -> BootstrapPeakSpread:
    by_year: dict[int, dict[int, float]] = {}
    for row in per_season_hourly.iter_rows(named=True):
        by_year.setdefault(int(row["season_year"]), {})[int(row["hour"])] = as_float(
            row["mean_value_mm"]
        )
    season_years = sorted(by_year)
    n_season_years = len(season_years)
    if n_season_years == 0:
        raise NoSeasonYearsError("no season-years available for bootstrap")

    peak_hours: list[int] = []
    for _ in range(n_resamples):
        drawn = [rng.choice(season_years) for _ in range(n_season_years)]
        sums = [0.0] * 24
        counts = [0] * 24
        for year in drawn:
            for hour, value in by_year[year].items():
                sums[hour] += value
                counts[hour] += 1
        observed_hours = [h for h in range(24) if counts[h] > 0]
        if not observed_hours:
            continue
        means = {h: sums[h] / counts[h] for h in observed_hours}
        grand_mean = sum(means.values()) / len(observed_hours)
        if grand_mean == 0.0:
            continue
        normalised = {h: means[h] / grand_mean for h in observed_hours}
        peak = max(observed_hours, key=lambda h: (normalised[h], -h))
        peak_hours.append(peak)

    if not peak_hours:
        raise EmptyBootstrapResultError(
            "every bootstrap resample produced a zero grand mean — no usable peak hour"
        )

    return BootstrapPeakSpread(
        peak_hours=tuple(peak_hours),
        spread_hours=circular_range_hours([float(h) for h in peak_hours]),
        n_season_years=n_season_years,
        adequate_sample=n_season_years >= min_season_years_for_adequacy,
    )
