from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.weather import PointForecast, WeatherForecastResult

if TYPE_CHECKING:
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationWeatherSource


class _WeatherRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_time: datetime
    parameter: Literal["precipitation"]
    member_id: Literal["0"]
    value: float = Field(ge=0, allow_inf_nan=False)


class DeploymentSmokeWeatherAdapter:
    def __init__(self, weather_csv: bytes, *, cycle_time: UtcDatetime) -> None:
        self._cycle_time = ensure_utc(cycle_time)
        try:
            rows = [
                _WeatherRow.model_validate(row)
                for row in csv.DictReader(io.StringIO(weather_csv.decode("utf-8")))
            ]
        except (UnicodeDecodeError, ValidationError, csv.Error) as exc:
            raise ConfigurationError("invalid smoke weather CSV") from exc
        expected = [self._cycle_time + timedelta(hours=n) for n in (1, 2, 3)]
        if any(row.valid_time.utcoffset() != timedelta(0) for row in rows):
            raise ConfigurationError("smoke weather timestamps must have a UTC offset")
        if [row.valid_time for row in rows] != expected:
            raise ConfigurationError(
                "smoke weather must cover exactly the three future hours"
            )
        self._values = pl.DataFrame(
            {
                "valid_time": expected,
                "parameter": [row.parameter for row in rows],
                "member_id": [0] * 3,
                "value": [row.value for row in rows],
            },
            schema={
                "valid_time": pl.Datetime("us", "UTC"),
                "parameter": pl.String,
                "member_id": pl.Int32,
                "value": pl.Float64,
            },
        )

    def fetch_forecasts(
        self, station_configs: list[StationWeatherSource], cycle_time: UtcDatetime
    ) -> dict[StationId, WeatherForecastResult]:
        if cycle_time != self._cycle_time:
            raise AdapterError("smoke weather is available only for the fixture cycle")
        if len(station_configs) != 1:
            raise AdapterError("smoke weather requires exactly one station binding")
        binding = station_configs[0]
        if (
            binding.nwp_source != "icon_ch2_eps"
            or binding.extraction_type is not SpatialRepresentation.POINT
            or binding.role is not WeatherSourceRole.FORECAST
            or binding.status is not WeatherSourceStatus.ACTIVE
        ):
            raise AdapterError(
                "smoke weather requires the active fixture point binding"
            )
        return {
            binding.station_id: PointForecast(
                nwp_source=binding.nwp_source,
                cycle_time=self._cycle_time,
                values=self._values.clone(),
            )
        }
