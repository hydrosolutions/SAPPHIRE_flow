from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import FormulaId, StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class ComponentWeight:
    """One (component station, weight) row of a calculated station's formula.

    A calculated station's discharge is `Q = Σ(wᵢ · Qᵢ)` over its component rows for a
    given parameter and validity window (Plan 015). Weights are signed physical scaling
    factors (negative allowed for difference formulas) and need not sum to 1.
    """

    id: FormulaId
    calculated_station_id: StationId
    component_station_id: StationId
    parameter: str
    weight: float
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None  # None = current; non-None = superseded
    created_at: UtcDatetime

    def __post_init__(self) -> None:
        if not (
            self.weight != 0 and math.isfinite(self.weight) and abs(self.weight) < 1e6
        ):
            raise ValueError(
                f"weight must be nonzero and finite (|w| < 1e6), got {self.weight!r}"
            )
        if self.calculated_station_id == self.component_station_id:
            raise ValueError(
                "calculated_station_id and component_station_id must differ "
                f"(both {self.calculated_station_id})"
            )
