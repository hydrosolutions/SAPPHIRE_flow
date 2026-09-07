from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import polars as pl
import pytest

from sapphire_flow.services.skill.combined_skill import (
    compute_bma_skill_cross_validated,
    compute_combined_skill,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import SeasonDefinition, StationThreshold
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForcingType,
    ModelCombinationStrategy,
    ObservationSource,
    QcStatus,
    SkillSource,
    ThresholdSource,
)
from sapphire_flow.types.ids import (
    BMA_MODEL_ID,
    POOLED_MODEL_ID,
    ArtifactId,
    HindcastForecastId,
    ModelId,
    ObservationId,
    StationId,
)
from sapphire_flow.types.observation import Observation

_EPOCH = ensure_utc(datetime(2025, 1, 15, 0, 0, tzinfo=UTC))
_RNG = random.Random(99)


def _uuid() -> UUID:
    return UUID(int=_RNG.getrandbits(128), version=4)


def _utc(year: int, month: int, day: int, hour: int = 0) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _make_hindcast(
    *,
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    hindcast_step: UtcDatetime,
    n_members: int = 5,
    n_steps: int = 3,
    value: float = 10.0,
    parameter: str = "discharge",
) -> object:
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast import HindcastForecast

    rows = []
    time_step = timedelta(hours=1)
    for step in range(n_steps):
        vt = ensure_utc(
            datetime.fromtimestamp(
                hindcast_step.timestamp() + (step + 1) * 3600, tz=UTC
            )
        )
        for m in range(n_members):
            rows.append({"valid_time": vt, "member_id": m, "value": value + m * 0.1})

    df = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
    )
    units = "m³/s" if parameter == "discharge" else "m"
    ensemble = ForecastEnsemble.from_members(
        station_id=station_id,
        issued_at=hindcast_step,
        parameter=parameter,
        units=units,
        time_step=time_step,
        values=df,
    )
    return HindcastForecast(
        id=HindcastForecastId(_uuid()),
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=artifact_id,
        hindcast_step=hindcast_step,
        forcing_type=ForcingType.REANALYSIS,
        representation=EnsembleRepresentation.MEMBERS,
        hindcast_run_id=_uuid(),
        ensemble=ensemble,
        created_at=_EPOCH,
    )


def _make_observation(
    *,
    station_id: StationId,
    timestamp: UtcDatetime,
    value: float = 10.0,
    parameter: str = "discharge",
) -> Observation:
    return Observation(
        id=ObservationId(_uuid()),
        station_id=station_id,
        timestamp=timestamp,
        parameter=parameter,
        value=value,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus.QC_PASSED,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_EPOCH,
    )


def _make_threshold(
    *, station_id: StationId, danger_level: str = "moderate"
) -> StationThreshold:
    return StationThreshold(
        station_id=station_id,
        danger_level=danger_level,
        parameter="discharge",
        value=10.5,
        source=ThresholdSource.AUTHORITY,
        created_at=_EPOCH,
        updated_at=_EPOCH,
    )


@pytest.fixture
def station_id() -> StationId:
    return StationId(_uuid())


@pytest.fixture
def artifact_id_a() -> ArtifactId:
    return ArtifactId(_uuid())


@pytest.fixture
def artifact_id_b() -> ArtifactId:
    return ArtifactId(_uuid())


@pytest.fixture
def model_a() -> ModelId:
    return ModelId("model_a")


@pytest.fixture
def model_b() -> ModelId:
    return ModelId("model_b")


@pytest.fixture
def clock() -> object:
    return lambda: _EPOCH


@pytest.fixture
def seasons() -> list[SeasonDefinition]:
    return [SeasonDefinition(name="winter", months=frozenset({12, 1, 2}))]


class TestTwoModelsFullOverlap:
    def test_combined_skill_computed_on_all_steps(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        # Both models have hindcasts at same 5 steps
        steps = [_utc(2025, 1, i + 1) for i in range(5)]
        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps
        ]

        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
            )
            for s in steps
        ]

        scores, diagrams = compute_combined_skill(
            station_id=station_id,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
        )

        assert len(scores) > 0
        assert len(diagrams) > 0
        # All scores have POOLED_MODEL_ID
        assert all(s.model_id == POOLED_MODEL_ID for s in scores)
        # artifact_id is None for combined scores
        assert all(s.model_artifact_id is None for s in scores)


