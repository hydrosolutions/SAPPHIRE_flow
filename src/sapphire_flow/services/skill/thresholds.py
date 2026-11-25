from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from sapphire_flow.services.skill.quantiles import QuantileSample
from sapphire_flow.services.threshold_probability import threshold_probability
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.threshold_skill import ThresholdSkillContext

if TYPE_CHECKING:
    from sapphire_flow.types.domain import StationThreshold
    from sapphire_flow.types.probability import ProbabilityMethod


def paired_threshold_probabilities(
    samples: list[np.ndarray | QuantileSample],
    observations: list[float],
    threshold: StationThreshold,
    units: str,
) -> tuple[np.ndarray, np.ndarray, ThresholdSkillContext]:
    probabilities: list[float] = []
    events: list[bool] = []
    methods: set[ProbabilityMethod] = set()
    for sample, observed in zip(samples, observations, strict=True):
        quantiles = isinstance(sample, QuantileSample)
        values = (
            list(zip(sample.probabilities, sample.values, strict=True))
            if quantiles
            else [(float(i), float(value)) for i, value in enumerate(sample)]
        )
        result = threshold_probability(
            EnsembleRepresentation.QUANTILES
            if quantiles
            else EnsembleRepresentation.MEMBERS,
            values,
            threshold.value,
        )
        methods.add(result.method)
        if result.probability is not None:
            probabilities.append(result.probability)
            events.append(observed > threshold.value)
    count = len(probabilities)
    context = ThresholdSkillContext(
        danger_level=threshold.danger_level,
        threshold_value=threshold.value,
        threshold_source=threshold.source,
        threshold_updated_at=threshold.updated_at,
        units=units,
        probability_methods=tuple(sorted(methods, key=lambda m: m.value)),
        paired_count=len(observations),
        scored_count=count,
        excluded_count=len(observations) - count,
        paired_event_count=sum(o > threshold.value for o in observations),
        scored_event_count=sum(events),
        scored_non_event_count=count - sum(events),
        coverage=(
            "no_resolved_probabilities"
            if count == 0
            else "conditional_resolved_subset"
            if count < len(observations)
            else "all_paired"
        ),
    )
    return (
        np.asarray(probabilities, dtype=float),
        np.asarray(events, dtype=bool),
        context,
    )
