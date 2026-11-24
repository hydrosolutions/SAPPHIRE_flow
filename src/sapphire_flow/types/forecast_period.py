from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.enums import AggregationMethod
    from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastPeriodDefinition:
    grid: TimeGrid
    label: PeriodLabel
    aggregation: AggregationMethod

    @property
    def content(self) -> dict[str, int | str]:
        return {
            "step_seconds": int(self.grid.step.total_seconds()),
            "origin_seconds": int(self.grid.phase.total_seconds()),
            "label": self.label.value,
            "aggregation": self.aggregation.value,
        }
