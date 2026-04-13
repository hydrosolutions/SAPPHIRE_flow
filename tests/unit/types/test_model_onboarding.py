from __future__ import annotations

from uuid import uuid4

import pytest

from sapphire_flow.types.enums import OnboardingOutcome
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
from sapphire_flow.types.model_onboarding import (
    CompatibilityReport,
    ModelOnboardingResult,
    SkillGateResult,
)
from tests.conftest import make_compatibility_report, make_training_unit

_MODEL = ModelId("test_model")
_STATION = StationId(uuid4())
_GROUP = StationGroupId(uuid4())
_ARTIFACT = ArtifactId(uuid4())


def _make_report() -> CompatibilityReport:
    return make_compatibility_report(model_id=_MODEL, station_id=_STATION)


class TestCompatibilityReportPostInit:
    def test_both_ids_set_raises(self) -> None:
        with pytest.raises(ValueError, match="Exactly one of station_id or group_id"):
            CompatibilityReport(
                model_id=_MODEL,
                station_id=_STATION,
                group_id=_GROUP,
                protocol_conforms=True,
                missing_target_parameters=frozenset(),
                missing_past_dynamic=frozenset(),
                missing_future_dynamic=frozenset(),
                missing_static_features=frozenset(),
                time_step_compatible=True,
            )

    def test_neither_id_set_raises(self) -> None:
        with pytest.raises(ValueError, match="Exactly one of station_id or group_id"):
            CompatibilityReport(
                model_id=_MODEL,
                station_id=None,
                group_id=None,
                protocol_conforms=True,
                missing_target_parameters=frozenset(),
                missing_past_dynamic=frozenset(),
                missing_future_dynamic=frozenset(),
                missing_static_features=frozenset(),
                time_step_compatible=True,
            )


class TestSkillGateResultPostInit:
    def test_duplicate_metric_in_scores_raises(self) -> None:
        with pytest.raises(ValueError, match="Duplicate metric name in metric_scores"):
            SkillGateResult(
                model_artifact_id=_ARTIFACT,
                metric_scores=(("crps", 0.5), ("crps", 0.6)),
                thresholds=(("crps", 0.7, True),),
                failing_metrics=frozenset(),
            )

    def test_duplicate_metric_in_thresholds_raises(self) -> None:
        with pytest.raises(ValueError, match="Duplicate metric name in thresholds"):
            SkillGateResult(
                model_artifact_id=_ARTIFACT,
                metric_scores=(("crps", 0.5),),
                thresholds=(("crps", 0.7, True), ("crps", 0.8, True)),
                failing_metrics=frozenset(),
            )


def _make_unit_result(outcome: OnboardingOutcome) -> object:
    from sapphire_flow.types.model_onboarding import OnboardingUnitResult

    report = _make_report()
    unit = make_training_unit(station_id=_STATION)
    return OnboardingUnitResult(
        unit=unit,
        outcome=outcome,
        compatibility=report,
        artifact_id=None,
        hindcast_steps=(),
        skill_gate=None,
    )


class TestModelOnboardingResultCounts:
    def _build_result(self, outcomes: list[OnboardingOutcome]) -> ModelOnboardingResult:
        units = tuple(_make_unit_result(o) for o in outcomes)  # type: ignore[arg-type]
        return ModelOnboardingResult(model_id=_MODEL, units=units)

    def test_promoted_count(self) -> None:
        result = self._build_result(
            [
                OnboardingOutcome.PROMOTED,
                OnboardingOutcome.PROMOTED,
                OnboardingOutcome.FAILED_TRAINING,
            ]
        )
        assert result.promoted_count() == 2

    def test_failed_count(self) -> None:
        result = self._build_result(
            [
                OnboardingOutcome.FAILED_SMOKE_TEST,
                OnboardingOutcome.FAILED_TRAINING,
                OnboardingOutcome.FAILED_HINDCAST,
                OnboardingOutcome.FAILED_SKILL,
                OnboardingOutcome.FAILED_ASSIGNMENT,
                OnboardingOutcome.PROMOTED,
            ]
        )
        assert result.failed_count() == 5

    def test_skipped_count(self) -> None:
        result = self._build_result(
            [
                OnboardingOutcome.SKIPPED_COMPAT,
                OnboardingOutcome.SKIPPED_NO_DATA,
                OnboardingOutcome.SKIPPED_INSUFFICIENT_EVAL,
                OnboardingOutcome.PROMOTED,
            ]
        )
        assert result.skipped_count() == 3

    def test_gate_rejected_count(self) -> None:
        result = self._build_result(
            [
                OnboardingOutcome.GATE_REJECTED,
                OnboardingOutcome.GATE_REJECTED,
                OnboardingOutcome.PROMOTED,
            ]
        )
        assert result.gate_rejected_count() == 2

    def test_all_zero_for_empty(self) -> None:
        result = ModelOnboardingResult(model_id=_MODEL, units=())
        assert result.promoted_count() == 0
        assert result.failed_count() == 0
        assert result.skipped_count() == 0
        assert result.gate_rejected_count() == 0
