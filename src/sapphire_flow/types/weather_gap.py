from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Literal

from sapphire_flow.types.datetime import UtcDatetime  # noqa: TC001 - evidence schema
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.weather import (
    WeatherForecastRecord,  # noqa: TC001 - evidence schema
)

GapWeatherParameter = Literal["precipitation", "temperature", "global_radiation"]
GAP_WEATHER_PARAMETERS = frozenset({"precipitation", "temperature", "global_radiation"})


class WeatherGapMode(StrEnum):
    STRICT_MODELS = "strict_models"
    ALWAYS = "always"
    DISABLED = "disabled"


class WeatherGapMethod(StrEnum):
    LINEAR = "linear"
    STORED_FORECAST = "stored_forecast"


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherGapPolicy:
    mode: WeatherGapMode
    max_gap_days: int


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherGapEndpoint:
    day: UtcDatetime
    value: float

    def __post_init__(self) -> None:
        _require_day(self.day)
        if not math.isfinite(self.value):
            raise ValueError("weather gap endpoint must be finite")


def _require_day(day: UtcDatetime) -> None:
    if day.utcoffset() != timedelta(0) or any(
        (day.hour, day.minute, day.second, day.microsecond)
    ):
        raise ValueError("weather gap requires a UTC daily bucket")


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherGapFill:
    parameter: GapWeatherParameter
    day: UtcDatetime
    value: float
    method: WeatherGapMethod
    endpoints: tuple[WeatherGapEndpoint, ...] = ()
    donor_records: tuple[WeatherForecastRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_day(self.day)
        if self.parameter not in GAP_WEATHER_PARAMETERS or not math.isfinite(
            self.value
        ):
            raise ValueError("weather gap fill requires a supported finite value")
        if self.parameter != "temperature" and self.value < 0:
            raise ValueError("weather gap fill must be nonnegative")
        if self.method is WeatherGapMethod.LINEAR:
            if len(self.endpoints) != 2 or self.donor_records:
                raise ValueError("linear weather fill requires two original endpoints")
            left, right = self.endpoints
            if not left.day < self.day < right.day:
                raise ValueError("weather fill cannot extrapolate")
            if self.parameter == "precipitation" and right.day - left.day != timedelta(
                days=2
            ):
                raise ValueError("rainfall interpolation requires a single missing day")
            expected = left.value + (right.value - left.value) * (
                (self.day - left.day) / (right.day - left.day)
            )
        elif self.method is WeatherGapMethod.STORED_FORECAST:
            if self.endpoints or not self.donor_records:
                raise ValueError(
                    "stored weather fill requires original forecast records"
                )
            first = self.donor_records[0]
            cycle = first.cycle_time
            _require_day(cycle)
            if self.parameter == "global_radiation" or cycle > self.day:
                raise ValueError("unsupported stored weather donor")
            end = self.day + timedelta(days=1)
            if end > cycle + timedelta(hours=360):
                raise ValueError("stored weather donor exceeds its horizon")
            expected_times: list[UtcDatetime] = []
            stamp = self.day
            while stamp < end:
                expected_times.append(stamp)
                stamp += timedelta(
                    hours=3 if stamp - cycle < timedelta(hours=144) else 6
                )
            rows = sorted(self.donor_records, key=lambda row: row.valid_time)
            if [row.valid_time for row in rows] != expected_times or any(
                row.station_id != first.station_id
                or row.nwp_source != "ifs_ecmwf"
                or row.cycle_time != cycle
                or row.parameter != self.parameter
                or row.member_id != 0
                or row.spatial_type is not SpatialRepresentation.BASIN_AVERAGE
                or row.band_id is not None
                or row.is_gap
                or row.gap_status is not None
                or isinstance(row.value, bool)
                or not math.isfinite(row.value)
                or (self.parameter == "precipitation" and row.value < 0)
                for row in rows
            ):
                raise ValueError(
                    "stored weather donor lacks complete single-cycle support"
                )
            expected = math.fsum(row.value for row in rows)
            if self.parameter == "temperature":
                expected /= len(rows)
        else:
            raise ValueError("unsupported weather gap method")
        if self.value != expected:
            raise ValueError("weather fill differs from its recorded support")
