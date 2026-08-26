"""Plan 184 (M-A6) task T3 — the estimands.

Seam tests: every assertion is on a VALUE actually produced (a mean, a
count, a raised exception), never on an argument passed to a mock — the
same discipline `test_ma6_pairs.py` states for T1.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import ClassVar

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_estimands import (
    BandMembershipError,
    CategoricalGrainRefusedError,
    CategoricalScores,
    ConditionalAccumulatedDifference,
    DuplicateBandMemberError,
    ElevationBand,
    ElevationBandWetHourConditionalIntensityBiasComparison,
    EmptySubsetError,
    EstimandSubsetTypeError,
    JointWetConditionality,
    JointWetHourConditionalIntensityBias,
    MatchedHourMeanDifference,
    RetentionConditionality,
    Scale,
    ScaleNotSupportedError,
    UnconditionedSubsetError,
    WetHourConditionalIntensityBias,
    WetHourConditionalIntensityBiasComparison,
    assign_elevation_band,
    band_conditional_accumulated_difference,
    band_matched_hour_mean_difference,
    band_wet_hour_conditional_intensity_bias_comparison,
    categorical_scores,
    conditional_accumulated_difference,
    joint_wet_hour_conditional_intensity_bias,
    joint_wet_scale_subset,
    matched_hour_mean_difference,
    scale_subset,
    wet_hour_conditional_intensity_bias,
    wet_hour_conditional_intensity_bias_comparison,
    wet_scale_subset,
)
from scripts.dhm_precip.ma6_pairs import MaskedGaugeSeries, PairedSeries, subset
from scripts.dhm_precip.params import DEFAULT_PARAMS


def _with_station(frame: pl.DataFrame, station: Station) -> pl.DataFrame:
    """`station` is a frame column now — derived, never a separately-
    suppliable constructor field (Plan 184 phase 2 round 2)."""
    return frame.with_columns(pl.lit(str(station)).alias("station"))


def _paired_frame(rows: list[dict[str, object]], *, station: Station) -> pl.DataFrame:
    return _with_station(
        pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms"))),
        station,
    )


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
        ],
        station=station,
    )
    return PairedSeries(frame=frame)


class TestScaleIsImportedNotDuplicated:
    """Root-cause structural fix (Plan 184 phase 2, 2026-08-24): `Scale`
    moved to `ma6_pairs.py` — a subset's scale is a property of HOW it was
    taken, so it belongs with `subset()`. `ma6_estimands.Scale` must be the
    SAME object `ma6_pairs.Scale` is, imported, never a second competing
    definition (which would make a `ma6_pairs`-built subset's `.scale` fail
    an `isinstance`/`in _MAGNITUDE_SCALES` check performed against
    `ma6_estimands`'s own, different, enum)."""

    def test_ma6_estimands_scale_is_ma6_pairs_scale(self) -> None:
        from scripts.dhm_precip import ma6_pairs

        assert Scale is ma6_pairs.Scale

    def test_a_ma6_pairs_built_subsets_scale_round_trips_through_an_estimand(
        self,
    ) -> None:
        # If ma6_estimands defined its OWN Scale, MatchedHourMeanDifference.
        # __post_init__'s `self.scale not in _MAGNITUDE_SCALES` check would
        # compare a ma6_pairs.Scale.JJAS instance against ma6_estimands'
        # OWN (different) Scale.JJAS member and reject every real subset —
        # this only passes because both modules share the ONE enum.
        paired = _make_paired_series(
            Station("A"), datetime(2024, 7, 1, 0), 1, gauge=[1.0], era5=[1.0]
        )
        jjas = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)

        result = matched_hour_mean_difference(jjas)

        assert result.scale is Scale.JJAS


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

        result = matched_hour_mean_difference(jjas)

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
            ],
            station=station,
        )
        paired = PairedSeries(frame=frame)

        djf = scale_subset(paired, scale=Scale.DJF, params=DEFAULT_PARAMS)
        result = matched_hour_mean_difference(djf)

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
            matched_hour_mean_difference(djf)

    def test_daily_scale_is_not_supported(self) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station, datetime(2024, 7, 1, 0), 1, gauge=[1.0], era5=[1.0]
        )
        daily = scale_subset(paired, scale=Scale.DAILY, params=DEFAULT_PARAMS)

        with pytest.raises(Exception):  # noqa: B017, PT011 — ScaleNotSupportedError, __post_init__
            matched_hour_mean_difference(daily)


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

        result = wet_hour_conditional_intensity_bias(wet_jjas, params=DEFAULT_PARAMS)

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


class TestJointWetHourConditionalIntensityBias:
    def test_restricted_to_both_sides_wet_hours_only(self) -> None:
        station = Station("A")
        # wet_threshold_mm_per_h default 0.2. Hours: (gauge 0.0 dry, era5
        # 0.0 dry) excluded; (gauge 1.0 wet, era5 0.1 dry) excluded — gauge
        # wet but ERA5 dry; (gauge 2.0 wet, era5 0.5 wet) included, diff
        # 1.5; (gauge 0.0 dry, era5 1.0 wet) excluded — ERA5 wet but gauge
        # dry.
        paired = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            4,
            gauge=[0.0, 1.0, 2.0, 0.0],
            era5=[0.0, 0.1, 0.5, 1.0],
        )
        joint_wet_jjas = joint_wet_scale_subset(
            paired, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )

        result = joint_wet_hour_conditional_intensity_bias(
            joint_wet_jjas, params=DEFAULT_PARAMS
        )

        assert result.n == 1
        assert result.mean_intensity_bias_mm_per_h == pytest.approx(1.5)

    def test_joint_conditionality_marker_is_carried_and_gauge_alone_lacks_it(
        self,
    ) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station,
            datetime(2024, 7, 1, 0),
            2,
            gauge=[1.0, 2.0],
            era5=[0.5, 0.5],
        )
        joint_wet_jjas = joint_wet_scale_subset(
            paired, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )
        wet_jjas = wet_scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)

        joint_result = joint_wet_hour_conditional_intensity_bias(
            joint_wet_jjas, params=DEFAULT_PARAMS
        )
        gauge_alone_result = wet_hour_conditional_intensity_bias(
            wet_jjas, params=DEFAULT_PARAMS
        )

        expected_conditionality = (
            JointWetConditionality.CONDITIONAL_ON_RETENTION_AND_ERA5_WET_CLASSIFICATION
        )
        assert joint_result.joint_conditionality is expected_conditionality
        # Rule 1: the gauge-alone conditioning is already well-defined
        # under the mask (singly conditional) — over-caveating it with the
        # doubly-conditional marker would misrepresent it.
        assert not hasattr(gauge_alone_result, "joint_conditionality")


