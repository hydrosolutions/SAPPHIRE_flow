from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class DemoScenario:
    issued_at: UtcDatetime
    history: tuple[float | None, ...]
    lower: tuple[float, ...]
    median: tuple[float, ...]
    upper: tuple[float, ...]
    outturn: tuple[float | None, ...]

    def __post_init__(self) -> None:
        if self.issued_at.utcoffset() != timedelta(0) or self.issued_at.microsecond:
            raise ValueError("demo issue time must be UTC at whole-second precision")
        arrays = (self.history, self.lower, self.median, self.upper, self.outturn)
        if tuple(map(len, arrays)) != (168, 72, 72, 72, 72):
            raise ValueError("demo series lengths must be 168 history and 72 horizon")
        if any(
            v is not None and (not math.isfinite(v) or v < 0)
            for array in arrays
            for v in array
        ):
            raise ValueError("demo discharge must be finite and nonnegative")
        if any(
            not lo <= mid <= hi
            for lo, mid, hi in zip(self.lower, self.median, self.upper, strict=True)
        ):
            raise ValueError("demo quantiles must be ordered")
        if tuple(i for i, v in enumerate(self.history) if v is None) != tuple(
            range(120, 126)
        ) or tuple(i for i, v in enumerate(self.outturn) if v is None) != (40, 41):
            raise ValueError("demo nulls must match the fixed history/outturn gaps")