class TestPartialOverlap:
    def test_combined_skill_on_intersection_only(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        # Model A: steps 1-5, Model B: steps 3-7 → intersection 3-5
        steps_a = [_utc(2025, 1, i + 1) for i in range(5)]  # days 1-5
        steps_b = [_utc(2025, 1, i + 3) for i in range(5)]  # days 3-7

        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_a
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_b
        ]

        # Create observations for all days 1-7
        all_steps = sorted(set(steps_a) | set(steps_b))
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
            )
            for s in all_steps
        ]

        scores, _ = compute_combined_skill(
            station_id=station_id,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
        )

        # Should get scores (intersection = days 3-5, i.e. 3 steps)
        assert len(scores) > 0
        aggregate = [
            s
            for s in scores
            if s.lead_time_hours == 1 and s.season is None and s.flow_regime is None
        ]
        assert len(aggregate) > 0
        # sample_size should be 3 (intersection of 3 steps)
        assert all(s.sample_size == 3 for s in aggregate)


class TestNoOverlap:
    def test_no_intersection_returns_empty(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        # Model A: days 1-3, Model B: days 4-6 → no intersection
        steps_a = [_utc(2025, 1, i + 1) for i in range(3)]
        steps_b = [_utc(2025, 1, i + 4) for i in range(3)]

        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_a
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_b
        ]

        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=_utc(2025, 1, 5),
            )
        ]

        scores, diagrams = compute_combined_skill(
            station_id=station_id,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
        )

        assert scores == []
        assert diagrams == []


class TestSingleModel:
    def test_single_model_yields_no_combined_skill(
        self,
        station_id: StationId,
        model_a: ModelId,
        artifact_id_a: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        """Plan 222 T3 (D2) — `combine_ensembles_pooled` now requires at
        least two `MEMBERS` contributors per parameter; a single-model
        POOLED call produces no combined ensemble for any step, so
        `compute_combined_skill` computes nothing. Pre-Plan-222 this
        "pooled" a single model on its own. The real caller
        (`flows/compute_skills.py`) already gates `len(hindcasts_by_model)
        < 2` before reaching here — this direct-call test locks the
        underlying function's own behaviour, not just the caller's guard."""
        steps = [_utc(2025, 1, i + 1) for i in range(4)]
        hindcasts = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps
        ]
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
            )
            for s in steps
        ]

        scores, diagrams = compute_combined_skill(
            station_id=station_id,
            parameter="discharge",
            strategy=ModelCombinationStrategy.POOLED,
            hindcasts_by_model={model_a: hindcasts},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
        )

        assert scores == []
        assert diagrams == []


class TestCoverageLogging:
    def test_coverage_log_message(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        from structlog.testing import capture_logs

        # Model A: days 1-4, Model B: days 3-6 → intersection days 3-4 (2 steps)
        steps_a = [_utc(2025, 1, i + 1) for i in range(4)]
        steps_b = [_utc(2025, 1, i + 3) for i in range(4)]

        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_a
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps_b
        ]

        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
            )
            for s in sorted(set(steps_a) | set(steps_b))
        ]

        with capture_logs() as captured:
            compute_combined_skill(
                station_id=station_id,
                parameter="discharge",
                strategy=ModelCombinationStrategy.POOLED,
                hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
                observations=observations,
                thresholds=[],
                flow_regime_config=None,
                seasons=seasons,
                skill_source=SkillSource.HINDCAST_REANALYSIS,
                forcing_type=ForcingType.REANALYSIS,
                clock=clock,  # type: ignore[arg-type]
                uuid_factory=uuid4,
            )

        coverage_events = [
            e for e in captured if e.get("event") == "combined_skill.coverage"
        ]
        assert len(coverage_events) == 1
        assert coverage_events[0]["intersection_steps"] == 2


class TestBmaStrategy:
    def test_bma_without_weights_raises(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        steps = [_utc(2025, 1, i + 1) for i in range(5)]
        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps
        ]

        with pytest.raises(ValueError, match="BMA strategy requires weights"):
            compute_combined_skill(
                station_id=station_id,
                parameter="discharge",
                strategy=ModelCombinationStrategy.BMA,
                hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
                observations=[],
                thresholds=[],
                flow_regime_config=None,
                seasons=seasons,
                skill_source=SkillSource.HINDCAST_REANALYSIS,
                forcing_type=ForcingType.REANALYSIS,
                clock=clock,  # type: ignore[arg-type]
                uuid_factory=uuid4,
            )

    def test_bma_with_weights_uses_bma_model_id(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        steps = [_utc(2025, 1, i + 1) for i in range(5)]
        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
            )
            for s in steps
        ]
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
            )
            for s in steps
        ]
        weights = {model_a: 0.6, model_b: 0.4}

        scores, diagrams = compute_combined_skill(
            station_id=station_id,
            parameter="discharge",
            strategy=ModelCombinationStrategy.BMA,
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
            weights=weights,
        )

        assert len(scores) > 0
        assert all(s.model_id == BMA_MODEL_ID for s in scores)


