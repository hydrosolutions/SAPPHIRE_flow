from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import numpy as np
import pytest

from sapphire_flow.services.skill.quantiles import QuantileSample
from sapphire_flow.services.skill.rated_thresholds import (
    PairedSkillSample,
    paired_rated_probabilities,
    prepare_ratings,
)
from sapphire_flow.store.rated_threshold_codec import parse_rated_threshold_manifest
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AggregationMethod, InterpolationMethod
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.ids import RatingCurveId, StationId, TenantId
from sapphire_flow.types.native_capture import Sha256Digest
from sapphire_flow.types.rated_threshold_skill import (
    RatedThresholdManifest,
    RatedThresholdSkillContext,
    ReportedLevelThreshold,
    ThresholdReferenceBasis,
)
from sapphire_flow.types.rating_curve import RatingCurve
from sapphire_flow.types.rating_reference import curve_snapshot
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid

STAMP = ensure_utc(datetime(2013, 2, 1, 18, tzinfo=UTC))
STATION = StationId(UUID(int=1))
DAY = timedelta(days=1)


@pytest.fixture
def curve() -> RatingCurve:
    return RatingCurve(
        id=RatingCurveId(UUID(int=2)),
        station_id=STATION,
        version=1,
        valid_from=ensure_utc(STAMP - 10 * DAY),
        valid_to=None,
        points=[
            {"water_level": 0.0, "discharge": 0.0},
            {"water_level": 10.0, "discharge": 100.0},
        ],
        interpolation=InterpolationMethod.LINEAR,
        uploaded_by=None,
        created_at=STAMP,
    )


def manifest(curves: list[RatingCurve], level: float = 5.0) -> RatedThresholdManifest:
    return RatedThresholdManifest(
        station_id=STATION,
        tenant_id=TenantId(UUID(int=3)),
        network="example",
        station_code="test",
        api_station_id=1,
        source_url="https://example.com/stations/",
        retrieved_at=STAMP,
        raw_response_sha256=Sha256Digest(value="a" * 64),
        reference_basis=ThresholdReferenceBasis.OWNER_ASSUMED,
        reference_operator="test operator",
        reference_evidence="explicit test assumption",
        reference_approved_at=STAMP,
        thresholds=(ReportedLevelThreshold(level="warning", value=level),),
        curves=tuple(curve_snapshot(c) for c in curves),
    )


def evaluate(
    m: RatedThresholdManifest,
    prediction: np.ndarray | QuantileSample,
    observed: float,
    displacement: timedelta = timedelta(0),
) -> tuple[np.ndarray, np.ndarray, RatedThresholdSkillContext]:
    period = ForecastPeriodDefinition(
        grid=TimeGrid(step=DAY, phase=timedelta(hours=18)),
        label=PeriodLabel.START,
        aggregation=AggregationMethod.MEAN,
    )
    return paired_rated_probabilities(
        [PairedSkillSample(prediction=prediction, observed=observed, valid_time=STAMP)],
        m.thresholds[0],
        m,
        prepare_ratings(m),
        time_step=DAY,
        period=period,
        reference_displacement=displacement,
    )


