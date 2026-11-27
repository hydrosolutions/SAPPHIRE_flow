from datetime import timedelta
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sapphire_flow.store.forecast_period_codec import (
    ForecastPeriodPayload,
    parse_forecast_period,
)
from sapphire_flow.types.hindcast_inputs import (
    HistoricalSampling,
    HistoricalSourcePeriod,
)
from sapphire_flow.types.ids import StationId


class HistoricalSourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station_id: UUID
    source: str = Field(min_length=1)
    parameter: str = Field(min_length=1)
    period: ForecastPeriodPayload
    evidence: str = Field(min_length=1)
    stored_label_shift_seconds: int = Field(default=0, strict=True)
    input_support_displacement_seconds: int = Field(default=0, strict=True)
    sampling: Literal["interval", "snapshot"] = "interval"


def parse_source_periods(value: object) -> tuple[HistoricalSourcePeriod, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("source_periods must be a list of source declarations")
    result: list[HistoricalSourcePeriod] = []
    for item in cast("list[object] | tuple[object, ...]", value):
        payload = HistoricalSourcePayload.model_validate(item)
        period = parse_forecast_period(payload.period.model_dump())
        assert period is not None
        result.append(
            HistoricalSourcePeriod(
                station_id=StationId(payload.station_id),
                source=payload.source,
                parameter=payload.parameter,
                period=period,
                evidence=payload.evidence,
                stored_label_shift=timedelta(
                    seconds=payload.stored_label_shift_seconds
                ),
                input_support_displacement=timedelta(
                    seconds=payload.input_support_displacement_seconds
                ),
                sampling=HistoricalSampling(payload.sampling),
            )
        )
    if len({item.key for item in result}) != len(result):
        raise ValueError("duplicate historical source declaration")
    return tuple(
        sorted(
            result, key=lambda item: (str(item.station_id), item.source, item.parameter)
        )
    )


def source_periods_content(
    value: tuple[HistoricalSourcePeriod, ...],
) -> list[dict[str, object]]:
    return [
        {
            "station_id": str(item.station_id),
            "source": item.source,
            "parameter": item.parameter,
            "period": item.period.content,
            "evidence": item.evidence,
            "stored_label_shift_seconds": int(item.stored_label_shift.total_seconds()),
            "input_support_displacement_seconds": int(
                item.input_support_displacement.total_seconds()
            ),
            "sampling": item.sampling.value,
        }
        for item in sorted(
            value, key=lambda item: (str(item.station_id), item.source, item.parameter)
        )
    ]
