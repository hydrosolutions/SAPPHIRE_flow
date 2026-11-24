from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, cast

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

_DAY = timedelta(days=1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class PeriodLabel(Enum):
    START = "start"
    END = "end"


@dataclass(frozen=True, kw_only=True, slots=True)
class TimeGrid:
    """Declared fixed grid; phase is measured from UTC midnight, not a label."""

    step: timedelta
    phase: timedelta

    def __post_init__(self) -> None:
        if self.step <= timedelta(0) or _DAY % self.step:
            raise ValueError(
                "TimeGrid step must be positive and divide 24 hours exactly"
            )
        if not timedelta(0) <= self.phase < self.step:
            raise ValueError("TimeGrid phase must satisfy 0 <= phase < step")
        if self.phase % timedelta(minutes=1):
            raise ValueError("TimeGrid phase must be a whole number of minutes")

    def alignable(self, other: TimeGrid) -> bool:
        return self == other

    def nests_into(self, coarse: TimeGrid) -> bool:
        return coarse.step % self.step == timedelta(0) and (
            coarse.phase - self.phase
        ) % self.step == timedelta(0)

    def floor(self, instant: UtcDatetime) -> UtcDatetime:
        elapsed = cast("datetime", ensure_utc(instant)) - _EPOCH - self.phase
        return ensure_utc(_EPOCH + self.phase + (elapsed // self.step) * self.step)

    def lookback_bounds(
        self, anchor: UtcDatetime, steps: int
    ) -> tuple[UtcDatetime, UtcDatetime]:
        """Physical [start, end) covering complete buckets before the anchor."""
        _require_count(steps)
        end = self.floor(anchor)
        return ensure_utc(end - steps * self.step), end

    def future_bounds(
        self, anchor: UtcDatetime, steps: int
    ) -> tuple[UtcDatetime, UtcDatetime]:
        """Physical [start, end) covering complete buckets at/after the anchor."""
        _require_count(steps)
        start = self.floor(anchor)
        if ensure_utc(anchor) != start:
            start = ensure_utc(start + self.step)
        return start, ensure_utc(start + steps * self.step)


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelTimeGrid:
    """Physical period boundaries and the model's timestamp convention."""

    grid: TimeGrid
    label: PeriodLabel


def _require_count(steps: object) -> None:
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 0:
        raise ValueError("TimeGrid steps must be a nonnegative integer")


def require_common_time_grid(grids: Sequence[TimeGrid]) -> TimeGrid:
    if not grids:
        raise ValueError("A group requires at least one declared time grid")
    first = grids[0]
    if any(not first.alignable(grid) for grid in grids[1:]):
        raise ValueError("Group members must share the same time-grid step and phase")
    return first
