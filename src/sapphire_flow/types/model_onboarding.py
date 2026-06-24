from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import OnboardingOutcome

if TYPE_CHECKING:
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
    from sapphire_flow.types.training import HindcastStepResult, TrainingUnit


@dataclass(frozen=True, kw_only=True, slots=True)
class CompatibilityReport:
    model_id: ModelId
    station_id: StationId | None
    group_id: StationGroupId | None
    protocol_conforms: bool
    missing_target_parameters: frozenset[str]
    missing_past_dynamic: frozenset[str]
    missing_future_dynamic: frozenset[str]
    missing_static_features: frozenset[str]
    time_step_compatible: bool
    fi_unit_mismatches: frozenset[str] = frozenset()
    fi_unsupported_units: frozenset[str] = frozenset()
    spatial_type_supported: bool = True
    station_codes_resolvable: bool = True

    def __post_init__(self) -> None:
        if self.station_id is not None and self.group_id is not None:
            raise ValueError(
                "Exactly one of station_id or group_id must be set, not both"
            )
        if self.station_id is None and self.group_id is None:
            raise ValueError("Exactly one of station_id or group_id must be set")

    @property
    def is_compatible(self) -> bool:
        return (
            self.protocol_conforms
            and not self.missing_target_parameters
            and not self.missing_past_dynamic
            and not self.missing_future_dynamic
            and not self.missing_static_features
            and self.time_step_compatible
            and not self.fi_unit_mismatches
            and not self.fi_unsupported_units
            and self.spatial_type_supported
            and self.station_codes_resolvable
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class SkillGateResult:
    model_artifact_id: ArtifactId
    metric_scores: tuple[tuple[str, float], ...]
    thresholds: tuple[tuple[str, float, bool], ...]
    failing_metrics: frozenset[str]

    def __post_init__(self) -> None:
        score_keys = {k for k, _ in self.metric_scores}
        if len(score_keys) != len(self.metric_scores):
            raise ValueError("Duplicate metric name in metric_scores")
        thresh_keys = {k for k, *_ in self.thresholds}
        if len(thresh_keys) != len(self.thresholds):
            raise ValueError("Duplicate metric name in thresholds")

    @property
    def passed(self) -> bool:
        return not self.failing_metrics


@dataclass(frozen=True, kw_only=True, slots=True)
class SkillGateMetric:
    threshold: float
    higher_is_better: bool = True


SUPPORTED_SKILL_METRICS: frozenset[str] = frozenset(
    {
        "crpss",
        "nse",
        "kge",
        "crps",
        "rmse",
        "mae",
        "bias",
        "brier_score",
        "reliability",
    }
)

ONBOARDING_FAILED_OUTCOMES: frozenset[OnboardingOutcome] = frozenset(
    {
        OnboardingOutcome.FAILED_SMOKE_TEST,
        OnboardingOutcome.FAILED_TRAINING,
        OnboardingOutcome.FAILED_HINDCAST,
        OnboardingOutcome.FAILED_SKILL,
        OnboardingOutcome.FAILED_ASSIGNMENT,
    }
)

ONBOARDING_SKIPPED_OUTCOMES: frozenset[OnboardingOutcome] = frozenset(
    {
        OnboardingOutcome.SKIPPED_COMPAT,
        OnboardingOutcome.SKIPPED_NO_DATA,
        OnboardingOutcome.SKIPPED_INSUFFICIENT_EVAL,
    }
)


@dataclass(frozen=True, kw_only=True, slots=True)
class OnboardingUnitResult:
    unit: TrainingUnit
    outcome: OnboardingOutcome
    compatibility: CompatibilityReport
    artifact_id: ArtifactId | None
    hindcast_steps: tuple[HindcastStepResult, ...]
    skill_gate: SkillGateResult | None
    error: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelOnboardingResult:
    model_id: ModelId
    units: tuple[OnboardingUnitResult, ...]

    def __len__(self) -> int:
        return len(self.units)

    def promoted_count(self) -> int:
        return sum(1 for u in self.units if u.outcome == OnboardingOutcome.PROMOTED)

    def failed_count(self) -> int:
        return sum(1 for u in self.units if u.outcome in ONBOARDING_FAILED_OUTCOMES)

    def skipped_count(self) -> int:
        return sum(1 for u in self.units if u.outcome in ONBOARDING_SKIPPED_OUTCOMES)

    def gate_rejected_count(self) -> int:
        return sum(
            1 for u in self.units if u.outcome == OnboardingOutcome.GATE_REJECTED
        )