class TestUnconditionedSubsetIsRefusedByTheWetFactories:
    """Root-cause structural fix (Plan 184 phase 2, 2026-08-24): the wet/
    joint factories used to accept ANY `PairedRetainedSubset`, including an
    unconditioned `scale_subset()` output that was never wet-filtered at
    all. Both now verify, against the subset's own `frame` and through
    `wet_predicate` itself, that every retained hour actually satisfies the
    conditioning being claimed."""

    @staticmethod
    def _mixed_wet_and_dry_paired() -> PairedSeries:
        # gauge 0.0 is dry (< 0.2 default threshold); gauge 1.0 is wet.
        # scale_subset's output therefore carries BOTH — genuinely
        # unconditioned, not merely mislabelled.
        return _make_paired_series(
            Station("A"),
            datetime(2024, 7, 1, 0),
            2,
            gauge=[0.0, 1.0],
            era5=[0.0, 0.5],
        )

    def test_wet_factory_rejects_an_unconditioned_scale_subset(self) -> None:
        paired = self._mixed_wet_and_dry_paired()
        unconditioned = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)

        with pytest.raises(UnconditionedSubsetError):
            wet_hour_conditional_intensity_bias(unconditioned, params=DEFAULT_PARAMS)

    def test_wet_factory_accepts_a_genuinely_wet_conditioned_subset(self) -> None:
        # The positive case: wet_scale_subset's own output — every row
        # gauge-wet by construction — is accepted without complaint.
        paired = self._mixed_wet_and_dry_paired()
        conditioned = wet_scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)

        result = wet_hour_conditional_intensity_bias(conditioned, params=DEFAULT_PARAMS)

        assert result.n == 1

    def test_joint_factory_rejects_an_unconditioned_scale_subset(self) -> None:
        paired = self._mixed_wet_and_dry_paired()
        unconditioned = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)

        with pytest.raises(UnconditionedSubsetError):
            joint_wet_hour_conditional_intensity_bias(
                unconditioned, params=DEFAULT_PARAMS
            )

    def test_joint_factory_rejects_a_gauge_wet_only_subset(self) -> None:
        # A subset conditioned on gauge-wet ALONE (wet_scale_subset's
        # output) is still not jointly-wet-conditioned unless ERA5 is also
        # wet on every row — the joint factory must refuse it too, not
        # just a fully-unconditioned one.
        station = Station("A")
        # Row 0: gauge wet, ERA5 dry — passes wet_scale_subset's predicate
        # but NOT joint_wet_scale_subset's.
        paired = _make_paired_series(
            station, datetime(2024, 7, 1, 0), 1, gauge=[1.0], era5=[0.0]
        )
        gauge_wet_only = wet_scale_subset(
            paired, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )
        assert gauge_wet_only.n_common_retained == 1

        with pytest.raises(UnconditionedSubsetError):
            joint_wet_hour_conditional_intensity_bias(
                gauge_wet_only, params=DEFAULT_PARAMS
            )

    def test_joint_factory_accepts_a_genuinely_jointly_wet_conditioned_subset(
        self,
    ) -> None:
        paired = self._mixed_wet_and_dry_paired()
        conditioned = joint_wet_scale_subset(
            paired, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )

        result = joint_wet_hour_conditional_intensity_bias(
            conditioned, params=DEFAULT_PARAMS
        )

        assert result.n == 1