class TestBmaCrossValidation:
    def test_cross_validation_splits_into_halves(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        # 10 steps → two folds of 5
        steps = [_utc(2025, 1, i + 1) for i in range(10)]
        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
                value=10.0,
            )
            for s in steps
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
                value=12.0,
            )
            for s in steps
        ]
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
                value=11.0,
            )
            for s in steps
        ]

        scores, _ = compute_bma_skill_cross_validated(
            station_id=station_id,
            parameter="discharge",
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
            skill_store=None,
        )

        assert len(scores) > 0
        assert all(s.model_id == BMA_MODEL_ID for s in scores)

    def test_cross_validation_returns_averaged_scores(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        steps = [_utc(2025, 1, i + 1) for i in range(8)]
        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
                value=10.0,
            )
            for s in steps
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
                value=12.0,
            )
            for s in steps
        ]
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
                value=11.0,
            )
            for s in steps
        ]

        scores, diagrams = compute_bma_skill_cross_validated(
            station_id=station_id,
            parameter="discharge",
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
            skill_store=None,
        )

        assert isinstance(scores, list)
        assert isinstance(diagrams, list)
        assert all(s.model_id == BMA_MODEL_ID for s in scores)


class TestBmaCrossValidationRequiresBothFolds:
    """Independent-review fixer round (blocker): a BMA cohort with only ONE
    successful cross-validation fold is not a validated two-fold average —
    it is a single held-out evaluation. ``_scores_for_fold`` can legitimately
    return ``([], [])`` on its own (e.g. no usable BMA weights from that
    fold's training half); the OTHER fold's real result must not be
    published alone as if cross-validation had succeeded, because the flow
    level completeness gate (``compute_skills.compute_combined_skills_task``)
    only refuses to publish when this function itself reports nothing.
    """

    def test_one_incomplete_fold_yields_no_scores_or_diagrams(
        self,
        monkeypatch: pytest.MonkeyPatch,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        from sapphire_flow.services.skill import combined_skill

        # Same shape as the passing `TestBmaCrossValidation` tests above —
        # left alone, both folds would succeed.
        steps = [_utc(2025, 1, i + 1) for i in range(10)]
        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
                value=10.0,
            )
            for s in steps
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
                value=12.0,
            )
            for s in steps
        ]
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
                value=11.0,
            )
            for s in steps
        ]

        # Force exactly the FIRST fold's training step to come back with no
        # usable BMA weights (as a degenerate/insufficient training split
        # would), while the second fold computes real weights normally.
        real_compute_bma_weights = combined_skill.compute_bma_weights
        calls = {"n": 0}

        def _first_call_has_no_weights(**kwargs: object) -> dict[ModelId, float]:
            calls["n"] += 1
            if calls["n"] == 1:
                return {}
            return real_compute_bma_weights(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            combined_skill, "compute_bma_weights", _first_call_has_no_weights
        )

        scores, diagrams = compute_bma_skill_cross_validated(
            station_id=station_id,
            parameter="discharge",
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
            skill_store=None,
        )

        assert calls["n"] == 2, "both folds must have attempted weight computation"
        assert scores == []
        assert diagrams == []


class TestBmaCrossValidationObservationBounds:
    """Plan 228 review fixer round (blocker): ``_obs_for_steps`` used to
    truncate observations to ``[min(hindcast_step), max(hindcast_step)]`` —
    the ISSUE times, not the forecasts' ``valid_time``s. For a SINGLE
    hindcast that range collapses (``min == max``), and because
    observations are stamped at ``valid_time`` (always strictly AFTER
    ``hindcast_step``), a one-step fold matched ZERO observations. With
    exactly two hindcast steps, BOTH one-step folds hit this — every
    cross-validated BMA score silently came from no data at all."""

    def test_two_step_cv_both_single_hindcast_folds_score_against_real_observations(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        # Exactly 2 hindcast steps -> mid=1 -> two folds, each a SINGLE
        # hindcast_step. min(step_set) == max(step_set) for every fold.
        steps = [_utc(2025, 1, 1), _utc(2025, 1, 2)]
        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
                value=10.0,
            )
            for s in steps
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
                value=12.0,
            )
            for s in steps
        ]
        # Every observation sits at its forecast's valid_time (hindcast_step
        # + 1h, per `_make_hindcast`'s fixed hourly time_step) — strictly
        # AFTER hindcast_step, so `min(step_set) <= ts <= max(step_set)`
        # (the buggy bound) never matches it for a single-step fold.
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
                value=11.0,
            )
            for s in steps
        ]

        scores, _ = compute_bma_skill_cross_validated(
            station_id=station_id,
            parameter="discharge",
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
            skill_store=None,
        )

        assert len(scores) > 0, (
            "both one-step folds produced no skill scores at all — "
            "observations were bounded by hindcast_step (issue time) "
            "instead of the forecasts' own valid_time"
        )
        assert all(s.sample_size >= 1 for s in scores), (
            "a skill score was computed with zero matched observations"
        )


