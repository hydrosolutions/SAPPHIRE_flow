from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

import numpy as np

QUANTILE_SCORE = "quantile_score_trapz_p05_p95"


@dataclass(frozen=True, kw_only=True, slots=True)
class QuantileSample:
    probabilities: tuple[float, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.probabilities) != len(self.values) or len(self.values) < 2:
            raise ValueError("quantile sample needs matching probability/value pairs")
        if not all(math.isfinite(x) for x in (*self.probabilities, *self.values)):
            raise ValueError("quantile sample must be finite")
        if not 0 <= self.probabilities[0] < self.probabilities[-1] <= 1:
            raise ValueError("quantile probabilities must be within [0, 1]")
        if any(a >= b for a, b in pairwise(self.probabilities)):
            raise ValueError("quantile probabilities must be strictly increasing")
        if any(a > b for a, b in pairwise(self.values)):
            raise ValueError("quantile values must not cross")
        if self.probabilities[0] > 0.05 or self.probabilities[-1] < 0.95:
            raise ValueError("quantile sample must cover probabilities 0.05 to 0.95")

    def at(self, probability: float) -> float:
        if not self.probabilities[0] <= probability <= self.probabilities[-1]:
            raise ValueError("requested quantile is outside supplied support")
        return float(np.interp(probability, self.probabilities, self.values))

    def score(self, observed: float) -> float:
        if not math.isfinite(observed):
            raise ValueError("quantile score observation must be finite")
        probabilities = np.array(
            [0.05, *(p for p in self.probabilities if 0.05 < p < 0.95), 0.95]
        )
        values = np.interp(probabilities, self.probabilities, self.values)
        error = observed - values
        loss = error * (probabilities - (error < 0))
        return float(
            2 * np.sum(np.diff(probabilities) * (loss[:-1] + loss[1:]) / 2) / 0.9
        )