class TestWetHourConditionalIntensityBiasComparison:
    @staticmethod
    def _real_shaped_paired() -> tuple[Station, PairedSeries]:
        # A 4-hour cycle repeated 20 times (80 hours): dry/dry, gauge-wet-
        # only, both-wet, era5-wet-only. Gauge-wet hours = 2 per cycle (40
        # total); jointly-wet hours = 1 per cycle (20 total) — a real
        # (not contrived 1-row) population where n_joint is STRICTLY less
        # than n_gauge_alone, not merely <=.
        station = Station("A")
        cycle_gauge = [0.0, 1.0, 2.0, 0.0]
        cycle_era5 = [0.0, 0.1, 0.5, 1.0]
        n_cycles = 20
        gauge = cycle_gauge * n_cycles
        era5 = cycle_era5 * n_cycles
        paired = _make_paired_series(
            station, datetime(2024, 7, 1, 0), len(gauge), gauge=gauge, era5=era5
        )
        return station, paired

    @classmethod
    def _real_shaped_comparison(cls) -> WetHourConditionalIntensityBiasComparison:
        _station, paired = cls._real_shaped_paired()
        return wet_hour_conditional_intensity_bias_comparison(
            paired, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )

    def test_joint_n_is_strictly_less_than_gauge_alone_n_on_real_shaped_data(
        self,
    ) -> None:
        comparison = self._real_shaped_comparison()

        assert comparison.gauge_alone.n == 40
        assert comparison.joint.n == 20
        assert comparison.joint.n < comparison.gauge_alone.n
        assert comparison.joint is not comparison.gauge_alone
        assert comparison.joint.n != comparison.gauge_alone.n

    def test_detection_ratio_and_mean_shift_are_computed_not_stored(self) -> None:
        comparison = self._real_shaped_comparison()

        assert comparison.detection_ratio == pytest.approx(20 / 40)
        # gauge_alone mean over 40 hours: 20x(1.0-0.1)+20x(2.0-0.5) = 48/40 = 1.2
        # joint mean over 20 hours: (2.0-0.5) = 1.5
        assert comparison.gauge_alone.mean_intensity_bias_mm_per_h == pytest.approx(1.2)
        assert comparison.joint.mean_intensity_bias_mm_per_h == pytest.approx(1.5)
        assert comparison.mean_shift_mm_per_h == pytest.approx(1.5 - 1.2)

    def test_rejects_a_paired_that_is_not_a_real_pairedseries(self) -> None:
        with pytest.raises(EstimandSubsetTypeError):
            WetHourConditionalIntensityBiasComparison(
                scale=Scale.JJAS,
                paired=42,  # type: ignore[arg-type]
                params=DEFAULT_PARAMS,
            )

    def test_daily_scale_is_not_supported(self) -> None:
        _station, paired = self._real_shaped_paired()

        with pytest.raises(ScaleNotSupportedError):
            WetHourConditionalIntensityBiasComparison(
                scale=Scale.DAILY,
                paired=paired,
                params=DEFAULT_PARAMS,
            )

    def test_station_cannot_be_supplied_independently_of_paired(self) -> None:
        # Finding 1 (Plan 184 phase 2 CLOSING independent review,
        # 2026-08-24) — the SEVENTH instance of this milestone's signature
        # defect: `station` used to be an independently-suppliable
        # constructor field, never reconciled against `paired.station`, so
        # a correctly-nested pair of values could be emitted under the
        # WRONG station label. The fix removes the field entirely, so
        # attempting to supply it is a TypeError (unexpected keyword
        # argument) — a mislabelled result is now unconstructible, not
        # merely unchecked.
        _station, paired = self._real_shaped_paired()

        with pytest.raises(TypeError, match="station"):
            WetHourConditionalIntensityBiasComparison(
                station=Station("mismatched"),  # type: ignore[call-arg]
                scale=Scale.JJAS,
                paired=paired,
                params=DEFAULT_PARAMS,
            )

    def test_station_is_derived_from_paired_not_stored(self) -> None:
        station, paired = self._real_shaped_paired()

        comparison = wet_hour_conditional_intensity_bias_comparison(
            paired, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )

        assert comparison.station == station
        assert comparison.station == paired.station

    def test_gauge_alone_and_joint_are_not_independently_suppliable_fields(
        self,
    ) -> None:
        # Finding 1 (Plan 184 phase 2 independent review, 2026-08-24): the
        # OLD comparator took `gauge_alone`/`joint` as independently-
        # suppliable constructor fields. The fix removes the fields
        # entirely rather than strengthening the guard on them — so
        # attempting to supply either is a TypeError (unexpected keyword
        # argument), the same "no field to attach through" shape every
        # other estimand in this module already proves.
        _station, paired = self._real_shaped_paired()
        comparison = wet_hour_conditional_intensity_bias_comparison(
            paired, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )

        with pytest.raises(TypeError, match="gauge_alone"):
            WetHourConditionalIntensityBiasComparison(
                scale=Scale.JJAS,
                paired=paired,
                params=DEFAULT_PARAMS,
                gauge_alone=comparison.gauge_alone,  # type: ignore[call-arg]
            )
        with pytest.raises(TypeError, match="joint"):
            WetHourConditionalIntensityBiasComparison(
                scale=Scale.JJAS,
                paired=paired,
                params=DEFAULT_PARAMS,
                joint=comparison.joint,  # type: ignore[call-arg]
            )

    def test_same_timestamps_different_values_cannot_be_crossed_between_conditionings(
        self,
    ) -> None:
        # Finding 1 (Plan 184 phase 2 independent review, 2026-08-24) — the
        # SIXTH instance of this milestone's signature defect, and the
        # case the OLD test (`test_joint_population_not_nested_in_gauge_
        # alone_is_rejected`, which used DISJOINT timestamps) could not
        # reach: two subsets built from DIFFERENT PairedSeries that share
        # the SAME timestamps but carry UNRELATED gauge/ERA5 values passed
        # the old anti-join-on-timestamp guard undetected. `paired_x` and
        # `paired_y` below are exactly that shape — same station, same 4
        # timestamps, different values, each with its own non-trivial
        # gauge-wet/jointly-wet population (so a crossed result would be
        # numerically distinguishable, not a degenerate no-op).
        station = Station("A")
        start = datetime(2024, 7, 1, 0)
        # paired_x: all 4 hours gauge-wet; 2 of those also era5-wet.
        paired_x = _make_paired_series(
            station, start, 4, gauge=[1.0, 1.0, 1.0, 1.0], era5=[0.5, 0.5, 0.1, 0.1]
        )
        # paired_y: same timestamps, DIFFERENT values — only 2 hours
        # gauge-wet, both of those also era5-wet (a completely different
        # detection/intensity shape).
        paired_y = _make_paired_series(
            station, start, 4, gauge=[5.0, 5.0, 0.0, 0.0], era5=[50.0, 50.0, 0.0, 0.0]
        )

        comparison_x = wet_hour_conditional_intensity_bias_comparison(
            paired_x, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )
        comparison_y = wet_hour_conditional_intensity_bias_comparison(
            paired_y, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )

        # The two series give genuinely different, non-degenerate numbers
        # — confirming this WOULD be a meaningful attack if crossing were
        # possible, not a no-op with nothing to fabricate.
        assert comparison_x.gauge_alone.n == 4
        assert comparison_x.joint.n == 2
        assert comparison_x.detection_ratio == pytest.approx(0.5)
        assert comparison_x.gauge_alone.mean_intensity_bias_mm_per_h == pytest.approx(
            0.7
        )
        assert comparison_x.joint.mean_intensity_bias_mm_per_h == pytest.approx(0.5)

        assert comparison_y.gauge_alone.n == 2
        assert comparison_y.joint.n == 2
        assert comparison_y.detection_ratio == pytest.approx(1.0)
        assert comparison_y.gauge_alone.mean_intensity_bias_mm_per_h == pytest.approx(
            -45.0
        )

        # There is no argument through which `comparison_x`'s `paired`
        # could be swapped for `paired_y`'s components after the fact —
        # `gauge_alone`/`joint` are properties, `paired` is the only
        # data-bearing field, and reconstructing with `paired_y`'s
        # PRE-COMPUTED gauge_alone/joint (rather than `paired_y` itself)
        # is rejected outright because those are not constructor fields.
        with pytest.raises(TypeError, match="joint"):
            WetHourConditionalIntensityBiasComparison(
                scale=Scale.JJAS,
                paired=paired_x,
                params=DEFAULT_PARAMS,
                joint=comparison_y.joint,  # type: ignore[call-arg]
            )
        # The only way to get comparison_x's numbers is to pass paired_x;
        # they are never contaminated by paired_y's values.
        assert comparison_x.detection_ratio != pytest.approx(
            comparison_y.detection_ratio
        )

    def test_detection_ratio_has_no_field_to_attach_through(self) -> None:
        _station, paired = self._real_shaped_paired()
        comparison = self._real_shaped_comparison()
        stolen_ratio = comparison.detection_ratio

        with pytest.raises(TypeError, match="detection_ratio"):
            WetHourConditionalIntensityBiasComparison(
                scale=Scale.JJAS,
                paired=paired,
                params=DEFAULT_PARAMS,
                detection_ratio=stolen_ratio,  # type: ignore[call-arg]
            )

    def test_mean_shift_has_no_field_to_attach_through(self) -> None:
        _station, paired = self._real_shaped_paired()
        comparison = self._real_shaped_comparison()
        stolen_shift = comparison.mean_shift_mm_per_h

        with pytest.raises(TypeError, match="mean_shift_mm_per_h"):
            WetHourConditionalIntensityBiasComparison(
                scale=Scale.JJAS,
                paired=paired,
                params=DEFAULT_PARAMS,
                mean_shift_mm_per_h=stolen_shift,  # type: ignore[call-arg]
            )

    def test_joint_intensity_bias_has_no_value_field_to_attach_through(self) -> None:
        # Mirrors TestValueCannotBeAttachedFromADifferentPopulation for the
        # NEW joint estimand type: two DIFFERENT, EQUAL-SIZED joint-wet
        # subsets, a real value computed from one, no constructor field
        # through which that value can be attached to the other.
        station = Station("A")
        paired_x = _make_paired_series(
            station, datetime(2024, 7, 1, 0), 2, gauge=[1.0, 1.0], era5=[0.5, 0.5]
        )
        paired_y = _make_paired_series(
            station, datetime(2024, 7, 1, 0), 2, gauge=[100.0, 100.0], era5=[0.5, 0.5]
        )
        subset_x = joint_wet_scale_subset(
            paired_x, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )
        subset_y = joint_wet_scale_subset(
            paired_y, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )
        assert subset_x.n_common_retained == subset_y.n_common_retained == 2
        result_from_x = joint_wet_hour_conditional_intensity_bias(
            subset_x, params=DEFAULT_PARAMS
        )
        value_from_x = result_from_x.mean_intensity_bias_mm_per_h

        with pytest.raises(TypeError, match="mean_intensity_bias_mm_per_h"):
            JointWetHourConditionalIntensityBias(
                subset=subset_y,  # a DIFFERENT, equal-sized population
                mean_intensity_bias_mm_per_h=value_from_x,  # type: ignore[call-arg]
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

        result = conditional_accumulated_difference(jjas)

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
            ],
            station=station,
        )
        paired = PairedSeries(frame=frame)
        all_hours = scale_subset(paired, scale=Scale.DAILY, params=DEFAULT_PARAMS)

        result = conditional_accumulated_difference(all_hours)

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
                subset=real_subset,
                periods=(),  # type: ignore[call-arg]
            )

    def test_rejects_a_subset_that_is_not_a_real_pairedretainedsubset(self) -> None:
        with pytest.raises(EstimandSubsetTypeError):
            ConditionalAccumulatedDifference(
                subset=42,  # type: ignore[arg-type]
            )

    def test_empty_subset_raises(self) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station, datetime(2024, 7, 1, 0), 1, gauge=[1.0], era5=[1.0]
        )
        djf = scale_subset(paired, scale=Scale.DJF, params=DEFAULT_PARAMS)

        with pytest.raises(EmptySubsetError):
            conditional_accumulated_difference(djf)


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
        return conditional_accumulated_difference(jjas)

    def test_jjas_grain_is_refused(self) -> None:
        accumulated = self._jjas_accumulated()

        with pytest.raises(CategoricalGrainRefusedError):
            categorical_scores(accumulated, params=DEFAULT_PARAMS)

    def test_jjas_grain_is_refused_by_direct_construction_too(self) -> None:
        # Finding 2 (Plan 184 phase 2 independent review, 2026-08-24): the
        # OLD `CategoricalScores` refused seasonal grain ONLY inside the
        # `categorical_scores()` factory — direct construction accepted a
        # JJAS score with arbitrary n_hours/POD/FAR/CSI, bypassing D12's
        # vacuity refusal entirely. The refusal now lives in
        # `CategoricalScores.__post_init__` itself, so direct construction
        # (bypassing the factory) must raise too.
        accumulated = self._jjas_accumulated()

        with pytest.raises(CategoricalGrainRefusedError):
            CategoricalScores(accumulated=accumulated, params=DEFAULT_PARAMS)

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
        accumulated = conditional_accumulated_difference(djf)

        with pytest.raises(CategoricalGrainRefusedError):
            categorical_scores(accumulated, params=DEFAULT_PARAMS)

    def test_djf_grain_is_refused_by_direct_construction_too(self) -> None:
        station = Station("A")
        paired = _make_paired_series(
            station,
            datetime(2024, 12, 1, 0),
            2,
            gauge=[1.0, 2.0],
            era5=[0.5, 0.5],
        )
        djf = scale_subset(paired, scale=Scale.DJF, params=DEFAULT_PARAMS)
        accumulated = conditional_accumulated_difference(djf)

        with pytest.raises(CategoricalGrainRefusedError):
            CategoricalScores(accumulated=accumulated, params=DEFAULT_PARAMS)

    def test_rejects_an_accumulated_that_is_not_a_real_conditionalaccumulateddifference(
        self,
    ) -> None:
        with pytest.raises(EstimandSubsetTypeError):
            CategoricalScores(
                accumulated=42,  # type: ignore[arg-type]
                params=DEFAULT_PARAMS,
            )

    def test_scores_and_counts_are_not_independently_suppliable_fields(self) -> None:
        # Finding 2 continued: `scale`, counts and scores were freely
        # constructible fields — only the factory refused seasonal grain.
        # They are now `@property`s derived from `self.accumulated`/
        # `self.params`, so there is no `n_hours=`/`pod=`/`far=`/`csi=`
        # constructor argument through which a caller could attach
        # fabricated values to a real `CategoricalScores` instance.
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
            ],
            station=station,
        )
        paired = PairedSeries(frame=frame)
        daily = scale_subset(paired, scale=Scale.DAILY, params=DEFAULT_PARAMS)
        accumulated = conditional_accumulated_difference(daily)

        with pytest.raises(TypeError, match="pod"):
            CategoricalScores(
                accumulated=accumulated,
                params=DEFAULT_PARAMS,
                pod=1.0,  # type: ignore[call-arg]
            )
        with pytest.raises(TypeError, match="n_hours"):
            CategoricalScores(
                accumulated=accumulated,
                params=DEFAULT_PARAMS,
                n_hours=999,  # type: ignore[call-arg]
            )

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
            ],
            station=station,
        )
        paired = PairedSeries(frame=frame)
        daily = scale_subset(paired, scale=Scale.DAILY, params=DEFAULT_PARAMS)
        accumulated = conditional_accumulated_difference(daily)

        scores = categorical_scores(accumulated, params=DEFAULT_PARAMS)

        assert scores.n_periods == 2
        assert scores.pod == pytest.approx(1.0)
        assert scores.far == pytest.approx(0.0)
        assert scores.csi == pytest.approx(1.0)


