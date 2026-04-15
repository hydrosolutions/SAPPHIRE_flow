from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from sapphire_flow.exceptions import ModelSmokeTestError
from sapphire_flow.services.model_onboarding import (
    create_station_assignment,
    evaluate_skill_gate,
    smoke_test_model,
    validate_compatibility_for_unit,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ModelAssignmentStatus,
    SkillFreshness,
    SkillSource,
    SpatialRepresentation,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.model import ModelDataRequirements
from tests.conftest import (
    make_deployment_config,
    make_station_config,
    make_training_unit,
)
from tests.fakes.fake_models import FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeSkillStore,
    FakeStationStore,
)

_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_RNG = random.Random(42)
_CLOCK = lambda: _NOW  # noqa: E731


def _uuid() -> UUID:
    return UUID(int=random.Random(99).getrandbits(128), version=4)


def _make_skill_score(
    *,
    model_id: ModelId,
    artifact_id: ArtifactId,
    metric: str,
    score: float,
    sample_size: int = 200,
) -> object:
    from sapphire_flow.types.skill import SkillScore

    return SkillScore(
        id=uuid4(),
        station_id=StationId(uuid4()),
        model_id=model_id,
        parameter="discharge",
        model_artifact_id=artifact_id,
        skill_source=SkillSource.HINDCAST_REANALYSIS,
        forcing_type=None,
        computation_version=1,
        computed_at=_NOW,
        lead_time_hours=24,
        season=None,
        flow_regime=None,
        flow_regime_config_id=None,
        metric=metric,
        score=score,
        sample_size=sample_size,
        freshness=SkillFreshness.CURRENT,
        eval_period_start=_NOW,
        eval_period_end=_NOW,
        created_at=_NOW,
    )


def _make_model_with_reqs(
    *,
    target_parameters: frozenset[str] = frozenset({"discharge"}),
    past_dynamic: frozenset[str] = frozenset({"precipitation", "temperature"}),
    future_dynamic: frozenset[str] = frozenset(),
    static_features: frozenset[str] = frozenset(),
    supported_time_steps: frozenset[timedelta] | None = None,
) -> FakeStationForecastModel:
    model = FakeStationForecastModel()
    # FakeStationForecastModel is not a frozen dataclass — patch data_requirements
    model.__class__ = type(
        "PatchedModel",
        (FakeStationForecastModel,),
        {
            "data_requirements": ModelDataRequirements(
                target_parameters=target_parameters,
                past_dynamic_features=past_dynamic,
                future_dynamic_features=future_dynamic,
                static_features=static_features,
                supported_time_steps=supported_time_steps
                or frozenset({timedelta(days=1)}),
                lookback_steps=7,
                forecast_horizon_steps=5,
                spatial_input_type=SpatialRepresentation.POINT,
            )
        },
    )
    return model


class TestValidateCompatibility:
    def _call(
        self,
        model: FakeStationForecastModel,
        station_forecast_targets: frozenset[str] | None,
        available_features: frozenset[str],
        available_static: frozenset[str],
        time_step: timedelta,
    ) -> object:
        station = make_station_config(forecast_targets=station_forecast_targets)
        station_store = FakeStationStore()
        station_store.store_station(station)
        unit = make_training_unit(model_id=ModelId("test_model"), station_id=station.id)
        return validate_compatibility_for_unit(
            model_id=ModelId("test_model"),
            model=model,
            unit=unit,
            station_store=station_store,
            group_store=None,  # type: ignore[arg-type]
            available_features=available_features,
            available_static_by_station={station.id: available_static},
            requested_time_step=time_step,
        )

    def test_compatible_station(self) -> None:
        model = _make_model_with_reqs()
        report = self._call(
            model,
            station_forecast_targets=frozenset({"discharge"}),
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset(),
            time_step=timedelta(days=1),
        )
        assert report.is_compatible  # type: ignore[union-attr]

    def test_missing_target_parameters(self) -> None:
        model = _make_model_with_reqs()
        report = self._call(
            model,
            station_forecast_targets=None,
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset(),
            time_step=timedelta(days=1),
        )
        assert not report.is_compatible  # type: ignore[union-attr]
        assert "discharge" in report.missing_target_parameters  # type: ignore[union-attr]

    def test_missing_past_dynamic_features(self) -> None:
        model = _make_model_with_reqs(
            past_dynamic=frozenset({"precipitation", "temperature", "wind_speed"})
        )
        report = self._call(
            model,
            station_forecast_targets=frozenset({"discharge"}),
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset(),
            time_step=timedelta(days=1),
        )
        assert not report.is_compatible  # type: ignore[union-attr]
        assert "wind_speed" in report.missing_past_dynamic  # type: ignore[union-attr]

    def test_time_step_incompatible(self) -> None:
        model = _make_model_with_reqs(
            supported_time_steps=frozenset({timedelta(hours=1)})
        )
        report = self._call(
            model,
            station_forecast_targets=frozenset({"discharge"}),
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset(),
            time_step=timedelta(days=1),
        )
        assert not report.is_compatible  # type: ignore[union-attr]
        assert not report.time_step_compatible  # type: ignore[union-attr]

    def test_compatible_with_static_features(self) -> None:
        model = _make_model_with_reqs(static_features=frozenset({"elevation", "area"}))
        report = self._call(
            model,
            station_forecast_targets=frozenset({"discharge"}),
            available_features=frozenset({"precipitation", "temperature"}),
            available_static=frozenset({"elevation", "area"}),
            time_step=timedelta(days=1),
        )
        assert report.is_compatible  # type: ignore[union-attr]


