from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.operational_weather import (
    OperationalWeatherCoverageError,
    OperationalWeatherDay,
    OperationalWeatherWindow,
    WeatherCoverage,
    daily_weather_value,
    require_midnight,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.operational_weather import OperationalWeatherSample


def reduce_daily_window(
    *,
    station_id: StationId,
    binding_fingerprint: str,
    issue_time: UtcDatetime,
    start: UtcDatetime,
    end: UtcDatetime,
    samples: tuple[OperationalWeatherSample, ...],
    coverage: WeatherCoverage = WeatherCoverage.COMPLETE,
) -> OperationalWeatherWindow:
    for bound in (issue_time, start, end):
        require_midnight(bound)
    if start >= end:
        raise ValueError("operational weather support must be nonempty")
    by_day: dict[UtcDatetime, list[OperationalWeatherSample]] = {}
    for sample in samples:
        if not start <= sample.valid_time < end:
            continue
        day = ensure_utc(sample.valid_time.replace(hour=0))
        by_day.setdefault(day, []).append(sample)
    days: list[OperationalWeatherDay] = []
    for offset in range((end - start).days):
        day = ensure_utc(start + timedelta(days=offset))
        rows = tuple(by_day.get(day, ()))
        temperature = daily_weather_value(day, rows, "temperature")
        precipitation = daily_weather_value(day, rows, "precipitation")
        if coverage is WeatherCoverage.COMPLETE and (
            temperature is None or precipitation is None
        ):
            raise OperationalWeatherCoverageError(
                "operational weather has a gap in required daily support"
            )
        days.append(
            OperationalWeatherDay(
                day=day,
                precipitation_mm=precipitation,
                temperature_c=temperature,
                samples=rows,
            )
        )
    return OperationalWeatherWindow(
        station_id=station_id,
        binding_fingerprint=binding_fingerprint,
        issue_time=issue_time,
        start=start,
        end=end,
        days=tuple(days),
    )