class TestCategoricalScoresHonoursWetThresholdSide:
    """Finding 2 (Plan 184 phase 2 CLOSING independent review, 2026-08-24):
    `_confusion_counts` used to hardcode `>=` for the aggregated-bucket
    wet/dry call, ignoring a configured `wet_threshold_side=">"`. A bucket
    totalling EXACTLY the wet threshold (0.2 mm) must be classified wet
    under `">="` and dry under `">"`."""

    @staticmethod
    def _boundary_accumulated() -> ConditionalAccumulatedDifference:
        # Day 1: gauge accumulates to EXACTLY the wet threshold (0.2 mm);
        # ERA5 is clearly wet either way (0.5 mm). Day 2: gauge is clearly
        # wet either way (1.0 mm); ERA5 is clearly dry either way (0.0
        # mm). Day 2 is therefore a MISS under both sides — a stable
        # denominator — while day 1 flips between a HIT (>=) and a FALSE
        # ALARM (>), so POD/FAR/CSI move only because of the boundary
        # bucket, not because of anything else in the population.
        station = Station("A")
        frame = _paired_frame(
            [
                {
                    "timestamp": datetime(2024, 7, 1, 0),
                    "gauge_value_mm": 0.2,
                    "era5_nearest_mm_per_h": 0.5,
                },
                {
                    "timestamp": datetime(2024, 7, 2, 0),
                    "gauge_value_mm": 1.0,
                    "era5_nearest_mm_per_h": 0.0,
                },
            ],
            station=station,
        )
        paired = PairedSeries(frame=frame)
        daily = scale_subset(paired, scale=Scale.DAILY, params=DEFAULT_PARAMS)
        return conditional_accumulated_difference(daily)

    def test_default_side_is_ge(self) -> None:
        # The categorical-numbers-must-not-move claim in the Plan 184
        # phase 2 verification instructions depends on this: the default
        # `wet_threshold_side` must be ">=", so this fix does not change
        # any DEFAULT_PARAMS-driven result.
        assert DEFAULT_PARAMS.wet_threshold_side == ">="

    def test_ge_side_classifies_the_boundary_bucket_wet(self) -> None:
        accumulated = self._boundary_accumulated()

        scores = categorical_scores(accumulated, params=DEFAULT_PARAMS)

        # Day 1: 0.2 >= 0.2 -> gauge wet; 0.5 >= 0.2 -> era5 wet -> HIT.
        # Day 2: gauge wet, era5 dry -> MISS.
        assert scores.pod == pytest.approx(0.5)
        assert scores.far == pytest.approx(0.0)
        assert scores.csi == pytest.approx(0.5)

    def test_gt_side_classifies_the_boundary_bucket_dry(self) -> None:
        accumulated = self._boundary_accumulated()
        gt_params = dataclasses.replace(DEFAULT_PARAMS, wet_threshold_side=">")

        scores = categorical_scores(accumulated, params=gt_params)

        # Day 1: 0.2 > 0.2 is False -> gauge DRY; 0.5 > 0.2 -> era5 wet ->
        # FALSE ALARM (the boundary bucket flips from a hit under ">=").
        # Day 2: gauge wet, era5 dry -> MISS, same as under ">=".
        assert scores.pod == pytest.approx(0.0)
        assert scores.far == pytest.approx(1.0)
        assert scores.csi == pytest.approx(0.0)


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
        result_a = matched_hour_mean_difference(subset_a)
        result_b = matched_hour_mean_difference(subset_b)

        band = band_matched_hour_mean_difference(
            ElevationBand.BELOW_700M,
            (result_a, result_b),
            station_elev_m={station_a: 500.0, station_b: 600.0},
        )

        assert band.station_count == 2
        assert band.member_ns == (40, 4)
        # Station-equal mean of 2.0 and 10.0 = 6.0, NOT the hour-pooled
        # weighted mean ((2*40 + 10*4) / 44 = 2.7273).
        assert band.mean_value == pytest.approx(6.0)

    def test_a_member_outside_the_bands_own_edges_is_rejected(self) -> None:
        # Change 4 (Plan 184 phase 2 round 2): band_matched_hour_mean_
        # difference gets the SAME band-membership verification the
        # wet-hour comparison already has — a member whose own elevation
        # places it outside the declared band's D4a edges is refused.
        station_a = Station("A")
        paired_a = _make_paired_series(
            station_a, datetime(2024, 7, 1, 0), 4, gauge=[2.0] * 4, era5=[0.0] * 4
        )
        subset_a = scale_subset(paired_a, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        result_a = matched_hour_mean_difference(subset_a)

        with pytest.raises(BandMembershipError, match="A"):
            band_matched_hour_mean_difference(
                ElevationBand.ABOVE_3000M,
                (result_a,),
                station_elev_m={station_a: 500.0},
            )


class TestBandConditionalAccumulatedDifferenceVerifiesMembership:
    """Change 4 (Plan 184 phase 2 round 2): `band_conditional_accumulated_
    difference` gets the SAME `station_elev_m` band-membership verification
    `band_matched_hour_mean_difference`/`band_wet_hour_conditional_
    intensity_bias_comparison` already have — the same `BandMembershipError`,
    not a second mechanism."""

    @staticmethod
    def _result_for(station: Station) -> ConditionalAccumulatedDifference:
        paired = _make_paired_series(
            station, datetime(2024, 7, 1, 0), 4, gauge=[2.0] * 4, era5=[0.0] * 4
        )
        jjas = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        return conditional_accumulated_difference(jjas)

    def test_accepts_a_member_inside_the_bands_own_edges(self) -> None:
        station_a = Station("A")
        result_a = self._result_for(station_a)

        band = band_conditional_accumulated_difference(
            ElevationBand.BELOW_700M,
            (result_a,),
            station_elev_m={station_a: 500.0},
        )

        assert band.station_count == 1

    def test_a_member_outside_the_bands_own_edges_is_rejected(self) -> None:
        station_a = Station("A")
        result_a = self._result_for(station_a)

        with pytest.raises(BandMembershipError, match="A"):
            band_conditional_accumulated_difference(
                ElevationBand.ABOVE_3000M,
                (result_a,),
                station_elev_m={station_a: 500.0},
            )

    def test_a_member_missing_from_station_elev_m_is_rejected(self) -> None:
        station_a = Station("A")
        result_a = self._result_for(station_a)

        with pytest.raises(BandMembershipError, match="A"):
            band_conditional_accumulated_difference(
                ElevationBand.BELOW_700M, (result_a,), station_elev_m={}
            )


class TestStationAndScaleAreDerivedNotAccepted:
    """Root-cause structural fix (Plan 184 phase 2, 2026-08-24): `station`
    and `scale` are `@property`s read off `self.subset.station`/
    `self.subset.scale` on all four fixed estimand types — NOT constructor
    fields. A station-A/JJAS subset can therefore never be reported as
    station-B/DJF: there is no `station=`/`scale=` argument through which a
    caller could even attempt it, so the attempt is a `TypeError` (no such
    keyword), not a value that happens to be checked and rejected."""

    def test_matched_hour_mean_difference_rejects_station_kwarg(self) -> None:
        station_a = Station("A")
        subset_a = scale_subset(
            _make_paired_series(
                station_a, datetime(2024, 7, 1, 0), 2, gauge=[1.0, 1.0], era5=[0.0, 0.0]
            ),
            scale=Scale.JJAS,
            params=DEFAULT_PARAMS,
        )

        with pytest.raises(TypeError, match="station"):
            MatchedHourMeanDifference(
                subset=subset_a,
                station=Station("B"),  # type: ignore[call-arg]
            )

    def test_matched_hour_mean_difference_rejects_scale_kwarg(self) -> None:
        subset_jjas = scale_subset(
            _make_paired_series(
                Station("A"),
                datetime(2024, 7, 1, 0),
                2,
                gauge=[1.0, 1.0],
                era5=[0.0, 0.0],
            ),
            scale=Scale.JJAS,
            params=DEFAULT_PARAMS,
        )

        with pytest.raises(TypeError, match="scale"):
            MatchedHourMeanDifference(
                subset=subset_jjas,
                scale=Scale.DJF,  # type: ignore[call-arg]
            )

    def test_matched_hour_mean_difference_station_always_matches_its_own_subset(
        self,
    ) -> None:
        # There is no argument through which station A's subset could be
        # reported as station B — the derived property always agrees with
        # the subset it was actually built from.
        station_a = Station("A")
        subset_a = scale_subset(
            _make_paired_series(
                station_a, datetime(2024, 7, 1, 0), 2, gauge=[1.0, 1.0], era5=[0.0, 0.0]
            ),
            scale=Scale.JJAS,
            params=DEFAULT_PARAMS,
        )

        result = matched_hour_mean_difference(subset_a)

        assert result.station == station_a
        assert result.station != Station("B")

    def test_conditional_accumulated_difference_rejects_station_and_scale_kwargs(
        self,
    ) -> None:
        subset_jjas = scale_subset(
            _make_paired_series(
                Station("A"),
                datetime(2024, 7, 1, 0),
                2,
                gauge=[1.0, 1.0],
                era5=[0.0, 0.0],
            ),
            scale=Scale.JJAS,
            params=DEFAULT_PARAMS,
        )

        with pytest.raises(TypeError, match="station"):
            ConditionalAccumulatedDifference(
                subset=subset_jjas,
                station=Station("B"),  # type: ignore[call-arg]
            )
        with pytest.raises(TypeError, match="scale"):
            ConditionalAccumulatedDifference(
                subset=subset_jjas,
                scale=Scale.DJF,  # type: ignore[call-arg]
            )

    def test_wet_hour_conditional_intensity_bias_rejects_station_and_scale_kwargs(
        self,
    ) -> None:
        wet_subset = wet_scale_subset(
            _make_paired_series(
                Station("A"),
                datetime(2024, 7, 1, 0),
                2,
                gauge=[1.0, 1.0],
                era5=[0.0, 0.0],
            ),
            scale=Scale.JJAS,
            params=DEFAULT_PARAMS,
        )

        with pytest.raises(TypeError, match="station"):
            WetHourConditionalIntensityBias(
                subset=wet_subset,
                station=Station("B"),  # type: ignore[call-arg]
            )
        with pytest.raises(TypeError, match="scale"):
            WetHourConditionalIntensityBias(
                subset=wet_subset,
                scale=Scale.DJF,  # type: ignore[call-arg]
            )

    def test_joint_wet_hour_conditional_intensity_bias_rejects_station_and_scale_kwargs(
        self,
    ) -> None:
        joint_subset = joint_wet_scale_subset(
            _make_paired_series(
                Station("A"),
                datetime(2024, 7, 1, 0),
                2,
                gauge=[1.0, 1.0],
                era5=[0.5, 0.5],
            ),
            scale=Scale.JJAS,
            params=DEFAULT_PARAMS,
        )

        with pytest.raises(TypeError, match="station"):
            JointWetHourConditionalIntensityBias(
                subset=joint_subset,
                station=Station("B"),  # type: ignore[call-arg]
            )
        with pytest.raises(TypeError, match="scale"):
            JointWetHourConditionalIntensityBias(
                subset=joint_subset,
                scale=Scale.DJF,  # type: ignore[call-arg]
            )

    def test_a_djf_subset_cannot_be_reported_as_jjas(self) -> None:
        # The scale side of the same guarantee: matched_hour_mean_
        # difference's own scale is always the subset's own scale, never
        # an independently-chosen one.
        djf_paired = _make_paired_series(
            Station("A"), datetime(2024, 12, 1, 0), 2, gauge=[1.0, 1.0], era5=[0.0, 0.0]
        )
        djf_subset = scale_subset(djf_paired, scale=Scale.DJF, params=DEFAULT_PARAMS)

        result = matched_hour_mean_difference(djf_subset)

        assert result.scale is Scale.DJF
        assert result.scale is not Scale.JJAS


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
        subset_x, subset_y = self._two_equal_sized_but_different_subsets()
        result_from_x = matched_hour_mean_difference(subset_x)
        value_from_x = result_from_x.mean_difference_mm_per_h

        with pytest.raises(TypeError, match="mean_difference_mm_per_h"):
            MatchedHourMeanDifference(
                subset=subset_y,  # a DIFFERENT, equal-sized population
                mean_difference_mm_per_h=value_from_x,  # type: ignore[call-arg]
            )

    def test_wet_hour_conditional_intensity_bias_has_no_value_field_to_attach_through(
        self,
    ) -> None:
        subset_x, subset_y = self._two_equal_sized_but_different_subsets()
        result_from_x = wet_hour_conditional_intensity_bias(
            subset_x, params=DEFAULT_PARAMS
        )
        value_from_x = result_from_x.mean_intensity_bias_mm_per_h

        with pytest.raises(TypeError, match="mean_intensity_bias_mm_per_h"):
            WetHourConditionalIntensityBias(
                subset=subset_y,  # a DIFFERENT, equal-sized population
                mean_intensity_bias_mm_per_h=value_from_x,  # type: ignore[call-arg]
            )

    def test_conditional_accumulated_difference_has_no_periods_field_to_attach_through(
        self,
    ) -> None:
        subset_x, subset_y = self._two_equal_sized_but_different_subsets()
        result_from_x = conditional_accumulated_difference(subset_x)
        periods_from_x = result_from_x.periods

        with pytest.raises(TypeError, match="periods"):
            ConditionalAccumulatedDifference(
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
            ],
            station=station,
        )
        paired = PairedSeries(frame=frame)
        daily = scale_subset(paired, scale=Scale.DAILY, params=DEFAULT_PARAMS)
        return conditional_accumulated_difference(daily)

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

        mean_result = matched_hour_mean_difference(jjas)
        wet_result = wet_hour_conditional_intensity_bias(
            wet_jjas, params=DEFAULT_PARAMS
        )

        assert not hasattr(mean_result, "retention_conditionality")
        assert not hasattr(wet_result, "retention_conditionality")