class TestSmokeTestModel:
    def test_passes_for_valid_model(self) -> None:
        model = FakeStationForecastModel()
        rng = random.Random(7)
        smoke_test_model(model=model, rng=rng)

    def test_fails_for_broken_model(self) -> None:
        class BrokenModel(FakeStationForecastModel):
            def train(self, data, params, rng):  # type: ignore[override]
                raise RuntimeError("training exploded")

        model = BrokenModel()
        rng = random.Random(7)
        with pytest.raises(ModelSmokeTestError, match="training exploded"):
            smoke_test_model(model=model, rng=rng)


class TestEvaluateSkillGate:
    def test_empty_thresholds_passes(self) -> None:
        skill_store = FakeSkillStore()
        model_id = ModelId("m1")
        artifact_id = ArtifactId(uuid4())
        config = make_deployment_config(skill_gate_thresholds={})

        result = evaluate_skill_gate(
            model_id=model_id,
            model_artifact_id=artifact_id,
            skill_store=skill_store,
            config=config,
        )

        assert result.passed
        assert result.failing_metrics == frozenset()

    def test_passes_with_sufficient_scores(self) -> None:
        from sapphire_flow.types.model_onboarding import SkillGateMetric

        skill_store = FakeSkillStore()
        model_id = ModelId("m2")
        artifact_id = ArtifactId(uuid4())
        score = _make_skill_score(
            model_id=model_id, artifact_id=artifact_id, metric="nse", score=0.8
        )
        skill_store._scores.append(score)  # type: ignore[attr-defined]

        config = make_deployment_config(
            skill_gate_thresholds={
                "nse": SkillGateMetric(threshold=0.5, higher_is_better=True)
            },
            min_skill_samples=100,
        )

        result = evaluate_skill_gate(
            model_id=model_id,
            model_artifact_id=artifact_id,
            skill_store=skill_store,
            config=config,
        )

        assert result.passed
        assert result.failing_metrics == frozenset()

    def test_fails_with_insufficient_scores(self) -> None:
        from sapphire_flow.types.model_onboarding import SkillGateMetric

        skill_store = FakeSkillStore()
        model_id = ModelId("m3")
        artifact_id = ArtifactId(uuid4())
        score = _make_skill_score(
            model_id=model_id, artifact_id=artifact_id, metric="nse", score=0.2
        )
        skill_store._scores.append(score)  # type: ignore[attr-defined]

        config = make_deployment_config(
            skill_gate_thresholds={
                "nse": SkillGateMetric(threshold=0.5, higher_is_better=True)
            },
            min_skill_samples=100,
        )

        result = evaluate_skill_gate(
            model_id=model_id,
            model_artifact_id=artifact_id,
            skill_store=skill_store,
            config=config,
        )

        assert not result.passed
        assert "nse" in result.failing_metrics

    def test_skipped_insufficient_eval(self) -> None:
        from sapphire_flow.types.model_onboarding import SkillGateMetric

        skill_store = FakeSkillStore()
        model_id = ModelId("m4")
        artifact_id = ArtifactId(uuid4())
        # Score exists but sample_size < min_skill_samples — filtered out
        score = _make_skill_score(
            model_id=model_id,
            artifact_id=artifact_id,
            metric="nse",
            score=0.8,
            sample_size=5,
        )
        skill_store._scores.append(score)  # type: ignore[attr-defined]

        config = make_deployment_config(
            skill_gate_thresholds={
                "nse": SkillGateMetric(threshold=0.5, higher_is_better=True)
            },
            min_skill_samples=100,
        )

        result = evaluate_skill_gate(
            model_id=model_id,
            model_artifact_id=artifact_id,
            skill_store=skill_store,
            config=config,
        )

        # No valid strata → metric_scores is empty, gate fails (no data for nse)
        assert len(result.metric_scores) == 0
        assert not result.passed


class TestCreateAssignment:
    def test_creates_station_assignment(self) -> None:
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)
        model_id = ModelId("my_model")

        assignment = create_station_assignment(
            station_id=station.id,
            model_id=model_id,
            time_step=timedelta(days=1),
            priority=0,
            station_store=station_store,
            clock=_CLOCK,
        )

        assert assignment.station_id == station.id
        assert assignment.model_id == model_id
        assert assignment.status == ModelAssignmentStatus.ACTIVE

    def test_skips_inactive_assignment(self) -> None:
        from sapphire_flow.types.station import ModelAssignment

        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)
        model_id = ModelId("my_model")

        # Pre-seed an INACTIVE assignment
        inactive = ModelAssignment(
            station_id=station.id,
            model_id=model_id,
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.INACTIVE,
            priority=0,
            created_at=_NOW,
        )
        station_store._assignments.append(inactive)  # type: ignore[attr-defined]

        returned = create_station_assignment(
            station_id=station.id,
            model_id=model_id,
            time_step=timedelta(days=1),
            priority=0,
            station_store=station_store,
            clock=_CLOCK,
        )

        # Returns the existing inactive assignment, does not overwrite
        assert returned.status == ModelAssignmentStatus.INACTIVE
        assignments = station_store.fetch_model_assignments(station.id)
        assert len(assignments) == 1
        assert assignments[0].status == ModelAssignmentStatus.INACTIVE