class TestRatedThresholds:
    def test_partial_overlapping_curve_is_not_ignored(self, curve: RatingCurve) -> None:
        overlapping = replace(
            curve,
            id=RatingCurveId(UUID(int=99)),
            valid_from=ensure_utc(STAMP + timedelta(hours=12)),
        )
        _, _, context = evaluate(manifest([curve, overlapping]), np.array([60.0]), 70.0)
        assert context.scored_count == 0
        assert context.exclusions == (("conflicting_ratings", 1),)

    def test_floods_outside_curve_are_not_removed(self, curve: RatingCurve) -> None:
        probabilities, events, context = evaluate(
            manifest([curve]), np.array([-20.0, 1000.0]), 2000.0
        )
        assert probabilities.tolist() == [0.5]
        assert events.tolist() == [True]
        assert context.scored_count == 1
        assert not context.exclusions

    def test_strict_threshold_equality_is_not_an_event(
        self, curve: RatingCurve
    ) -> None:
        probabilities, events, _ = evaluate(
            manifest([curve]), np.array([50.0, 50.0]), 50.0
        )
        assert probabilities.tolist() == [0.0]
        assert events.tolist() == [False]

    def test_nonlinear_rating_preserves_original_quantile_cdf(
        self, curve: RatingCurve
    ) -> None:
        logarithmic = replace(
            curve,
            interpolation=InterpolationMethod.LOG_LINEAR,
            points=[
                {"water_level": 1.0, "discharge": 10.0},
                {"water_level": 3.0, "discharge": 1000.0},
            ],
        )
        prediction = QuantileSample(
            probabilities=(0.05, 0.5, 0.95), values=(10.0, 100.0, 1000.0)
        )
        probabilities, events, _ = evaluate(
            manifest([logarithmic], 1.5), prediction, 40.0
        )
        expected = 1 - (0.05 + (np.sqrt(1000) - 10) / 90 * 0.45)
        assert probabilities.tolist() == pytest.approx([expected])
        assert events.tolist() == [True]

    def test_unknown_quantile_tail_stays_unresolved(self, curve: RatingCurve) -> None:
        prediction = QuantileSample(
            probabilities=(0.05, 0.5, 0.95), values=(1.0, 2.0, 3.0)
        )
        probabilities, events, context = evaluate(manifest([curve]), prediction, 1000.0)
        assert probabilities.size == events.size == 0
        assert context.qualified_event_count == 1
        assert context.exclusions == (("unresolved_probability", 1),)

    def test_threshold_above_table_is_unavailable(self, curve: RatingCurve) -> None:
        probabilities, _, context = evaluate(
            manifest([curve], 11), np.array([500.0]), 500.0
        )
        assert probabilities.size == 0
        assert context.exclusions == (("threshold_outside_rating_range", 1),)

    def test_displaced_reference_must_fit_the_same_curve(
        self, curve: RatingCurve
    ) -> None:
        bounded = replace(curve, valid_to=ensure_utc(STAMP + DAY))
        _, _, context = evaluate(
            manifest([bounded]), np.array([50.0]), 50.0, timedelta(minutes=15)
        )
        assert context.exclusions == (("missing_or_boundary_rating", 1),)

    def test_period_ending_exactly_at_curve_boundary_is_supported(
        self, curve: RatingCurve
    ) -> None:
        bounded = replace(curve, valid_to=ensure_utc(STAMP + DAY))
        _, _, context = evaluate(manifest([bounded]), np.array([50.0]), 50.0)
        assert context.scored_count == 1

    def test_exact_duplicate_deliveries_do_not_create_conflict(
        self, curve: RatingCurve
    ) -> None:
        duplicate = replace(
            curve, id=RatingCurveId(UUID(int=9)), version=2, delivery_id="duplicate"
        )
        _, _, context = evaluate(manifest([curve, duplicate]), np.array([50.0]), 60.0)
        assert context.scored_count == 1
        assert len(context.curve_counts) == 1

    def test_conflicting_curves_do_not_choose_newest_silently(
        self, curve: RatingCurve
    ) -> None:
        other = replace(
            curve,
            id=RatingCurveId(UUID(int=9)),
            version=2,
            points=[
                {"water_level": 0.0, "discharge": 0.0},
                {"water_level": 10.0, "discharge": 200.0},
            ],
        )
        _, _, context = evaluate(manifest([curve, other]), np.array([50.0]), 60.0)
        assert context.exclusions == (("conflicting_ratings", 1),)

    def test_ambiguous_plateau_is_unavailable(self, curve: RatingCurve) -> None:
        flat = replace(
            curve,
            points=[
                {"water_level": 0.0, "discharge": 0.0},
                {"water_level": 4.0, "discharge": 50.0},
                {"water_level": 6.0, "discharge": 50.0},
                {"water_level": 10.0, "discharge": 100.0},
            ],
        )
        _, _, context = evaluate(manifest([flat]), np.array([50.0]), 60.0)
        assert context.exclusions == (("ambiguous_threshold_plateau", 1),)


class TestRatedManifest:
    def test_frozen_round_trip_preserves_digest(self, curve: RatingCurve) -> None:
        original = manifest([curve])
        parsed = parse_rated_threshold_manifest(original.content)
        assert parsed == original
        assert parsed.digest == original.digest

    def test_missing_reference_evidence_is_refused(self, curve: RatingCurve) -> None:
        with pytest.raises(ValueError, match="reference evidence"):
            replace(manifest([curve]), reference_evidence=" ")

    def test_wrong_station_curve_is_refused(self, curve: RatingCurve) -> None:
        with pytest.raises(ValueError, match="another station"):
            manifest([replace(curve, station_id=StationId(UUID(int=11)))])
