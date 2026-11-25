from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class ProbabilityMethod(StrEnum):
    EMPIRICAL_MEMBERS = "empirical_members"
    INTERPOLATED_QUANTILES = "piecewise_linear_quantile_cdf"
    QUANTILE_BOUNDS = "quantile_bounds"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, kw_only=True, slots=True)
class ExceedanceProbability:
    probability: float | None
    lower: float | None
    upper: float | None
    method: ProbabilityMethod
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.method is ProbabilityMethod.UNAVAILABLE:
            if any(v is not None for v in (self.probability, self.lower, self.upper)):
                raise ValueError("unavailable probability cannot contain numbers")
            if not self.reason:
                raise ValueError("unavailable probability requires a reason")
            return
        if (
            self.lower is None
            or self.upper is None
            or not (
                math.isfinite(self.lower)
                and math.isfinite(self.upper)
                and 0 <= self.lower <= self.upper <= 1
            )
        ):
            raise ValueError("probability bounds must be finite and ordered in [0, 1]")
        if self.probability is not None and not (
            math.isfinite(self.probability)
            and self.lower <= self.probability <= self.upper
        ):
            raise ValueError("point probability must be within its bounds")
