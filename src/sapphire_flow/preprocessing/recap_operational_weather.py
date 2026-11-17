from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.operational_weather import (
    UTC_DAILY_WEATHER_GRID,
    OperationalWeatherCoverageError,
    OperationalWeatherDay,
    OperationalWeatherPeriod,
    OperationalWeatherWindow,
    WeatherCoverage,
    daily_weather_value,
    require_daily_grid_boundary,
    require_midnight,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.operational_weather import OperationalWeatherSample
    from sapphire_flow.types.time_grid import TimeGrid


def reduce_weather_periods(
    *,
    start: UtcDatetime,
    end: UtcDatetime,
    samples: tuple[OperationalWeatherSample, ...],
    grid: TimeGrid,
    coverage: WeatherCoverage = WeatherCoverage.COMPLETE,
) -> tuple[OperationalWeatherPeriod, ...]:
    for bound in (start, end):
        require_daily_grid_boundary(bound, grid)
    if start >= end:
        raise ValueError("operational weather support must be nonempty")
    by_day: dict[UtcDatetime, list[OperationalWeatherSample]] = {}
    for sample in samples:
        if not start <= sample.valid_time < end:
            continue
        day = grid.floor(sample.valid_time)
        by_day.setdefault(day, []).append(sample)
    periods: list[OperationalWeatherPeriod] = []
    for offset in range((end - start).days):
        day = ensure_utc(start + timedelta(days=offset))
        rows = tuple(by_day.get(day, ()))
        temperature = daily_weather_value(day, rows, "temperature", grid=grid)
        precipitation = daily_weather_value(day, rows, "precipitation", grid=grid)
        if coverage is WeatherCoverage.COMPLETE and (
            temperature is None or precipitation is None
        ):
            raise OperationalWeatherCoverageError(
                "operational weather has a gap in required daily support"
            )
        periods.append(
            OperationalWeatherPeriod(
                grid=grid,
                start=day,
                precipitation_mm=precipitation,
                temperature_c=temperature,
                samples=rows,
            )
        )
    return tuple(periods)


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
    periods = reduce_weather_periods(
        start=start,
        end=end,
        samples=samples,
        grid=UTC_DAILY_WEATHER_GRID,
        coverage=coverage,
    )
    return OperationalWeatherWindow(
        station_id=station_id,
        binding_fingerprint=binding_fingerprint,
        issue_time=issue_time,
        start=start,
        end=end,
        days=tuple(
            OperationalWeatherDay(
                day=period.start,
                precipitation_mm=period.precipitation_mm,
                temperature_c=period.temperature_c,
                samples=period.samples,
            )
            for period in periods
        ),
    )
