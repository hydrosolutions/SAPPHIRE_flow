"""Plan 182 (M-A10) D5 — circular bootstrap peak-hour spread, and the
small-sample rule: 'fewer than 5 season-years cannot on its own establish
adequacy, regardless of how narrow the spread looks.'

Round-2 finding this locks: three season-years all peaking at the same hour
would otherwise produce a false ZERO-spread 'adequate' reading, silently
authorising a verdict while leaving interannual uncertainty unmeasured.

Imports are guarded so a not-yet-implemented module fails these tests as a
genuine RED assertion, never a collection-time ImportError.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import polars as pl

try:
    from scripts.dhm_precip.coloc_bootstrap import (
        bootstrap_peak_hour_spread,
        per_season_hourly_means,
    )
except ImportError:
    bootstrap_peak_hour_spread = None  # type: ignore[assignment]
    per_season_hourly_means = None  # type: ignore[assignment]

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS

_STATION = Station("Lukla Airport")


def _three_identical_seasons_frame() -> pl.DataFrame:
    """Three season-years (2021-2023), each with the SAME single-day, one
    24h profile peaking at hour 23 in every year — i.e. the narrowest
    possible bootstrap spread (0h) a real dataset could produce."""
    rows: list[dict[str, object]] = []
    for year in (2021, 2022, 2023):
        for hour in range(24):
            value = 5.0 if hour == 23 else 0.1
            rows.append(
                {
                    "station": _STATION,
                    "timestamp": datetime(year, 7, 1) + timedelta(hours=hour),
                    "value_mm": value,
                }
            )
    return pl.DataFrame(rows)


class TestSmallSampleRuleCannotBeOverriddenByZeroSpread:
    def test_three_season_years_is_never_adequate_even_at_zero_spread(self) -> None:
        assert per_season_hourly_means is not None, (
            "per_season_hourly_means not implemented yet"
        )
        assert bootstrap_peak_hour_spread is not None, (
            "bootstrap_peak_hour_spread not implemented yet"
        )
        per_season = per_season_hourly_means(_three_identical_seasons_frame())
        result = bootstrap_peak_hour_spread(
            per_season,
            rng=random.Random(42),
            n_resamples=200,
            min_season_years_for_adequacy=DEFAULT_PARAMS.coloc_min_season_years_for_adequacy,
        )
        assert result.n_season_years == 3
        assert result.spread_hours == 0.0
        assert result.adequate_sample is False

    def test_five_season_years_can_be_adequate(self) -> None:
        assert per_season_hourly_means is not None, (
            "per_season_hourly_means not implemented yet"
        )
        assert bootstrap_peak_hour_spread is not None, (
            "bootstrap_peak_hour_spread not implemented yet"
        )
        rows: list[dict[str, object]] = []
        for year in (2019, 2020, 2021, 2022, 2023):
            for hour in range(24):
                value = 5.0 if hour == 23 else 0.1
                rows.append(
                    {
                        "station": _STATION,
                        "timestamp": datetime(year, 7, 1) + timedelta(hours=hour),
                        "value_mm": value,
                    }
                )
        per_season = per_season_hourly_means(pl.DataFrame(rows))
        result = bootstrap_peak_hour_spread(
            per_season,
            rng=random.Random(42),
            n_resamples=200,
            min_season_years_for_adequacy=DEFAULT_PARAMS.coloc_min_season_years_for_adequacy,
        )
        assert result.n_season_years == 5
        assert result.adequate_sample is True
