from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from math import isfinite
from typing import TYPE_CHECKING

from sapphire_flow.services.operational_weather_proof import WeatherProofError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleMode, SpatialRepresentation
from sapphire_flow.types.operational_weather import require_midnight, require_utc

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.weather import WeatherForecastRecord


def recap_daily_support(
    cycle_time: UtcDatetime,
    day: UtcDatetime,
) -> tuple[UtcDatetime, ...]:
    require_utc(cycle_time)
    require_midnight(day)
    if cycle_time.hour not in (0, 6, 12, 18) or any(
        (cycle_time.minute, cycle_time.second, cycle_time.microsecond)
    ):
        raise WeatherProofError("unsupported Recap forecast cycle")
    maximum = 360 if cycle_time.hour == 0 else 72
    if day < cycle_time or day + timedelta(days=1) > cycle_time + timedelta(
        hours=maximum
    ):
        return ()
    leads = (
        tuple(range(0, 145, 3)) + tuple(range(150, 361, 6))
        if maximum == 360
        else tuple(range(0, 73, 3))
    )
    return tuple(
        ensure_utc(cycle_time + timedelta(hours=lead))
        for lead in leads
        if day <= cycle_time + timedelta(hours=lead) < day + timedelta(days=1)
    )


def recap_complete_daily_records(
    records: list[WeatherForecastRecord],
    *,
    issue_time: UtcDatetime,
    feature_horizons: dict[str, int],
    ensemble_mode: EnsembleMode,
) -> list[WeatherForecastRecord]:
    require_midnight(issue_time)
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
    if cycle > issue_time:
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
            grid = recap_daily_support(
                cycle, ensure_utc(issue_time + timedelta(days=offset))
            )
            if not grid:
                break
            keys = [(feature, member, stamp) for member in members for stamp in grid]
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
            day_start = issue_time + timedelta(days=offset)
            if any(
                record.parameter == feature
                and day_start <= record.valid_time < day_start + timedelta(days=1)
                and record.valid_time not in grid
                for record in selected
            ):
                raise WeatherProofError(
                    "selected Recap weather has unsupported cadence"
                )
            complete.extend(day_rows)
    return complete
