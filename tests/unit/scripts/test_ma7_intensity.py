"""Plan 193 (M-A7) task T2 — wet-hour intensity distributions.

Seam tests: every assertion is on a VALUE actually produced, never on an
argument passed to a mock (`ma6_pairs`'s own convention, reused here)."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_estimands import ElevationBand
from scripts.dhm_precip.ma6_pairs import MaskedGaugeSeries
from scripts.dhm_precip.ma7_intensity import (
    BandIntensityDistribution,
    BandMembershipError,
    DuplicateBandMemberError,
    MixedSeasonError,
    MixedSelectionParamsError,
    StationIntensityDistribution,
    bootstrap_band_quantile,
    bootstrap_station_quantile,
)
from scripts.dhm_precip.ma7_profiles import NoSeasonYearsError
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.seasons import Season

_STATION_A = Station("Alpha")
_STATION_B = Station("Beta")


def _series(station: Station, rows: list[dict[str, object]]) -> MaskedGaugeSeries:
    frame = pl.DataFrame(rows).with_columns(
        pl.lit(str(station)).alias("station"),
        pl.col("timestamp").cast(pl.Datetime("us")),
    )
    return MaskedGaugeSeries(frame=frame.select("station", "timestamp", "value_mm"))


def _jjas_rows(
    year: int, values: list[float], *, start_hour: int = 0
) -> list[dict[str, object]]:
    base = datetime(year, 7, 1, start_hour)
    return [
        {"timestamp": base + timedelta(hours=i), "value_mm": v}
        for i, v in enumerate(values)
    ]


class TestStationIntensityDistributionExposure:
    def test_wet_and_total_counts_and_fraction(self) -> None:
        # 0.2 mm/h floor (default params): 2 wet, 1 dry, 1 exactly-zero.
        rows = _jjas_rows(2022, [0.0, 0.1, 0.5, 1.2])
        dist = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        assert dist.n_total_retained == 4
        assert dist.n_wet_retained == 2
        assert dist.wet_hour_fraction == pytest.approx(0.5)

    def test_degenerate_season_returns_none_not_a_crash(self) -> None:
        rows = _jjas_rows(2022, [1.0])
        dist = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.DJF
        )
        assert dist.n_total_retained == 0
        assert dist.wet_hour_fraction is None
        assert dist.q50_mm_per_h is None
        assert dist.total_retained_mass_mm is None


class TestMassStatisticIsUnthresholded:
    """The plan's own required test: the VALUE of the mass statistic equals
    the unthresholded computation and differs from the thresholded one."""

    def test_total_mass_includes_sub_threshold_and_dry_hours(self) -> None:
        # Values: two below the 0.2 mm/h floor (0.05, 0.1), one dry (0.0),
        # one wet (3.0). Unthresholded total = 3.15; thresholded (wet-only)
        # total would be 3.0.
        rows = _jjas_rows(2022, [0.0, 0.05, 0.1, 3.0])
        dist = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        unthresholded_total = 0.0 + 0.05 + 0.1 + 3.0
        thresholded_total = 3.0  # only the wet (>=0.2) hour
        assert dist.total_retained_mass_mm == pytest.approx(unthresholded_total)
        assert dist.total_retained_mass_mm != pytest.approx(thresholded_total)
        assert dist.n_total_retained == 4  # carries its own n (D4 pinned)


class TestBodyTailQuantiles:
    def test_q50_and_q99_are_computed_on_the_wet_population(self) -> None:
        # 10 dry hours (0.0) + wet hours 1..10 mm — q50/q99 must reflect
        # only the wet population, not be dragged toward 0 by the dry hours.
        rows = _jjas_rows(2022, [0.0] * 10 + [float(v) for v in range(1, 11)])
        dist = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        assert dist.n_wet_retained == 10
        assert dist.q50_mm_per_h == pytest.approx(5.5)
        assert dist.q99_mm_per_h is not None
        assert dist.q99_mm_per_h > dist.q50_mm_per_h  # tail strictly above body


class TestBootstrapStationQuantile:
    def test_deterministic_with_the_same_seed(self) -> None:
        rows = []
        for year in range(2019, 2025):
            rows += _jjas_rows(year, [float(v) for v in range(1, 11)])
        dist = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        r1 = bootstrap_station_quantile(
            dist, quantile=0.99, rng=random.Random(3), n_resamples=200
        )
        r2 = bootstrap_station_quantile(
            dist, quantile=0.99, rng=random.Random(3), n_resamples=200
        )
        assert r1.resampled_values_mm_per_h == r2.resampled_values_mm_per_h
        assert r1.ci_low_mm_per_h == r2.ci_low_mm_per_h
        assert r1.ci_high_mm_per_h == r2.ci_high_mm_per_h

    def test_different_seed_gives_a_different_resample_sequence(self) -> None:
        rows = []
        for year in range(2019, 2025):
            rows += _jjas_rows(
                year, [float((v * 7 + year) % 13) + 0.5 for v in range(1, 15)]
            )
        dist = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        r1 = bootstrap_station_quantile(
            dist, quantile=0.5, rng=random.Random(1), n_resamples=500
        )
        r2 = bootstrap_station_quantile(
            dist, quantile=0.5, rng=random.Random(2), n_resamples=500
        )
        assert r1.resampled_values_mm_per_h != r2.resampled_values_mm_per_h

    def test_adequate_sample_gates_on_n_season_years(self) -> None:
        rows = []
        for year in range(2019, 2022):  # 3 season-years
            rows += _jjas_rows(year, [1.0, 2.0, 3.0])
        dist = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        result = bootstrap_station_quantile(
            dist, quantile=0.5, rng=random.Random(0), n_resamples=100
        )
        assert result.n_season_years == 3
        assert result.adequate_sample is False

    def test_zero_season_years_raises(self) -> None:
        dist = StationIntensityDistribution(
            series=_series(_STATION_A, _jjas_rows(2022, [1.0])), season=Season.DJF
        )
        with pytest.raises(NoSeasonYearsError):
            bootstrap_station_quantile(
                dist, quantile=0.5, rng=random.Random(0), n_resamples=50
            )


def _band(
    *, station_elev_m: dict[Station, float], per_station_rows: dict[Station, list[dict]]
) -> BandIntensityDistribution:
    members = tuple(
        StationIntensityDistribution(series=_series(station, rows), season=Season.JJAS)
        for station, rows in per_station_rows.items()
    )
    return BandIntensityDistribution(
        band=ElevationBand.B700_2000M, members=members, station_elev_m=station_elev_m
    )


class TestBandIntensityDistribution:
    _ELEV = {_STATION_A: 1000.0, _STATION_B: 1500.0}

    def test_station_equal_differs_from_pooled_under_unequal_sample_size(self) -> None:
        # Station A: 1000 wet observations all at 1.0 mm/h.
        # Station B: 1 wet observation at 100.0 mm/h.
        # Pooled quantiles are dominated by A's sample size; station-equal
        # gives B's single value equal total probability mass to A's 1000.
        rows_a = _jjas_rows(2022, [1.0] * 1000)
        rows_b = _jjas_rows(2022, [100.0])
        band = _band(
            station_elev_m=self._ELEV,
            per_station_rows={_STATION_A: rows_a, _STATION_B: rows_b},
        )
        station_equal_q99 = band.station_equal_q99_mm_per_h
        pooled_q99 = band.pooled_q99_mm_per_h
        assert station_equal_q99 is not None
        assert pooled_q99 is not None
        # Station-equal must be pulled substantially toward B's 100.0 value
        # (B holds half the probability mass); pooled barely moves (B is
        # 1/1001 of the observations).
        assert station_equal_q99 > pooled_q99
        assert pooled_q99 == pytest.approx(1.0, abs=1.0)

    def test_duplicate_station_rejected(self) -> None:
        rows = _jjas_rows(2022, [1.0])
        member = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        with pytest.raises(DuplicateBandMemberError):
            BandIntensityDistribution(
                band=ElevationBand.B700_2000M,
                members=(member, member),
                station_elev_m=self._ELEV,
            )

    def test_member_outside_declared_band_rejected(self) -> None:
        rows = _jjas_rows(2022, [1.0])
        member = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        with pytest.raises(BandMembershipError):
            BandIntensityDistribution(
                band=ElevationBand.ABOVE_3000M,
                members=(member,),
                station_elev_m=self._ELEV,
            )

    def test_mixed_season_members_rejected(self) -> None:
        rows = _jjas_rows(2022, [1.0])
        member_jjas = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        member_djf = StationIntensityDistribution(
            series=_series(_STATION_B, rows), season=Season.DJF
        )
        with pytest.raises(MixedSeasonError):
            BandIntensityDistribution(
                band=ElevationBand.B700_2000M,
                members=(member_jjas, member_djf),
                station_elev_m=self._ELEV,
            )

    def test_mixed_params_members_rejected(self) -> None:
        rows = _jjas_rows(2022, [1.0])
        member_default = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        member_other = StationIntensityDistribution(
            series=_series(_STATION_B, rows),
            season=Season.JJAS,
            params=replace(DEFAULT_PARAMS, wet_threshold_mm_per_h=0.5),
        )
        with pytest.raises(MixedSelectionParamsError):
            BandIntensityDistribution(
                band=ElevationBand.B700_2000M,
                members=(member_default, member_other),
                station_elev_m=self._ELEV,
            )

    def test_bootstrap_adequacy_uses_union_of_season_years_not_intersection(
        self,
    ) -> None:
        # Union is 7 (2019-2025), intersection is 2 (2019, 2020). The band
        # point estimate already draws on each station's FULL record, so
        # adequacy (and the resample) must be measured against the union
        # (Plan 193 D9 correction).
        rows_a = []
        for year in range(2019, 2024):  # 5 season-years
            rows_a += _jjas_rows(year, [1.0, 2.0])
        rows_b = []
        for year in (2019, 2020, 2024, 2025):  # overlaps A in only 2 years
            rows_b += _jjas_rows(year, [1.0, 2.0])
        band = _band(
            station_elev_m=self._ELEV,
            per_station_rows={_STATION_A: rows_a, _STATION_B: rows_b},
        )
        result = bootstrap_band_quantile(
            band, quantile=0.5, rng=random.Random(0), n_resamples=100
        )
        assert result.n_season_years == 7  # union, not the intersection (2)
        assert result.adequate_sample is True

    def test_a_short_record_member_does_not_collapse_the_union(self) -> None:
        # A and B span 8 overlapping years each (2015-2022 / 2016-2023); a
        # THIRD member (Gamma) has just one season-year (2020, shared with
        # both). Under the old intersection mechanics the whole band would
        # collapse to Gamma's single year. Under the union, Gamma's short
        # record must not drag the other two members down.
        station_c = Station("Gamma")
        rows_a = []
        for year in range(2015, 2023):  # 2015..2022, 8 years
            rows_a += _jjas_rows(year, [1.0, 2.0])
        rows_b = []
        for year in range(2016, 2024):  # 2016..2023, 8 years
            rows_b += _jjas_rows(year, [1.0, 2.0])
        rows_c = _jjas_rows(2020, [1.0, 2.0])
        elev = {_STATION_A: 1000.0, _STATION_B: 1500.0, station_c: 1800.0}
        band = _band(
            station_elev_m=elev,
            per_station_rows={
                _STATION_A: rows_a,
                _STATION_B: rows_b,
                station_c: rows_c,
            },
        )
        result = bootstrap_band_quantile(
            band, quantile=0.5, rng=random.Random(0), n_resamples=100
        )
        # Union of {2015..2022} | {2016..2023} | {2020} == {2015..2023}: 9.
        assert result.n_season_years == 9
        assert result.adequate_sample is True


class TestExposureTravelsWithEveryDistribution:
    """2026-08-27 amendment — every distribution carries its retained
    wet-hour count AND its total retained-hour count."""

    def test_station_distribution_carries_both_counts(self) -> None:
        rows = _jjas_rows(2022, [0.0, 0.1, 1.0, 2.0])
        dist = StationIntensityDistribution(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        assert dist.n_total_retained == 4
        assert dist.n_wet_retained == 2

    def test_band_distribution_carries_both_counts_summed_over_members(self) -> None:
        rows_a = _jjas_rows(2022, [0.0, 1.0])
        rows_b = _jjas_rows(2022, [2.0])
        band = _band(
            station_elev_m={_STATION_A: 1000.0, _STATION_B: 1500.0},
            per_station_rows={_STATION_A: rows_a, _STATION_B: rows_b},
        )
        assert band.n_total_retained == 3
        assert band.n_wet_retained == 2
