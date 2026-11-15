from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sapphire_flow.types.weather_gap import WeatherGapMode, WeatherGapPolicy


class ModelWeatherGapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: WeatherGapMode | None = None
    max_gap_days: int | None = Field(default=None, ge=1, le=7, strict=True)


class WeatherGapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: WeatherGapMode = WeatherGapMode.STRICT_MODELS
    max_gap_days: int = Field(default=1, ge=1, le=7, strict=True)
    models: dict[str, ModelWeatherGapConfig] = Field(default_factory=dict)

    def for_model(self, model_id: str) -> WeatherGapPolicy:
        override = self.models.get(model_id)
        return WeatherGapPolicy(
            mode=self.mode
            if override is None or override.mode is None
            else override.mode,
            max_gap_days=self.max_gap_days
            if override is None or override.max_gap_days is None
            else override.max_gap_days,
        )
