"""Plan 184 (M-A6) task T3 — the estimands.

Seam tests: every assertion is on a VALUE actually produced (a mean, a
count, a raised exception), never on an argument passed to a mock — the
same discipline `test_ma6_pairs.py` states for T1.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_estimands import (
    CategoricalGrainRefusedError,
    ConditionalAccumulatedDifference,
    ElevationBand,
    EmptySubsetError,
    EstimandSubsetTypeError,
    MatchedHourMeanDifference,
    RetentionConditionality,
    Scale,
    WetHourConditionalIntensityBias,
    assign_elevation_band,
    band_matched_hour_mean_difference,
    categorical_scores,
    conditional_accumulated_difference,
    matched_hour_mean_difference,
    scale_subset,
    wet_hour_conditional_intensity_bias,
    wet_scale_subset,
)
from scripts.dhm_precip.ma6_pairs import MaskedGaugeSeries, PairedSeries, subset
from scripts.dhm_precip.params import DEFAULT_PARAMS


def _paired_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


def _make_paired_series(
    station: Station,
    start: datetime,
    n_hours: int,
    gauge: list[float],
    era5: list[float],
) -> PairedSeries:
    timestamps = [start + timedelta(hours=i) for i in range(n_hours)]
    frame = _paired_frame(
        [
            {
                "timestamp": timestamps[i],
                "gauge_value_mm": gauge[i],
                "era5_nearest_mm_per_h": era5[i],
            }
            for i in range(n_hours)
        ]
    )
    return PairedSeries(station=station, frame=frame)


class TestAssignElevationBand:
    def test_band_edges_match_d4a(self) -> None:
        assert assign_elevation_band(699.9) is ElevationBand.BELOW_700M
        assert assign_elevation_band(700.0) is ElevationBand.B700_2000M
        assert assign_elevation_band(1999.9) is ElevationBand.B700_2000M
        assert assign_elevation_band(2000.0) is ElevationBand.B2000_3000M
        assert assign_elevation_band(2999.9) is ElevationBand.B2000_3000M
        assert assign_elevation_band(3000.0) is ElevationBand.ABOVE_3000M


class TestMatchedHourMeanDifference:
    def test_mean_over_all_matched_hours_wet_and_dry(self) -> None:
        # July (JJAS): gauge - era5 = (2-1, 4-1, 0-0, 6-2) -> mean 2.0
        station = Station("A")
        paired = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            4,
            gauge=[2.0, 4.0, 0.0, 6.0],
            era5=[1.0, 1.0, 0.0, 2.0],
        )
        jjas = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)

        result = matched_hour_mean_difference(jjas, station=station, scale=Scale.JJAS)

        assert result.n == 4
        assert result.mean_difference_mm_per_h == pytest.approx(2.0)

    def test_djf_scale_only_sees_djf_months(self) -> None:
        station = Station("A")
        # 2 JJAS hours + 3 DJF hours (Dec, Jan, Feb).
        frame = _paired_frame(
            [
                {
                    "timestamp": datetime(2024, 7, 1, 0),
                    "gauge_value_mm": 5.0,
                    "era5_nearest_mm_per_h": 0.0,
                },
                {
                    "timestamp": datetime(2024, 7, 1, 1),
                    "gauge_value_mm": 5.0,
                    "era5_nearest_mm_per_h": 0.0,
                },
                {
                    "timestamp": datetime(2024, 12, 1, 0),
                    "gauge_value_mm": 1.0,
                    "era5_nearest_mm_per_h": 1.0,
                },
                {
                    "timestamp": datetime(2025, 1, 1, 0),
                    "gauge_value_mm": 2.0,
                    "era5_nearest_mm_per_h": 1.0,
                },
                {
                    "timestamp": datetime(2025, 2, 1, 0),
                    "gauge_value_mm": 3.0,
                    "era5_nearest_mm_per_h": 1.0,
                },
            ]
        )
        paired = PairedSeries(station=station, frame=frame)

        djf = scale_subset(paired, scale=Scale.DJF, params=DEFAULT_PARAMS)
        result = matched_hour_mean_difference(djf, station=station, scale=Scale.DJF)

        assert result.n == 3
        # (1-1) + (2-1) + (3-1) = 0+1+2 = 3, mean = 1.0
        assert result.mean_difference_mm_per_h == pytest.approx(1.0)

    def test_empty_subset_raises(self) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station, datetime(2024, 7, 1, 0), 1, gauge=[1.0], era5=[1.0]
        )
        djf = scale_subset(paired, scale=Scale.DJF, params=DEFAULT_PARAMS)

        with pytest.raises(EmptySubsetError):
            matched_hour_mean_difference(djf, station=station, scale=Scale.DJF)

    def test_daily_scale_is_not_supported(self) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station, datetime(2024, 7, 1, 0), 1, gauge=[1.0], era5=[1.0]
        )
        daily = scale_subset(paired, scale=Scale.DAILY, params=DEFAULT_PARAMS)

        with pytest.raises(Exception):  # noqa: B017, PT011 — ScaleNotSupportedError, __post_init__
            matched_hour_mean_difference(daily, station=station, scale=Scale.DAILY)


class TestWetHourConditionalIntensityBias:
    def test_restricted_to_gauge_wet_hours_only(self) -> None:
        station = Station("A")
        # wet_threshold_mm_per_h default 0.2. Hours: gauge 0.0 (dry, excluded),
        # gauge 1.0 (wet, era5 0.5 -> diff 0.5), gauge 3.0 (wet, era5 1.0 -> diff 2.0).
        paired = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            3,
            gauge=[0.0, 1.0, 3.0],
            era5=[0.0, 0.5, 1.0],
        )
        wet_jjas = wet_scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)

        result = wet_hour_conditional_intensity_bias(
            wet_jjas, station=station, scale=Scale.JJAS
        )

        assert result.n == 2
        assert result.mean_intensity_bias_mm_per_h == pytest.approx((0.5 + 2.0) / 2)

    def test_n_is_smaller_than_the_unconditioned_scale_subsets_n(self) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            3,
            gauge=[0.0, 1.0, 3.0],
            era5=[0.0, 0.5, 1.0],
        )
        all_jjas = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        wet_jjas = wet_scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)

        assert all_jjas.n_common_retained == 3
        assert wet_jjas.n_common_retained == 2
        assert wet_jjas.n_common_retained != all_jjas.n_common_retained, (
            "gauge-wet exposure must be a DIFFERENT count from the "
            "unconditioned scale subset's — conflating them is the "
            "mismatched-population failure mode this track keeps hitting"
        )


class TestConditionalAccumulatedDifference:
    def test_jjas_scale_forms_a_single_whole_season_bucket(self) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            4,
            gauge=[1.0, 2.0, 3.0, 4.0],
            era5=[0.5, 0.5, 0.5, 0.5],
        )
        jjas = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)

        result = conditional_accumulated_difference(
            jjas, station=station, scale=Scale.JJAS
        )

        assert result.n == 4
        assert result.n_periods == 1
        assert result.total_difference_mm == pytest.approx(10.0 - 2.0)

    def test_daily_scale_forms_one_bucket_per_calendar_day(self) -> None:
        station = Station("A")
        # Day 1: hours 22, 23 (2 hours). Day 2: hours 0, 1 (2 hours).
        start = datetime(2024, 7, 1, 22)
        timestamps = [start + timedelta(hours=i) for i in range(4)]
        frame = _paired_frame(
            [
                {
                    "timestamp": timestamps[i],
                    "gauge_value_mm": v,
                    "era5_nearest_mm_per_h": e,
                }
                for i, (v, e) in enumerate(
                    zip([1.0, 1.0, 2.0, 2.0], [0.5, 0.5, 1.0, 1.0], strict=True)
                )
            ]
        )
        paired = PairedSeries(station=station, frame=frame)
        all_hours = scale_subset(paired, scale=Scale.DAILY, params=DEFAULT_PARAMS)

        result = conditional_accumulated_difference(
            all_hours, station=station, scale=Scale.DAILY
        )

        assert result.n == 4
        assert result.n_periods == 2
        labels = {p.period_label for p in result.periods}
        assert labels == {"2024-07-01", "2024-07-02"}
        day1 = next(p for p in result.periods if p.period_label == "2024-07-01")
        assert day1.n_hours == 2
        assert day1.gauge_sum_mm == pytest.approx(2.0)
        assert day1.era5_sum_mm == pytest.approx(1.0)

    def test_periods_is_not_an_independently_settable_field(self) -> None:
        # Finding 1 (Plan 184 T3 independent review, 2026-08-21): the OLD
        # version of this test hand-constructed a ConditionalAccumulated-
        # Difference with a `periods=` kwarg summing to 999 hours against a
        # subset carrying only 3, and asserted the size-mismatch runtime
        # check raised. That check is gone because the field it guarded is
        # gone: `periods` is now a `@property` computed from `subset` and
        # `scale` (see `_compute_periods`), so there is no `periods=`
        # constructor argument to mismatch in the first place — a stronger
        # guarantee than a runtime check, since it also closes the
        # EQUAL-size case the old check could not catch (see
        # TestValueCannotBeAttachedFromADifferentPopulation below).
        station = Station("A")
        paired = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            3,
            gauge=[1.0, 1.0, 1.0],
            era5=[0.0, 0.0, 0.0],
        )
        real_subset = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        assert real_subset.n_common_retained == 3

        with pytest.raises(TypeError, match="periods"):
            ConditionalAccumulatedDifference(
                station=station,
                scale=Scale.JJAS,
                subset=real_subset,
                periods=(),  # type: ignore[call-arg]
            )

    def test_rejects_a_subset_that_is_not_a_real_pairedretainedsubset(self) -> None:
        with pytest.raises(EstimandSubsetTypeError):
            ConditionalAccumulatedDifference(
                station=Station("A"),
                scale=Scale.JJAS,
                subset=42,  # type: ignore[arg-type]
            )

    def test_empty_subset_raises(self) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station, datetime(2024, 7, 1, 0), 1, gauge=[1.0], era5=[1.0]
        )
        djf = scale_subset(paired, scale=Scale.DJF, params=DEFAULT_PARAMS)

        with pytest.raises(EmptySubsetError):
            conditional_accumulated_difference(djf, station=station, scale=Scale.DJF)


class TestCategoricalScoresRefusesSeasonGrain:
    """D12 — the vacuity trap. This is the required Verify-line test: a
    JJAS/DJF-grain categorical score must be REFUSED, not computed."""

    @staticmethod
    def _jjas_accumulated() -> ConditionalAccumulatedDifference:
        station = Station("A")
        paired = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            4,
            gauge=[1.0, 2.0, 3.0, 4.0],
            era5=[0.5, 0.5, 0.5, 0.5],
        )
        jjas = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        return conditional_accumulated_difference(
            jjas, station=station, scale=Scale.JJAS
        )

    def test_jjas_grain_is_refused(self) -> None:
        accumulated = self._jjas_accumulated()

        with pytest.raises(CategoricalGrainRefusedError):
            categorical_scores(accumulated, params=DEFAULT_PARAMS)

    def test_djf_grain_is_also_refused(self) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station,
            datetime(2024, 12, 1, 0),
            2,
            gauge=[1.0, 2.0],
            era5=[0.5, 0.5],
        )
        djf = scale_subset(paired, scale=Scale.DJF, params=DEFAULT_PARAMS)
        accumulated = conditional_accumulated_difference(
            djf, station=station, scale=Scale.DJF
        )

        with pytest.raises(CategoricalGrainRefusedError):
            categorical_scores(accumulated, params=DEFAULT_PARAMS)

    def test_daily_grain_is_accepted(self) -> None:
        # Sanity: the refusal is specific to JJAS/DJF, not everything.
        station = Station("A")
        start = datetime(2024, 7, 1, 0)
        n_hours = 48
        timestamps = [start + timedelta(hours=i) for i in range(n_hours)]
        # Day 1 wet on both sides, day 2 dry on both sides.
        gauge = [1.0] * 24 + [0.0] * 24
        era5 = [1.0] * 24 + [0.0] * 24
        frame = _paired_frame(
            [
                {
                    "timestamp": timestamps[i],
                    "gauge_value_mm": gauge[i],
                    "era5_nearest_mm_per_h": era5[i],
                }
                for i in range(n_hours)
            ]
        )
        paired = PairedSeries(station=station, frame=frame)
        daily = scale_subset(paired, scale=Scale.DAILY, params=DEFAULT_PARAMS)
        accumulated = conditional_accumulated_difference(
            daily, station=station, scale=Scale.DAILY
        )

        scores = categorical_scores(accumulated, params=DEFAULT_PARAMS)

        assert scores.n_periods == 2
        assert scores.pod == pytest.approx(1.0)
        assert scores.far == pytest.approx(0.0)
        assert scores.csi == pytest.approx(1.0)


class TestElevationBandEstimand:
    def test_band_value_is_the_unweighted_mean_of_member_station_values(self) -> None:
        # Station A: 40 gauge hours (high retention). Station B: 4 hours
        # (low retention). Pooling raw hours would let A dominate; the
        # station-equal rule must weight A and B equally regardless.
        station_a = Station("A")
        station_b = Station("B")
        paired_a = _make_paired_series(
            station_a,
            datetime(2024, 7, 1, 0),
            40,
            gauge=[2.0] * 40,
            era5=[0.0] * 40,
        )
        paired_b = _make_paired_series(
            station_b,
            datetime(2024, 7, 1, 0),
            4,
            gauge=[10.0] * 4,
            era5=[0.0] * 4,
        )
        subset_a = scale_subset(paired_a, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        subset_b = scale_subset(paired_b, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        result_a = matched_hour_mean_difference(
            subset_a, station=station_a, scale=Scale.JJAS
        )
        result_b = matched_hour_mean_difference(
            subset_b, station=station_b, scale=Scale.JJAS
        )

        band = band_matched_hour_mean_difference(
            ElevationBand.BELOW_700M, {station_a: result_a, station_b: result_b}
        )

        assert band.station_count == 2
        assert band.member_ns == (40, 4)
        # Station-equal mean of 2.0 and 10.0 = 6.0, NOT the hour-pooled
        # weighted mean ((2*40 + 10*4) / 44 = 2.7273).
        assert band.mean_value == pytest.approx(6.0)


class TestValueCannotBeAttachedFromADifferentPopulation:
    """Finding 1 (Plan 184 T3 independent review, 2026-08-21) — the FIFTH
    instance of this milestone's signature defect: a value attached to a
    population it was not computed from. The old regression
    (`test_periods_is_not_an_independently_settable_field`, formerly
    `test_periods_partition_the_subsets_own_hours_or_raise`) only proved a
    SIZE mismatch (999 hours vs 3) was caught — it could not catch the
    EQUAL-size case, which is the one that matters (an attacker with two
    subsets of the same n has no size signal to be caught on). These tests
    build two DIFFERENT but EQUAL-SIZED subsets, compute a real value from
    one, and prove there is no constructor argument through which that
    value could be attached to the other: the field simply does not exist,
    so the attempt is a `TypeError`, not a value that silently passes."""

    @staticmethod
    def _two_equal_sized_but_different_subsets() -> tuple[object, object]:
        station = Station("A")
        paired_x = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            3,
            gauge=[1.0, 1.0, 1.0],
            era5=[0.0, 0.0, 0.0],
        )
        paired_y = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            3,
            gauge=[100.0, 100.0, 100.0],
            era5=[0.0, 0.0, 0.0],
        )
        subset_x = scale_subset(paired_x, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        subset_y = scale_subset(paired_y, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        assert subset_x.n_common_retained == subset_y.n_common_retained == 3
        return subset_x, subset_y

    def test_matched_hour_mean_difference_has_no_value_field_to_attach_through(
        self,
    ) -> None:
        station = Station("A")
        subset_x, subset_y = self._two_equal_sized_but_different_subsets()
        result_from_x = matched_hour_mean_difference(
            subset_x, station=station, scale=Scale.JJAS
        )
        value_from_x = result_from_x.mean_difference_mm_per_h

        with pytest.raises(TypeError, match="mean_difference_mm_per_h"):
            MatchedHourMeanDifference(
                station=station,
                scale=Scale.JJAS,
                subset=subset_y,  # a DIFFERENT, equal-sized population
                mean_difference_mm_per_h=value_from_x,  # type: ignore[call-arg]
            )

    def test_wet_hour_conditional_intensity_bias_has_no_value_field_to_attach_through(
        self,
    ) -> None:
        station = Station("A")
        subset_x, subset_y = self._two_equal_sized_but_different_subsets()
        result_from_x = wet_hour_conditional_intensity_bias(
            subset_x, station=station, scale=Scale.JJAS
        )
        value_from_x = result_from_x.mean_intensity_bias_mm_per_h

        with pytest.raises(TypeError, match="mean_intensity_bias_mm_per_h"):
            WetHourConditionalIntensityBias(
                station=station,
                scale=Scale.JJAS,
                subset=subset_y,  # a DIFFERENT, equal-sized population
                mean_intensity_bias_mm_per_h=value_from_x,  # type: ignore[call-arg]
            )

    def test_conditional_accumulated_difference_has_no_periods_field_to_attach_through(
        self,
    ) -> None:
        station = Station("A")
        subset_x, subset_y = self._two_equal_sized_but_different_subsets()
        result_from_x = conditional_accumulated_difference(
            subset_x, station=station, scale=Scale.JJAS
        )
        periods_from_x = result_from_x.periods

        with pytest.raises(TypeError, match="periods"):
            ConditionalAccumulatedDifference(
                station=station,
                scale=Scale.JJAS,
                subset=subset_y,  # a DIFFERENT, equal-sized population
                periods=periods_from_x,  # type: ignore[call-arg]
            )


class TestCategoricalScoresRetentionConditionalityLabel:
    """Finding 2 (Plan 184 T3 independent review, 2026-08-21) — Rule 1's
    conditional-on-retention label must travel with `CategoricalScores`
    (POD/FAR/CSI are only reportable conditional-on-retention under the
    MNAR mask) but must NOT be attached to the two estimands Rule 1 says
    ARE well-defined under the mask, since that would over-caveat an
    already-well-defined result."""

    @staticmethod
    def _daily_accumulated() -> ConditionalAccumulatedDifference:
        station = Station("A")
        start = datetime(2024, 7, 1, 0)
        n_hours = 48
        gauge = [1.0] * 24 + [0.0] * 24
        era5 = [1.0] * 24 + [0.0] * 24
        timestamps = [start + timedelta(hours=i) for i in range(n_hours)]
        frame = _paired_frame(
            [
                {
                    "timestamp": timestamps[i],
                    "gauge_value_mm": gauge[i],
                    "era5_nearest_mm_per_h": era5[i],
                }
                for i in range(n_hours)
            ]
        )
        paired = PairedSeries(station=station, frame=frame)
        daily = scale_subset(paired, scale=Scale.DAILY, params=DEFAULT_PARAMS)
        return conditional_accumulated_difference(
            daily, station=station, scale=Scale.DAILY
        )

    def test_categorical_scores_carries_the_conditional_on_retention_label(
        self,
    ) -> None:
        scores = categorical_scores(self._daily_accumulated(), params=DEFAULT_PARAMS)

        assert (
            scores.retention_conditionality
            is RetentionConditionality.CONDITIONAL_ON_RETENTION
        )

    def test_mask_well_defined_estimands_do_not_carry_the_label(self) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            3,
            gauge=[2.0, 4.0, 0.0],
            era5=[1.0, 1.0, 0.0],
        )
        jjas = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        wet_jjas = wet_scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)

        mean_result = matched_hour_mean_difference(
            jjas, station=station, scale=Scale.JJAS
        )
        wet_result = wet_hour_conditional_intensity_bias(
            wet_jjas, station=station, scale=Scale.JJAS
        )

        assert not hasattr(mean_result, "retention_conditionality")
        assert not hasattr(wet_result, "retention_conditionality")


class TestSubsetSchemaGuardStillAppliesThroughThisModule:
    """T3 must not bypass T1's own schema guard — a caller cannot smuggle a
    gauge-only frame in where a paired subset is required."""

    def test_scale_subset_on_a_gauge_only_series_raises(self) -> None:

        gauge_only = MaskedGaugeSeries(
            station=Station("A"),
            frame=pl.DataFrame(
                {"timestamp": [datetime(2024, 7, 1, 0)], "value_mm": [1.0]}
            ),
        )
        with pytest.raises(Exception):  # noqa: B017, PT011 — pyright would catch this statically
            subset(gauge_only, pl.col("timestamp").dt.month() == 7).frame.select(
                "gauge_value_mm"
            )


if __name__ == "__main__":
    pytest.main([__file__])
