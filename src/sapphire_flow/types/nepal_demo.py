from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

ISSUE_OFFSETS = range(0, 48, 6)
FORECAST_OFFSETS = range(3, 73, 3)
OBSERVATION_OFFSETS = range(-168, 43)
OBSERVATION_GAPS = ((-48, -42), (17, 19))


def _require_utc(value: UtcDatetime) -> None:
    if value.utcoffset() != timedelta(0) or value.microsecond:
        raise ValueError("demo issue time must be UTC at whole-second precision")


@dataclass(frozen=True, kw_only=True, slots=True)
class DemoIssue:
    issued_at: UtcDatetime
    lower: tuple[float, ...]
    median: tuple[float, ...]
    upper: tuple[float, ...]

    def __post_init__(self) -> None:
        _require_utc(self.issued_at)
        arrays = (self.lower, self.median, self.upper)
        if any(len(array) != 24 for array in arrays):
            raise ValueError("forecast series lengths must be 24")
        if any(not math.isfinite(v) or v < 0 for array in arrays for v in array):
            raise ValueError("demo discharge must be finite and nonnegative")
        if any(not lo <= mid <= hi for lo, mid, hi in zip(*arrays, strict=True)):
            raise ValueError("demo quantiles must be ordered")


@dataclass(frozen=True, kw_only=True, slots=True)
class DemoScenario:
    first_issued_at: UtcDatetime
    observations: tuple[float | None, ...]
    forecasts: tuple[DemoIssue, ...]

    def __post_init__(self) -> None:
        _require_utc(self.first_issued_at)
        if len(self.observations) != 211 or len(self.forecasts) != 8:
            raise ValueError("demo needs 211 observations and 8 issues")
        expected_issues = tuple(
            self.first_issued_at + timedelta(hours=h) for h in ISSUE_OFFSETS
        )
        if tuple(f.issued_at for f in self.forecasts) != expected_issues:
            raise ValueError("issues must be ordered at exactly 6-hour spacing")
        if any(
            v is not None and (not math.isfinite(v) or v < 0) for v in self.observations
        ):
            raise ValueError("demo discharge must be finite and nonnegative")
        missing = tuple(v is None for v in self.observations)
        expected_missing = tuple(
            any(start <= h < end for start, end in OBSERVATION_GAPS)
            for h in OBSERVATION_OFFSETS
        )
        if missing != expected_missing:
            raise ValueError("observation nulls must match the declared gaps")
