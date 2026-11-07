from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SELECTED_WEATHER_EVIDENCE_BYTES = 32 * 1024 * 1024


class OperationalPastWeatherRoute(StrEnum):
    STORED_REANALYSIS = "stored_reanalysis"
    RECAP_COMBINED = "recap_combined"


class OperationalWeatherConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: OperationalPastWeatherRoute = OperationalPastWeatherRoute.STORED_REANALYSIS
    source: Literal["recap_ecmwf_operational"] = "recap_ecmwf_operational"
    provider_min_history_days: int = Field(default=14, ge=1, le=370, strict=True)
    provider_max_history_days: int = Field(default=370, ge=1, le=370, strict=True)
    rollover_buffer_days: int = Field(default=1, ge=0, le=1, strict=True)
    max_response_bytes: int = Field(default=64 * 1024 * 1024, gt=0, strict=True)
    max_cache_bytes: int = Field(default=2 * 1024 * 1024, gt=0, strict=True)
    max_evidence_bytes: int = Field(
        default=MAX_SELECTED_WEATHER_EVIDENCE_BYTES,
        gt=0,
        le=MAX_SELECTED_WEATHER_EVIDENCE_BYTES,
        strict=True,
    )
    max_future_receipts: int = Field(default=32, ge=1, le=32, strict=True)

    @model_validator(mode="after")
    def validate_history_bounds(self) -> Self:
        if self.provider_min_history_days > self.provider_max_history_days:
            raise ValueError("provider minimum history exceeds maximum history")
        return self
