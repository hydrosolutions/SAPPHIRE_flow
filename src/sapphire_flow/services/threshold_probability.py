from __future__ import annotations

import math
from bisect import bisect_right
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.probability import ExceedanceProbability, ProbabilityMethod


def unavailable_probability(reason: str) -> ExceedanceProbability:
    return ExceedanceProbability(
        probability=None,
        lower=None,
        upper=None,
        method=ProbabilityMethod.UNAVAILABLE,
        reason=reason,
    )


def threshold_probability(
    representation: EnsembleRepresentation,
    values: Sequence[tuple[float, float]],
    threshold: float,
) -> ExceedanceProbability:
    """P(value > threshold); interpolate only inside supplied quantile support."""
    if not math.isfinite(threshold):
        raise ValueError("threshold must be finite")
    if not values or any(not math.isfinite(x) for pair in values for x in pair):
        return unavailable_probability("empty_or_nonfinite_values")
    labels, amounts = zip(*sorted(values), strict=True)
    if len(set(labels)) != len(labels):
        return unavailable_probability("duplicate_labels")
    if representation is EnsembleRepresentation.MEMBERS:
        probability = sum(value > threshold for value in amounts) / len(amounts)
        return ExceedanceProbability(
            probability=probability,
            lower=probability,
            upper=probability,
            method=ProbabilityMethod.EMPIRICAL_MEMBERS,
        )
    if representation is not EnsembleRepresentation.QUANTILES:
        return unavailable_probability("unsupported_representation")
    if len(labels) < 2 or not 0 <= labels[0] < labels[-1] <= 1:
        return unavailable_probability("invalid_quantile_support")
    if any(a > b for a, b in pairwise(amounts)):
        return unavailable_probability("crossing_quantiles")
    if threshold < amounts[0]:
        lower, upper = 1 - labels[0], 1.0
    elif threshold >= amounts[-1]:
        lower, upper = 0.0, 1 - labels[-1]
    else:
        right = bisect_right(amounts, threshold)
        left = right - 1
        # Weighted form avoids overflow when finite endpoints have opposite signs.
        scale = max(abs(amounts[left]), abs(amounts[right]), 1.0)
        fraction = (threshold / scale - amounts[left] / scale) / (
            amounts[right] / scale - amounts[left] / scale
        )
        lower, upper = 1 - labels[right], 1 - labels[left]
        probability = upper * (1 - fraction) + lower * fraction
        return ExceedanceProbability(
            probability=min(upper, max(lower, probability)),
            lower=lower,
            upper=upper,
            method=ProbabilityMethod.INTERPOLATED_QUANTILES,
        )
    return ExceedanceProbability(
        probability=lower if lower == upper else None,
        lower=lower,
        upper=upper,
        method=ProbabilityMethod.QUANTILE_BOUNDS,
        reason=None if lower == upper else "outside_resolved_quantile_range",
    )