class TestElevationBandWetHourConditionalIntensityBiasComparison:
    """Finding 1 (Plan 184 phase 2 CLOSING independent review, 2026-08-24)
    — the aggregation-boundary half of the SEVENTH instance. The OLD
    `band_wet_hour_conditional_intensity_bias`/`band_joint_wet_hour_
    conditional_intensity_bias` combined gauge-alone and joint band
    results through two SEPARATE, independently-suppliable `Mapping[
    Station, ...]` arguments, with no check that the two mappings shared
    a station set, that a mapping's keys agreed with its values' own
    `.station`, or that gauge-alone and joint shared a common parent. The
    fix takes ONE `comparisons` tuple; `gauge_alone`/`joint` are both
    derived from it, so a band cannot be assembled from mismatched
    station sets."""

    # Both stations sit comfortably under the < 700 m edge (D4a) — used
    # throughout this class so `station_elev_m`'s band-membership
    # verification passes for the "real" cases below.
    _BELOW_700M_ELEV_M: ClassVar[dict[Station, float]] = {
        Station("A"): 500.0,
        Station("B"): 600.0,
    }

    @staticmethod
    def _comparison_for(station: Station) -> WetHourConditionalIntensityBiasComparison:
        cycle_gauge = [0.0, 1.0, 2.0, 0.0]
        cycle_era5 = [0.0, 0.1, 0.5, 1.0]
        n_cycles = 5
        gauge = cycle_gauge * n_cycles
        era5 = cycle_era5 * n_cycles
        paired = _make_paired_series(
            station, datetime(2024, 7, 1, 0), len(gauge), gauge=gauge, era5=era5
        )
        return wet_hour_conditional_intensity_bias_comparison(
            paired, scale=Scale.JJAS, params=DEFAULT_PARAMS
        )

    def test_gauge_alone_and_joint_bands_always_share_the_same_station_set(
        self,
    ) -> None:
        comp_a = self._comparison_for(Station("A"))
        comp_b = self._comparison_for(Station("B"))

        band = band_wet_hour_conditional_intensity_bias_comparison(
            ElevationBand.BELOW_700M,
            (comp_a, comp_b),
            station_elev_m=self._BELOW_700M_ELEV_M,
        )

        # There is exactly one `comparisons` tuple: both bands are counted
        # over the SAME two stations, by construction, never independently.
        assert band.gauge_alone.station_count == 2
        assert band.joint.station_count == 2

    def test_gauge_alone_and_joint_have_no_field_to_attach_through(self) -> None:
        # A band cannot be assembled from mismatched station sets: there
        # is no argument through which gauge-alone-only or joint-only
        # comparisons (from some OTHER, unrelated set of stations) could
        # be supplied in place of the derived properties.
        comp_a = self._comparison_for(Station("A"))
        comp_b = self._comparison_for(Station("B"))
        band = band_wet_hour_conditional_intensity_bias_comparison(
            ElevationBand.BELOW_700M,
            (comp_a, comp_b),
            station_elev_m=self._BELOW_700M_ELEV_M,
        )

        with pytest.raises(TypeError, match="gauge_alone"):
            ElevationBandWetHourConditionalIntensityBiasComparison(
                band=ElevationBand.BELOW_700M,
                scale=Scale.JJAS,
                comparisons=(comp_a, comp_b),
                station_elev_m=self._BELOW_700M_ELEV_M,
                gauge_alone=band.gauge_alone,  # type: ignore[call-arg]
            )
        with pytest.raises(TypeError, match="joint"):
            ElevationBandWetHourConditionalIntensityBiasComparison(
                band=ElevationBand.BELOW_700M,
                scale=Scale.JJAS,
                comparisons=(comp_a, comp_b),
                station_elev_m=self._BELOW_700M_ELEV_M,
                joint=band.joint,  # type: ignore[call-arg]
            )

    def test_duplicate_station_in_comparisons_is_rejected(self) -> None:
        # The band-membership check the old two-mapping design had no way
        # to make: a station counted twice would silently double its own
        # weight in the unweighted-mean band value.
        comp_a = self._comparison_for(Station("A"))

        with pytest.raises(DuplicateBandMemberError):
            band_wet_hour_conditional_intensity_bias_comparison(
                ElevationBand.BELOW_700M,
                (comp_a, comp_a),
                station_elev_m=self._BELOW_700M_ELEV_M,
            )

    def test_empty_comparisons_is_rejected(self) -> None:
        with pytest.raises(EmptySubsetError):
            band_wet_hour_conditional_intensity_bias_comparison(
                ElevationBand.BELOW_700M, (), station_elev_m=self._BELOW_700M_ELEV_M
            )

    def test_mismatched_scale_in_comparisons_is_rejected_by_direct_construction(
        self,
    ) -> None:
        comp_jjas = self._comparison_for(Station("A"))
        djf_paired = _make_paired_series(
            Station("B"),
            datetime(2024, 12, 1, 0),
            4,
            gauge=[1.0, 1.0, 1.0, 1.0],
            era5=[0.5, 0.5, 0.1, 0.1],
        )
        comp_djf = wet_hour_conditional_intensity_bias_comparison(
            djf_paired, scale=Scale.DJF, params=DEFAULT_PARAMS
        )

        with pytest.raises(ScaleNotSupportedError):
            ElevationBandWetHourConditionalIntensityBiasComparison(
                band=ElevationBand.BELOW_700M,
                scale=Scale.JJAS,
                comparisons=(comp_jjas, comp_djf),
                station_elev_m=self._BELOW_700M_ELEV_M,
            )

    def test_station_outside_the_bands_own_edges_is_rejected(self) -> None:
        # Root-cause structural fix (Plan 184 phase 2, 2026-08-24): `band`
        # used to be entirely independent of `comparisons` — the SAME
        # comparisons tuple could be labelled under ANY band with nothing
        # checking it. Station A's elevation (500 m) places it BELOW_700M
        # by D4a's own edges (`assign_elevation_band`), not in the
        # >= 3,000 m band being declared here.
        comp_a = self._comparison_for(Station("A"))

        with pytest.raises(BandMembershipError, match="A"):
            band_wet_hour_conditional_intensity_bias_comparison(
                ElevationBand.ABOVE_3000M,
                (comp_a,),
                station_elev_m={Station("A"): 500.0},
            )

    def test_station_missing_from_station_elev_m_is_rejected(self) -> None:
        comp_a = self._comparison_for(Station("A"))

        with pytest.raises(BandMembershipError, match="A"):
            band_wet_hour_conditional_intensity_bias_comparison(
                ElevationBand.BELOW_700M, (comp_a,), station_elev_m={}
            )


class TestSubsetSchemaGuardStillAppliesThroughThisModule:
    """T3 must not bypass T1's own schema guard — a caller cannot smuggle a
    gauge-only frame in where a paired subset is required."""

    def test_scale_subset_on_a_gauge_only_series_raises(self) -> None:
        # Fix (Plan 184 phase 2 round 2, change 5): the original version of
        # this test omitted the now-required `scale=` keyword, so
        # `subset()` raised `TypeError` on the missing argument and the
        # broad `pytest.raises(Exception)` swallowed it — the schema guard
        # this test claims to exercise (a caller cannot treat a
        # GaugeRetainedSubset's frame as if it carried the PAIRED columns)
        # was never actually reached.
        gauge_only = MaskedGaugeSeries(
            frame=_with_station(
                pl.DataFrame(
                    {"timestamp": [datetime(2024, 7, 1, 0)], "value_mm": [1.0]}
                ),
                Station("A"),
            ),
        )
        with pytest.raises(pl.exceptions.ColumnNotFoundError, match="gauge_value_mm"):
            subset(
                gauge_only, pl.col("timestamp").dt.month() == 7, scale=Scale.JJAS
            ).frame.select("gauge_value_mm")


if __name__ == "__main__":
    pytest.main([__file__])