class TestBmaCrossValidationDiagramMerge:
    """Plan 235 fixer round (major): `compute_bma_skill_cross_validated`'s
    two CV folds independently produce a diagram at the SAME natural key
    (same station/model/parameter/skill_source/diagram_type/threshold_level
    /lead_time_hours, same `generation_id`) — fold 1 evaluates half_2, fold
    2 evaluates half_1. The old `fold1_diagrams + fold2_diagrams`
    concatenation returned both, so `store_skill_diagrams`'s
    `ON CONFLICT DO NOTHING` silently dropped one of every colliding pair
    while `diagram_count` still counted both as published.
    """

    def test_merged_diagrams_have_no_duplicate_natural_keys(
        self,
        station_id: StationId,
        model_a: ModelId,
        model_b: ModelId,
        artifact_id_a: ArtifactId,
        artifact_id_b: ArtifactId,
        clock: object,
        seasons: list[SeasonDefinition],
    ) -> None:
        steps = [_utc(2025, 1, i + 1) for i in range(8)]
        hindcasts_a = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_a,
                artifact_id=artifact_id_a,
                hindcast_step=s,
                n_steps=1,
                value=10.0,
            )
            for s in steps
        ]
        hindcasts_b = [
            _make_hindcast(
                station_id=station_id,
                model_id=model_b,
                artifact_id=artifact_id_b,
                hindcast_step=s,
                n_steps=1,
                value=12.0,
            )
            for s in steps
        ]
        observations = [
            _make_observation(
                station_id=station_id,
                timestamp=ensure_utc(
                    datetime.fromtimestamp(s.timestamp() + 3600, tz=UTC)
                ),
                value=11.0,
            )
            for s in steps
        ]

        _, diagrams = compute_bma_skill_cross_validated(
            station_id=station_id,
            parameter="discharge",
            hindcasts_by_model={model_a: hindcasts_a, model_b: hindcasts_b},
            observations=observations,
            thresholds=[_make_threshold(station_id=station_id)],
            flow_regime_config=None,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,  # type: ignore[arg-type]
            uuid_factory=uuid4,
            skill_store=None,
        )

        assert len(diagrams) > 0
        natural_keys = [
            (
                d.model_id,
                d.model_artifact_id,
                d.parameter,
                d.skill_source,
                d.computation_version,
                d.lead_time_hours,
                d.season,
                d.flow_regime,
                d.diagram_type,
                d.threshold_level,
                d.time_step_seconds,
                d.phase_offset_seconds,
            )
            for d in diagrams
        ]
        assert len(natural_keys) == len(set(natural_keys)), (
            "two diagrams share an identical natural key — a real store "
            "would drop one of them via ON CONFLICT DO NOTHING while this "
            "count still reports both as published"
        )
        # Confirms the two folds' data were actually MERGED, not just
        # deduplicated by dropping one — a rank_histogram's counts sum to
        # the number of (obs, ensemble) pairs evaluated across BOTH folds
        # together, not just one fold's half.
        rank_histograms = [d for d in diagrams if d.diagram_type == "rank_histogram"]
        assert rank_histograms
        for rh in rank_histograms:
            assert sum(rh.data["counts"]) == len(steps), (
                "merged rank_histogram counts must cover every evaluated "
                "step across both CV folds, not just one fold's half"
            )


