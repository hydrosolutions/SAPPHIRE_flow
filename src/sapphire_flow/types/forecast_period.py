from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AggregationMethod  # noqa: TC001
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid  # noqa: TC001

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastPeriodDefinition:
    grid: TimeGrid
    label: PeriodLabel
    aggregation: AggregationMethod

    def bounds(self, timestamp: UtcDatetime) -> tuple[UtcDatetime, UtcDatetime]:
        label = ensure_utc(timestamp)
        if self.grid.floor(label) != label:
            raise ValueError("forecast timestamp is not on the declared grid")
        start = (
            label
            if self.label is PeriodLabel.START
            else ensure_utc(label - self.grid.step)
        )
        return start, ensure_utc(start + self.grid.step)

    @property
    def content(self) -> dict[str, int | str]:
        return {
            "step_seconds": int(self.grid.step.total_seconds()),
            "origin_seconds": int(self.grid.phase.total_seconds()),
            "label": self.label.value,
            "aggregation": self.aggregation.value,
        }
