"""Plan 193 (M-A7) task T1 — masked diurnal profiles with exposure.

Seam tests: every assertion is on a VALUE actually produced (a mean, a
retained count, a raised exception), never on an argument passed to a
mock (`ma6_pairs`'s own "Seam tests" convention, reused here)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from scripts.dhm_precip.circular import circular_range_hours
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_estimands import ElevationBand
from scripts.dhm_precip.ma6_pairs import MaskedGaugeSeries
from scripts.dhm_precip.ma7_profiles import (
    OLANGCHUNGGOLA,
    BandDiurnalProfile,
    BandMembershipError,
    DuplicateBandMemberError,
    HourlyMean,
    MixedSeasonError,
    MixedSelectionParamsError,
    NoSeasonYearsError,
    StationDiurnalProfile,
    bootstrap_band_peak_hour,
    bootstrap_station_peak_hour,
    per_season_year_hourly_means,
    season_year_expr,
)
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
    year: int, *, values_by_hour: dict[int, float], day: int = 15
) -> list[dict[str, object]]:
    base = datetime(year, 7, day, tzinfo=UTC).replace(tzinfo=None)
    return [
        {"timestamp": base + timedelta(hours=h), "value_mm": v}
        for h, v in values_by_hour.items()
    ]


class TestHourlyMeanInvariant:
    def test_zero_retained_requires_none_mean(self) -> None:
        with pytest.raises(ValueError, match="disagree"):
            HourlyMean(hour=3, mean_value_mm=1.0, n_retained=0)

    def test_positive_retained_requires_a_mean(self) -> None:
        with pytest.raises(ValueError, match="disagree"):
            HourlyMean(hour=3, mean_value_mm=None, n_retained=2)

    def test_hour_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="hour must be 0-23"):
            HourlyMean(hour=24, mean_value_mm=None, n_retained=0)


class TestStationDiurnalProfileHourly:
    def test_hourly_means_and_counts_match_input(self) -> None:
        rows = _jjas_rows(2022, values_by_hour={5: 1.0, 6: 3.0}) + _jjas_rows(
            2023, values_by_hour={5: 3.0}
        )
        series = _series(_STATION_A, rows)
        profile = StationDiurnalProfile(series=series, season=Season.JJAS)
        by_hour = {h.hour: h for h in profile.hourly}
        assert len(by_hour) == 24
        assert by_hour[5].n_retained == 2
        assert by_hour[5].mean_value_mm == pytest.approx(2.0)
        assert by_hour[6].n_retained == 1
        assert by_hour[6].mean_value_mm == pytest.approx(3.0)

    def test_hour_with_zero_retained_is_present_not_omitted(self) -> None:
        series = _series(_STATION_A, _jjas_rows(2022, values_by_hour={5: 1.0}))
        profile = StationDiurnalProfile(series=series, season=Season.JJAS)
        by_hour = {h.hour: h for h in profile.hourly}
        # D8: every hour is present, even with zero retained data.
        assert by_hour[12].n_retained == 0
        assert by_hour[12].mean_value_mm is None

    def test_degenerate_season_has_no_peak_but_is_still_constructible(self) -> None:
        series = _series(_STATION_A, _jjas_rows(2022, values_by_hour={5: 1.0}))
        # DJF has zero data in a JJAS-only fixture — degenerate, not omitted.
        profile = StationDiurnalProfile(series=series, season=Season.DJF)
        assert profile.n_season_retained == 0
        assert profile.peak_hour is None
        assert all(h.n_retained == 0 for h in profile.hourly)

    def test_peak_hour_tie_break_favours_larger_hour(self) -> None:
        rows = _jjas_rows(2022, values_by_hour={3: 5.0, 9: 5.0, 4: 1.0})
        profile = StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        assert profile.peak_hour == 9

    def test_olangchunggola_carries_the_d7_note_other_stations_do_not(self) -> None:
        rows = _jjas_rows(2022, values_by_hour={3: 5.0})
        olang = StationDiurnalProfile(
            series=_series(OLANGCHUNGGOLA, rows), season=Season.JJAS
        )
        other = StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        assert olang.open_anomaly_note is not None
        assert "Olangchunggola" in olang.open_anomaly_note
        assert other.open_anomaly_note is None


class TestMaskedRowsNeverEnterTheProfile:
    """The plan's own regression: assert on the OUTPUT value, not on input
    provenance (structurally identical frames carry no masked/unmasked
    marker)."""

    def test_a_row_absent_from_the_masked_frame_cannot_move_the_mean(self) -> None:
        # "Unmasked" would have included an extreme 90mm outlier at hour 5 in
        # 2023 — the M-A3 mask would have removed it. The masked series
        # given to StationDiurnalProfile never carries that row at all.
        unmasked_hour5_2023 = 90.0
        masked_rows = _jjas_rows(2022, values_by_hour={5: 1.0, 6: 3.0}) + _jjas_rows(
            2023, values_by_hour={6: 3.0}
        )  # hour 5, 2023 is ABSENT — the mask removed it upstream.
        profile = StationDiurnalProfile(
            series=_series(_STATION_A, masked_rows), season=Season.JJAS
        )
        by_hour = {h.hour: h for h in profile.hourly}
        # Only the 2022 hour-5 value (1.0) is retained; the masked-out 2023
        # outlier never enters the mean.
        assert by_hour[5].n_retained == 1
        assert by_hour[5].mean_value_mm == pytest.approx(1.0)
        assert by_hour[5].mean_value_mm != pytest.approx(
            (1.0 + unmasked_hour5_2023) / 2
        )


class TestSeasonYearExpr:
    def test_djf_december_belongs_to_the_following_year(self) -> None:
        frame = pl.DataFrame(
            {
                "timestamp": [
                    datetime(2024, 12, 15),
                    datetime(2025, 1, 15),
                    datetime(2025, 2, 15),
                ]
            }
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("us")))
        result = frame.with_columns(
            season_year_expr(Season.DJF, DEFAULT_PARAMS).alias("season_year")
        )
        assert result["season_year"].to_list() == [2025, 2025, 2025]

    def test_jjas_season_year_is_the_calendar_year(self) -> None:
        frame = pl.DataFrame({"timestamp": [datetime(2024, 7, 15)]}).with_columns(
            pl.col("timestamp").cast(pl.Datetime("us"))
        )
        result = frame.with_columns(
            season_year_expr(Season.JJAS, DEFAULT_PARAMS).alias("season_year")
        )
        assert result["season_year"].to_list() == [2024]

    def test_lete_and_olangchunggola_djf_adequacy_measured(self) -> None:
        """D8's own measured warning: naive `dt.year()` grouping mislabels
        these two stations' DJF adequacy. Lete's DJF spans 5 distinct
        season-years (2021-2025) once December is correctly attributed to
        the FOLLOWING year; a bare calendar-year grouping would only see 4
        (December of the 5th year never gets its own bucket)."""
        # One December-only DJF observation per season-year 2021-2025 (5
        # years), i.e. Decembers 2020-2024 (season-year = December's
        # calendar year + 1).
        decembers = [datetime(y, 12, 1) for y in (2020, 2021, 2022, 2023, 2024)]
        rows = [{"station": "Lete", "timestamp": d, "value_mm": 1.0} for d in decembers]
        frame = pl.DataFrame(rows).with_columns(
            pl.col("timestamp").cast(pl.Datetime("us"))
        )
        series = MaskedGaugeSeries(frame=frame)
        per_year = per_season_year_hourly_means(
            series.frame, Season.DJF, DEFAULT_PARAMS
        )
        assert sorted(per_year["season_year"].unique().to_list()) == [
            2021,
            2022,
            2023,
            2024,
            2025,
        ]


class TestBootstrapStationPeakHour:
    def test_deterministic_with_the_same_seed(self) -> None:
        rows = []
        for year in range(2019, 2025):
            rows += _jjas_rows(year, values_by_hour={22: 5.0, 23: 6.0, 3: 0.2})
        profile = StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        r1 = bootstrap_station_peak_hour(profile, rng=random.Random(7), n_resamples=200)
        r2 = bootstrap_station_peak_hour(profile, rng=random.Random(7), n_resamples=200)
        assert r1.resampled_peak_hours == r2.resampled_peak_hours
        assert r1.spread_hours == r2.spread_hours

    def test_different_seed_gives_a_different_resample_sequence(self) -> None:
        rows = []
        for year in range(2019, 2025):
            rows += _jjas_rows(
                year,
                values_by_hour={h: float((h * 7 + year) % 11) + 0.1 for h in range(24)},
            )
        profile = StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        r1 = bootstrap_station_peak_hour(profile, rng=random.Random(1), n_resamples=500)
        r2 = bootstrap_station_peak_hour(profile, rng=random.Random(2), n_resamples=500)
        assert r1.resampled_peak_hours != r2.resampled_peak_hours

    def test_adequate_sample_gates_on_n_season_years_not_spread(self) -> None:
        rows = []
        for year in range(2019, 2022):  # 3 season-years — below the bar (5)
            rows += _jjas_rows(year, values_by_hour={23: 5.0, 3: 0.1})
        profile = StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        result = bootstrap_station_peak_hour(
            profile, rng=random.Random(0), n_resamples=200
        )
        assert result.n_season_years == 3
        assert result.adequate_sample is False

        rows5 = []
        for year in range(2019, 2024):  # 5 season-years — at the bar
            rows5 += _jjas_rows(year, values_by_hour={23: 5.0, 3: 0.1})
        profile5 = StationDiurnalProfile(
            series=_series(_STATION_A, rows5), season=Season.JJAS
        )
        result5 = bootstrap_station_peak_hour(
            profile5, rng=random.Random(0), n_resamples=200
        )
        assert result5.n_season_years == 5
        assert result5.adequate_sample is True

    def test_zero_season_years_raises_not_silently_reports(self) -> None:
        profile = StationDiurnalProfile(
            series=_series(_STATION_A, _jjas_rows(2022, values_by_hour={5: 1.0})),
            season=Season.DJF,
        )
        with pytest.raises(NoSeasonYearsError):
            bootstrap_station_peak_hour(profile, rng=random.Random(0), n_resamples=50)


class TestPeakHourBootstrapIsCircular:
    """The measured defect: hour-of-day is circular, so a distribution of
    resampled peak hours straddling midnight must report a SMALL spread —
    never a near-24h one, the number a linear percentile interval would
    have produced."""

    def _straddling_midnight_profile(self) -> StationDiurnalProfile:
        # Five season-years, each with a single dominant hour (10.0 mm)
        # against a flat 0.1 mm background at every other hour of day.
        # The five dominant hours are exactly the straddling-midnight set
        # from the measured defect: 23, 0, 1, 22, 2. Every other hour of
        # day never accumulates more than 0.1 * 5 = 0.5 mm summed across
        # any resample draw, so a resampled peak hour can ONLY ever be one
        # of these five — a fact used below to bound the spread exactly,
        # independent of which seed is used.
        peak_hour_by_year = {2019: 23, 2020: 0, 2021: 1, 2022: 22, 2023: 2}
        rows: list[dict[str, object]] = []
        for year, peak_hour in peak_hour_by_year.items():
            values_by_hour = {h: 0.1 for h in range(24)}
            values_by_hour[peak_hour] = 10.0
            rows += _jjas_rows(year, values_by_hour=values_by_hour)
        return StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )

    def test_straddling_midnight_resamples_report_a_small_circular_spread(
        self,
    ) -> None:
        profile = self._straddling_midnight_profile()
        result = bootstrap_station_peak_hour(
            profile, rng=random.Random(0), n_resamples=500
        )
        # Every resampled peak hour is confined to the straddling-midnight
        # set by construction (see docstring above), so the circular range
        # over ANY non-empty subset of {22, 23, 0, 1, 2} is at most 4.0h —
        # the range of the full set — never anywhere near 24h.
        assert set(result.resampled_peak_hours) <= {22, 23, 0, 1, 2}
        assert result.spread_hours <= 4.0
        # The number a linear interval construction would have produced
        # instead, for the same resamples — the defect this locks.
        naive_linear_range = max(result.resampled_peak_hours) - min(
            result.resampled_peak_hours
        )
        assert naive_linear_range >= 20
        assert result.spread_hours < naive_linear_range

    def test_spread_is_computed_over_the_resamples_not_the_point_estimate(
        self,
    ) -> None:
        profile = self._straddling_midnight_profile()
        result = bootstrap_station_peak_hour(
            profile, rng=random.Random(0), n_resamples=500
        )
        # A tie among all five dominant hours (each contributes the same
        # season-total mass) breaks toward the LARGEST hour, so the point
        # estimate is 23 — a single hour, spread 0. The reported spread
        # must come from the resampled distribution, not from repeating
        # this point estimate.
        assert profile.peak_hour == 23
        assert result.peak_hour == 23
        assert len(set(result.resampled_peak_hours)) > 1
        assert result.spread_hours == circular_range_hours(
            [float(h) for h in result.resampled_peak_hours]
        )
        assert result.spread_hours > 0.0


def _band_members(
    *, station_elev_m: dict[Station, float], per_station_rows: dict[Station, list[dict]]
) -> BandDiurnalProfile:
    members = tuple(
        StationDiurnalProfile(series=_series(station, rows), season=Season.JJAS)
        for station, rows in per_station_rows.items()
    )
    return BandDiurnalProfile(
        band=ElevationBand.B700_2000M, members=members, station_elev_m=station_elev_m
    )


class TestBandDiurnalProfile:
    _ELEV = {_STATION_A: 1000.0, _STATION_B: 1500.0}

    def test_station_equal_differs_from_pooled_under_unequal_retention(self) -> None:
        # Station A: 90 retained hours (all within JJAS) at value 10 at hour 6.
        # Station B: 1 retained hour at value 0 at hour 6.
        # Pooled (retention-weighted) is dragged toward A; station-equal
        # treats both stations identically regardless of sample size.
        n_a = 90  # July 1 + 89 days stays inside September (JJAS-safe)
        rows_a = [
            {"timestamp": datetime(2022, 7, 1) + timedelta(days=i), "value_mm": 10.0}
            for i in range(n_a)
        ]
        rows_b = [{"timestamp": datetime(2022, 7, 1), "value_mm": 0.0}]
        # Force both onto hour 6 explicitly.
        rows_a = [{**r, "timestamp": r["timestamp"].replace(hour=6)} for r in rows_a]
        rows_b = [{**r, "timestamp": r["timestamp"].replace(hour=6)} for r in rows_b]
        band = _band_members(
            station_elev_m=self._ELEV,
            per_station_rows={_STATION_A: rows_a, _STATION_B: rows_b},
        )
        station_equal = {h.hour: h.mean_value_mm for h in band.station_equal_hourly}[6]
        pooled = {h.hour: h.mean_value_mm for h in band.pooled_hourly}[6]
        assert station_equal == pytest.approx((10.0 + 0.0) / 2)
        assert pooled == pytest.approx((n_a * 10.0 + 1 * 0.0) / (n_a + 1))
        assert station_equal != pytest.approx(pooled)

    def test_duplicate_station_rejected(self) -> None:
        rows = _jjas_rows(2022, values_by_hour={5: 1.0})
        member = StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        with pytest.raises(DuplicateBandMemberError):
            BandDiurnalProfile(
                band=ElevationBand.B700_2000M,
                members=(member, member),
                station_elev_m=self._ELEV,
            )

    def test_member_outside_declared_band_rejected(self) -> None:
        rows = _jjas_rows(2022, values_by_hour={5: 1.0})
        member = StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        with pytest.raises(BandMembershipError):
            BandDiurnalProfile(
                band=ElevationBand.ABOVE_3000M,  # A is 1000m — wrong band
                members=(member,),
                station_elev_m=self._ELEV,
            )

    def test_mixed_season_members_rejected(self) -> None:
        rows = _jjas_rows(2022, values_by_hour={5: 1.0})
        member_jjas = StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        member_djf = StationDiurnalProfile(
            series=_series(_STATION_B, rows), season=Season.DJF
        )
        with pytest.raises(MixedSeasonError):
            BandDiurnalProfile(
                band=ElevationBand.B700_2000M,
                members=(member_jjas, member_djf),
                station_elev_m=self._ELEV,
            )

    def test_mixed_params_members_rejected(self) -> None:
        from dataclasses import replace

        rows = _jjas_rows(2022, values_by_hour={5: 1.0})
        member_default = StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        member_other_params = StationDiurnalProfile(
            series=_series(_STATION_B, rows),
            season=Season.JJAS,
            params=replace(DEFAULT_PARAMS, wet_threshold_mm_per_h=0.5),
        )
        with pytest.raises(MixedSelectionParamsError):
            BandDiurnalProfile(
                band=ElevationBand.B700_2000M,
                members=(member_default, member_other_params),
                station_elev_m=self._ELEV,
            )

    def test_bootstrap_adequacy_uses_intersection_of_season_years_not_union(
        self,
    ) -> None:
        # Station A has 5 season-years (2019-2023); station B only shares 2
        # of those (2019, 2020) plus 2 more of its own (2024, 2025) — union
        # is 7, intersection is 2.
        rows_a = []
        for year in range(2019, 2024):
            rows_a += _jjas_rows(year, values_by_hour={6: 5.0})
        rows_b = []
        for year in (2019, 2020, 2024, 2025):
            rows_b += _jjas_rows(year, values_by_hour={6: 5.0})
        band = _band_members(
            station_elev_m=self._ELEV,
            per_station_rows={_STATION_A: rows_a, _STATION_B: rows_b},
        )
        result = bootstrap_band_peak_hour(band, rng=random.Random(0), n_resamples=100)
        assert result.n_season_years == 2  # intersection, not the union (7)
        assert result.adequate_sample is False


class TestBootstrapDeterminismCircularSpread:
    def test_circular_spread_is_reproducible_across_two_in_process_runs(
        self,
    ) -> None:
        rows = []
        for year in range(2018, 2026):
            rows += _jjas_rows(
                year, values_by_hour={h: float((h * 3 + year) % 13) for h in range(24)}
            )
        profile = StationDiurnalProfile(
            series=_series(_STATION_A, rows), season=Season.JJAS
        )
        a = bootstrap_station_peak_hour(
            profile, rng=random.Random(99), n_resamples=2000
        )
        b = bootstrap_station_peak_hour(
            profile, rng=random.Random(99), n_resamples=2000
        )
        assert a.spread_hours == b.spread_hours
        assert np.array_equal(
            np.array(a.resampled_peak_hours), np.array(b.resampled_peak_hours)
        )
