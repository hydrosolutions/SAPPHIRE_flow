from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastOutputKey:
    station_id: StationId
    model_id: ModelId
    issued_at: UtcDatetime
    parameter: str

    def __post_init__(self) -> None:
        if self.issued_at.utcoffset() != timedelta(0):
            raise ValueError("issued_at must be an aware UTC datetime")
        if not self.parameter.strip():
            raise ValueError("forecast parameter must not be empty")
