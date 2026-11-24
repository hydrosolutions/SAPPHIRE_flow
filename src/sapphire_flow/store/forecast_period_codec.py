from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sapphire_flow.types.enums import AggregationMethod
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid


class ForecastPeriodPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    step_seconds: int = Field(gt=0)
    origin_seconds: int = Field(ge=0)
    label: Literal["start", "end"]
    aggregation: Literal["sum", "mean", "max"]


def parse_forecast_period(value: object) -> ForecastPeriodDefinition | None:
    if value is None:
        return None
    payload = ForecastPeriodPayload.model_validate(value)
    return ForecastPeriodDefinition(
        grid=TimeGrid(
            step=timedelta(seconds=payload.step_seconds),
            phase=timedelta(seconds=payload.origin_seconds),
        ),
        label=PeriodLabel(payload.label),
        aggregation=AggregationMethod(payload.aggregation),
    )