def _make_roc_diagram(
    *,
    station_id: StationId,
    model_id: ModelId,
    hit_rate: list[float],
    false_alarm_rate: list[float],
    n_events: int,
    n_non_events: int,
    eval_period_start: UtcDatetime,
    eval_period_end: UtcDatetime,
) -> object:
    from sapphire_flow.types.skill import SkillDiagram

    return SkillDiagram(
        id=_uuid(),
        station_id=station_id,
        model_id=model_id,
        parameter="discharge",
        model_artifact_id=None,
        skill_source=SkillSource.HINDCAST_REANALYSIS,
        computation_version=2,
        lead_time_hours=24,
        season=None,
        flow_regime=None,
        flow_regime_config_id=None,
        diagram_type="roc",
        threshold_level=None,
        data={
            "thresholds": [0.0, 0.5, 1.0],
            "hit_rate": hit_rate,
            "false_alarm_rate": false_alarm_rate,
            "n_events": n_events,
            "n_non_events": n_non_events,
        },
        eval_period_start=eval_period_start,
        eval_period_end=eval_period_end,
        created_at=eval_period_end,
    )


class TestMergeFoldDiagramsRocWeighting:
    """Fixer round (major): `_merge_diagram_data`'s "roc" branch used to
    average `hit_rate`/`false_alarm_rate` across folds UNWEIGHTED
    (`_nanmean`), even though each fold's rate is a ratio over its OWN
    event/non-event count (`services.skill.diagrams.compute_roc_curve`) —
    giving a fold with 3 events the same say as a fold with 300. It must
    weight by each fold's `n_events`/`n_non_events` instead.
    """

    def test_roc_merge_weights_by_event_count_not_equal_average(self) -> None:
        from sapphire_flow.services.skill.combined_skill import _merge_fold_diagrams

        sid = StationId(_uuid())
        mid = ModelId("test")
        t0 = _utc(2025, 1, 1)
        t1 = _utc(2025, 1, 8)
        t2 = _utc(2025, 1, 15)

        # Fold 1: tiny evaluation window, 1 event, hit_rate=0.0.
        fold1 = _make_roc_diagram(
            station_id=sid,
            model_id=mid,
            hit_rate=[0.0, 0.0, 0.0],
            false_alarm_rate=[1.0, 0.5, 0.0],
            n_events=1,
            n_non_events=9,
            eval_period_start=t0,
            eval_period_end=t1,
        )
        # Fold 2: large evaluation window, 99 events, hit_rate=1.0.
        fold2 = _make_roc_diagram(
            station_id=sid,
            model_id=mid,
            hit_rate=[1.0, 1.0, 1.0],
            false_alarm_rate=[1.0, 0.5, 0.0],
            n_events=99,
            n_non_events=1,
            eval_period_start=t1,
            eval_period_end=t2,
        )

        merged = _merge_fold_diagrams([fold1], [fold2])  # type: ignore[list-item]
        assert len(merged) == 1
        merged_hit_rate = merged[0].data["hit_rate"]

        # An UNWEIGHTED average would give 0.5 at every threshold. Weighted
        # by event count (1 vs 99), the merged rate must sit much closer to
        # fold 2's 1.0 than to the midpoint.
        assert all(h > 0.9 for h in merged_hit_rate), (
            f"expected hit_rate weighted toward the 99-event fold, got "
            f"{merged_hit_rate} (an unweighted average would be 0.5)"
        )

    def test_merged_diagram_eval_period_spans_both_folds(self) -> None:
        """Fixer round (major): `replace(first, data=...)` kept only fold
        1's `eval_period_start`/`eval_period_end`, even though the merged
        data spans both folds' evaluation windows."""
        from sapphire_flow.services.skill.combined_skill import _merge_fold_diagrams

        sid = StationId(_uuid())
        mid = ModelId("test")
        t0 = _utc(2025, 1, 1)
        t1 = _utc(2025, 1, 8)
        t2 = _utc(2025, 1, 15)

        fold1 = _make_roc_diagram(
            station_id=sid,
            model_id=mid,
            hit_rate=[0.5, 0.5, 0.5],
            false_alarm_rate=[0.5, 0.5, 0.5],
            n_events=10,
            n_non_events=10,
            eval_period_start=t0,
            eval_period_end=t1,
        )
        fold2 = _make_roc_diagram(
            station_id=sid,
            model_id=mid,
            hit_rate=[0.5, 0.5, 0.5],
            false_alarm_rate=[0.5, 0.5, 0.5],
            n_events=10,
            n_non_events=10,
            eval_period_start=t1,
            eval_period_end=t2,
        )

        merged = _merge_fold_diagrams([fold1], [fold2])  # type: ignore[list-item]
        assert len(merged) == 1
        assert merged[0].eval_period_start == t0, (
            "merged eval_period_start must be the EARLIEST of both folds"
        )
        assert merged[0].eval_period_end == t2, (
            "merged eval_period_end must be the LATEST of both folds, not "
            "fold 1's alone"
        )
