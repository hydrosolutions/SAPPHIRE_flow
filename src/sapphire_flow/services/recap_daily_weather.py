from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from math import isfinite
from typing import TYPE_CHECKING

from sapphire_flow.services.operational_weather_proof import WeatherProofError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleMode, SpatialRepresentation
from sapphire_flow.types.operational_weather import (
    UTC_DAILY_WEATHER_GRID,
    qualified_ifs_daily_support,
    require_daily_grid_boundary,
    require_utc,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.time_grid import TimeGrid
    from sapphire_flow.types.weather import WeatherForecastRecord


def recap_daily_support(
    cycle_time: UtcDatetime,
    day: UtcDatetime,
    *,
    grid: TimeGrid = UTC_DAILY_WEATHER_GRID,
) -> tuple[UtcDatetime, ...]:
    require_utc(cycle_time)
    require_daily_grid_boundary(day, grid)
    try:
        return qualified_ifs_daily_support(cycle_time, day, grid=grid)
    except ValueError as exc:
        raise WeatherProofError(str(exc)) from exc


def recap_complete_daily_records(
    records: list[WeatherForecastRecord],
    *,
    issue_time: UtcDatetime,
    feature_horizons: dict[str, int],
    ensemble_mode: EnsembleMode,
    grid: TimeGrid = UTC_DAILY_WEATHER_GRID,
) -> list[WeatherForecastRecord]:
    return recap_complete_period_records(
        records,
        first_period_start=issue_time,
        cycle_ceiling=issue_time,
        feature_horizons=feature_horizons,
        ensemble_mode=ensemble_mode,
        grid=grid,
    )


def recap_complete_period_records(
    records: list[WeatherForecastRecord],
    *,
    first_period_start: UtcDatetime,
    cycle_ceiling: UtcDatetime,
    feature_horizons: dict[str, int],
    ensemble_mode: EnsembleMode,
    grid: TimeGrid = UTC_DAILY_WEATHER_GRID,
) -> list[WeatherForecastRecord]:
    require_utc(cycle_ceiling)
    require_daily_grid_boundary(first_period_start, grid)
    if not feature_horizons or any(
        feature not in ("precipitation", "temperature") for feature in feature_horizons
    ):
        return []
    selected = [
        record
        for record in records
        if record.parameter in feature_horizons
        and (ensemble_mode is EnsembleMode.ENSEMBLE or record.member_id == 0)
    ]
    if not selected:
        return []
    cycles = {record.cycle_time for record in selected}
    if len(cycles) != 1 or any(record.nwp_source != "ifs_ecmwf" for record in selected):
        raise WeatherProofError(
            "selected Recap weather must use one qualified source/cycle"
        )
    cycle = next(iter(cycles))
    if cycle > cycle_ceiling:
        raise WeatherProofError("selected Recap cycle follows issue time")
    if any(
        type(record.member_id) is not int or record.member_id < 0 for record in selected
    ):
        raise WeatherProofError("selected Recap weather lacks explicit member identity")
    members = {record.member_id for record in selected}
    points: dict[tuple[str, int | None, UtcDatetime], list[WeatherForecastRecord]] = (
        defaultdict(list)
    )
    for record in selected:
        points[(record.parameter, record.member_id, record.valid_time)].append(record)
    complete: list[WeatherForecastRecord] = []
    for feature, horizon in feature_horizons.items():
        for offset in range(horizon):
            support = recap_daily_support(
                cycle,
                ensure_utc(first_period_start + timedelta(days=offset)),
                grid=grid,
            )
            if not support:
                break
            keys = [(feature, member, stamp) for member in members for stamp in support]
            if any(len(points[key]) > 1 for key in keys):
                raise WeatherProofError(
                    "selected Recap weather contains duplicate support"
                )
            if any(len(points[key]) != 1 for key in keys):
                break
            day_rows = [points[key][0] for key in keys]
            if any(
                not isfinite(record.value)
                or isinstance(record.value, bool)
                or (record.parameter == "precipitation" and record.value < 0)
                or record.is_gap
                or record.gap_status is not None
                or record.spatial_type is not SpatialRepresentation.BASIN_AVERAGE
                or record.band_id is not None
                for record in day_rows
            ):
                raise WeatherProofError(
                    "selected Recap weather has invalid required samples"
                )
            day_start = first_period_start + timedelta(days=offset)
            if any(
                record.parameter == feature
                and day_start <= record.valid_time < day_start + timedelta(days=1)
                and record.valid_time not in support
                for record in selected
            ):
                raise WeatherProofError(
                    "selected Recap weather has unsupported cadence"
                )
            complete.extend(day_rows)
    return complete
