from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import ThresholdSource
    from sapphire_flow.types.probability import ProbabilityMethod


@dataclass(frozen=True, kw_only=True, slots=True)
class ThresholdSkillContext:
    danger_level: str
    threshold_value: float
    threshold_source: ThresholdSource
    threshold_updated_at: UtcDatetime
    units: str
    probability_methods: tuple[ProbabilityMethod, ...]
    paired_count: int
    scored_count: int
    excluded_count: int
    paired_event_count: int
    scored_event_count: int
    scored_non_event_count: int
    coverage: Literal[
        "all_paired", "conditional_resolved_subset", "no_resolved_probabilities"
    ]
    schema_version: Literal[1] = 1
    decision_probability: float = 0.5
    comparison: Literal["strict_gt"] = "strict_gt"
    reference: Literal["same_scored_subset_event_rate"] = (
        "same_scored_subset_event_rate"
    )
    temporal_basis: Literal["legacy_step_aggregate"] = "legacy_step_aggregate"
    threshold_basis: Literal["current_snapshot_historical_effective_dates_unknown"] = (
        "current_snapshot_historical_effective_dates_unknown"
    )

    def __post_init__(self) -> None:
        if (
            not self.danger_level
            or not self.units
            or not math.isfinite(self.threshold_value)
        ):
            raise ValueError("threshold context requires finite value, level and units")
        counts = (
            self.paired_count,
            self.scored_count,
            self.excluded_count,
            self.paired_event_count,
            self.scored_event_count,
            self.scored_non_event_count,
        )
        if any(type(n) is not int or n < 0 for n in counts):
            raise ValueError("threshold context counts must be nonnegative integers")
        if (
            self.scored_count + self.excluded_count != self.paired_count
            or self.scored_event_count + self.scored_non_event_count
            != self.scored_count
            or not self.scored_event_count
            <= self.paired_event_count
            <= self.paired_count
            or self.paired_event_count - self.scored_event_count > self.excluded_count
        ):
            raise ValueError("threshold context counts are inconsistent")
        expected = (
            "no_resolved_probabilities"
            if self.scored_count == 0
            else "conditional_resolved_subset"
            if self.excluded_count
            else "all_paired"
        )
        if self.coverage != expected:
            raise ValueError("threshold context coverage is inconsistent")
        if (
            not math.isfinite(self.decision_probability)
            or not 0 <= self.decision_probability <= 1
        ):
            raise ValueError("decision probability must be within [0, 1]")
