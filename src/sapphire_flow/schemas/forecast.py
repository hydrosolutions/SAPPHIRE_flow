from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ForecastAdjustmentItem(BaseModel):
    valid_time: str
    lead_time_hours: int
    adjustment_type: Literal["shift", "scale", "cap", "floor"]
    value: float
