from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, cast, overload

import polars as pl

from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.forecast_evidence import StationSourceEvidence
from sapphire_flow.types.operational_weather import require_daily_grid_boundary
from sapphire_flow.types.weather_gap import (
    GAP_WEATHER_PARAMETERS,
    GapWeatherParameter,
    WeatherGapEndpoint,
    WeatherGapFill,
    WeatherGapMethod,
    WeatherGapMode,
)
from sapphire_flow.types.weather_gap_v2 import WeatherGapEndpointV2, WeatherGapFillV2

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sapphire_flow.config.weather_gap import WeatherGapConfig
    from sapphire_flow.protocols.forecast_model import ForecastModel, GroupForecastModel
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.model import GroupModelInputs, StationModelInputs
    from sapphire_flow.types.time_grid import TimeGrid
    from sapphire_flow.types.weather import WeatherForecastRecord


@dataclass(frozen=True, kw_only=True, slots=True)
class FilledWeather:
    frame: pl.DataFrame
    fills: tuple[WeatherGapFill, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class FilledPeriodWeather:
    frame: pl.DataFrame
    fills: tuple[WeatherGapFillV2, ...]


def weather_fill_parameters(
    model: ForecastModel | GroupForecastModel,
    *,
    model_id: ModelId,
    config: WeatherGapConfig,
    time_step: timedelta,
) -> frozenset[str]:
    mode = config.for_model(str(model_id)).mode
    if time_step != timedelta(days=1) or mode is WeatherGapMode.DISABLED:
        return frozenset()
    declared = model.data_requirements.past_dynamic_features & GAP_WEATHER_PARAMETERS
    if mode is WeatherGapMode.ALWAYS:
        return declared
    if not isinstance(model, ForecastInterfaceAdapter):
        return frozenset()
    tolerances = model.past_weather_nan_tolerances(time_step)
    return frozenset(name for name in declared if tolerances.get(name) == 0)


@overload
def stored_weather_donors(
    records: Sequence[WeatherForecastRecord],
    *,
    station_id: StationId,
    issue_time: UtcDatetime,
    parameters: frozenset[str],
    grid: None = None,
    past_end: None = None,
) -> dict[tuple[str, UtcDatetime], WeatherGapFill]: ...


@overload
def stored_weather_donors(
    records: Sequence[WeatherForecastRecord],
    *,
    station_id: StationId,
    issue_time: UtcDatetime,
    parameters: frozenset[str],
    grid: TimeGrid,
    past_end: UtcDatetime,
) -> dict[tuple[str, UtcDatetime], WeatherGapFillV2]: ...


@overload
def stored_weather_donors(
    records: Sequence[WeatherForecastRecord],
    *,
    station_id: StationId,
    issue_time: UtcDatetime,
    parameters: frozenset[str],
    grid: TimeGrid | None = None,
    past_end: UtcDatetime | None = None,
) -> Mapping[tuple[str, UtcDatetime], WeatherGapFill | WeatherGapFillV2]: ...


def stored_weather_donors(
    records: Sequence[WeatherForecastRecord],
    *,
    station_id: StationId,
    issue_time: UtcDatetime,
    parameters: frozenset[str],
    grid: TimeGrid | None = None,
    past_end: UtcDatetime | None = None,
) -> Mapping[tuple[str, UtcDatetime], WeatherGapFill | WeatherGapFillV2]:
    if grid is not None:
        if past_end is None:
            raise ValueError("period donors require explicit past end")
        require_daily_grid_boundary(past_end, grid)
        if past_end > issue_time:
            raise ValueError("past donor window follows eligibility cutoff")
    grouped: dict[tuple[str, UtcDatetime, UtcDatetime], list[WeatherForecastRecord]] = (
        defaultdict(list)
    )
    for row in records:
        if (
            row.station_id == station_id
            and row.parameter in parameters
            and row.nwp_source == "ifs_ecmwf"
            and row.cycle_time <= issue_time
            and row.valid_time
            < (past_end if grid is not None and past_end is not None else issue_time)
            and row.member_id == 0
        ):
            day = ensure_utc(
                row.valid_time.replace(hour=0, minute=0, second=0, microsecond=0)
            )
            if grid is not None:
                day = grid.floor(row.valid_time)
            grouped[(row.parameter, day, row.cycle_time)].append(row)
    result: dict[tuple[str, UtcDatetime], WeatherGapFill | WeatherGapFillV2] = {}
    for (parameter, day, _cycle), rows in sorted(
        grouped.items(), key=lambda item: item[0][2]
    ):
        try:
            value = math.fsum(row.value for row in rows)
            if parameter == "temperature":
                value /= len(rows)
            fill: WeatherGapFill | WeatherGapFillV2 = (
                WeatherGapFill(
                    parameter=cast("GapWeatherParameter", parameter),
                    day=day,
                    value=value,
                    method=WeatherGapMethod.STORED_FORECAST,
                    donor_records=tuple(sorted(rows, key=lambda row: row.valid_time)),
                )
                if grid is None
                else WeatherGapFillV2(
                    grid=grid,
                    parameter=cast("GapWeatherParameter", parameter),
                    start=day,
                    value=value,
                    method=WeatherGapMethod.STORED_FORECAST,
                    donor_records=tuple(sorted(rows, key=lambda row: row.valid_time)),
                )
            )
        except ValueError:
            continue
        result[(parameter, day)] = fill
    return result


@overload
def fill_daily_weather(
    frame: pl.DataFrame,
    *,
    issue_time: UtcDatetime,
    lookbacks: Mapping[str, int],
    max_gap_days: int,
    donors: Mapping[tuple[str, UtcDatetime], WeatherGapFill] | None = None,
    precipitation_lower_bounds: Mapping[UtcDatetime, float] | None = None,
    grid: None = None,
    past_end: None = None,
) -> FilledWeather: ...


@overload
def fill_daily_weather(
    frame: pl.DataFrame,
    *,
    issue_time: UtcDatetime,
    lookbacks: Mapping[str, int],
    max_gap_days: int,
    donors: Mapping[tuple[str, UtcDatetime], WeatherGapFillV2] | None = None,
    precipitation_lower_bounds: Mapping[UtcDatetime, float] | None = None,
    grid: TimeGrid,
    past_end: UtcDatetime,
) -> FilledPeriodWeather: ...


@overload
def fill_daily_weather(
    frame: pl.DataFrame,
    *,
    issue_time: UtcDatetime,
    lookbacks: Mapping[str, int],
    max_gap_days: int,
    donors: Mapping[tuple[str, UtcDatetime], WeatherGapFill | WeatherGapFillV2]
    | None = None,
    precipitation_lower_bounds: Mapping[UtcDatetime, float] | None = None,
    grid: TimeGrid | None = None,
    past_end: UtcDatetime | None = None,
) -> FilledWeather | FilledPeriodWeather: ...


def fill_daily_weather(
    frame: pl.DataFrame,
    *,
    issue_time: UtcDatetime,
    lookbacks: Mapping[str, int],
    max_gap_days: int,
    donors: Mapping[tuple[str, UtcDatetime], WeatherGapFill | WeatherGapFillV2]
    | None = None,
    precipitation_lower_bounds: Mapping[UtcDatetime, float] | None = None,
    grid: TimeGrid | None = None,
    past_end: UtcDatetime | None = None,
) -> FilledWeather | FilledPeriodWeather:
    if grid is not None:
        if past_end is None:
            raise ValueError("period filling requires explicit past end")
        require_daily_grid_boundary(past_end, grid)
        if past_end > issue_time:
            raise ValueError("past period end follows eligibility cutoff")
    if not lookbacks:
        return (
            FilledWeather(frame=frame, fills=())
            if grid is None
            else FilledPeriodWeather(frame=frame, fills=())
        )
    if not 1 <= max_gap_days <= 7:
        raise ValueError("weather gap limit must be between one and seven days")
    anchor = ensure_utc(issue_time.replace(hour=0, minute=0, second=0, microsecond=0))
    if grid is not None:
        assert past_end is not None
        anchor = past_end
    rows = {row["timestamp"]: dict(row) for row in frame.iter_rows(named=True)}
    if len(rows) != frame.height:
        raise ValueError("weather filling refuses duplicate daily timestamps")
    fills: list[WeatherGapFill | WeatherGapFillV2] = []
    for parameter, lookback in sorted(lookbacks.items()):
        if parameter not in GAP_WEATHER_PARAMETERS:
            raise ValueError("unsupported weather fill parameter")
        days = [
            ensure_utc(anchor - timedelta(days=offset))
            for offset in range(lookback, 0, -1)
        ]
        original = [rows.get(day, {}).get(parameter) for day in days]
        missing = [
            value is None or (isinstance(value, float) and math.isnan(value))
            for value in original
        ]
        index = 0
        while index < len(days):
            if not missing[index]:
                index += 1
                continue
            start = index
            while index < len(days) and missing[index]:
                index += 1
            stop = index
            if stop - start > max_gap_days:
                continue
            for offset in range(start, stop):
                day = days[offset]
                fill = (donors or {}).get((parameter, day))
                if fill is not None:
                    if grid is not None:
                        if (
                            not isinstance(fill, WeatherGapFillV2)
                            or fill.grid != grid
                            or fill.start != day
                            or fill.parameter != parameter
                            or any(
                                row.cycle_time > issue_time
                                for row in fill.donor_records
                            )
                        ):
                            raise ValueError(
                                "period donor differs from requested context"
                            )
                    elif not isinstance(fill, WeatherGapFill):
                        raise ValueError("v1 filling cannot consume v2 donors")
                if fill is None and start > 0 and stop < len(days):
                    if parameter == "precipitation" and stop - start != 1:
                        continue
                    left_value, right_value = original[start - 1], original[stop]
                    assert left_value is not None and right_value is not None
                    left, right = float(left_value), float(right_value)
                    if not math.isfinite(left) or not math.isfinite(right):
                        continue
                    value = left + (right - left) * (
                        (offset - start + 1) / (stop - start + 1)
                    )
                    if parameter != "temperature" and min(left, right, value) < 0:
                        continue
                    fill = (
                        WeatherGapFill(
                            parameter=cast("GapWeatherParameter", parameter),
                            day=day,
                            value=value,
                            method=WeatherGapMethod.LINEAR,
                            endpoints=(
                                WeatherGapEndpoint(day=days[start - 1], value=left),
                                WeatherGapEndpoint(day=days[stop], value=right),
                            ),
                        )
                        if grid is None
                        else WeatherGapFillV2(
                            grid=grid,
                            parameter=cast("GapWeatherParameter", parameter),
                            start=day,
                            value=value,
                            method=WeatherGapMethod.LINEAR,
                            endpoints=(
                                WeatherGapEndpointV2(
                                    start=days[start - 1],
                                    end=days[start - 1] + grid.step,
                                    value=left,
                                ),
                                WeatherGapEndpointV2(
                                    start=days[stop],
                                    end=days[stop] + grid.step,
                                    value=right,
                                ),
                            ),
                        )
                    )
                if fill is None:
                    continue
                if parameter == "precipitation" and fill.value < (
                    precipitation_lower_bounds or {}
                ).get(day, 0):
                    continue
                rows.setdefault(day, {"timestamp": day})[parameter] = fill.value
                fills.append(fill)
    result_frame = (
        frame
        if not fills
        else pl.DataFrame([rows[key] for key in sorted(rows)], infer_schema_length=None)
    )
    if grid is not None:
        return FilledPeriodWeather(
            frame=result_frame, fills=cast("tuple[WeatherGapFillV2, ...]", tuple(fills))
        )
    return FilledWeather(
        frame=result_frame, fills=cast("tuple[WeatherGapFill, ...]", tuple(fills))
    )


def prepare_station_weather(
    inputs: StationModelInputs,
    *,
    model: ForecastModel | GroupForecastModel,
    model_id: ModelId,
    config: WeatherGapConfig,
) -> StationModelInputs:
    source = inputs.source_evidence or StationSourceEvidence()
    # Selected weather owns its raw snapshot and qualified donor receipts.
    if source.weather_use is not None:
        return inputs
    parameters = weather_fill_parameters(
        model, model_id=model_id, config=config, time_step=inputs.time_step
    )
    declared = dict(model.data_requirements.declared_lookbacks)
    donors = stored_weather_donors(
        (*source.future_weather, *source.tail_weather),
        station_id=inputs.station_id,
        issue_time=inputs.issue_time,
        parameters=parameters,
    )
    result = fill_daily_weather(
        inputs.data.past_dynamic,
        issue_time=inputs.issue_time,
        lookbacks={
            name: declared.get(name, model.data_requirements.lookback_steps)
            for name in parameters
        },
        max_gap_days=config.for_model(str(model_id)).max_gap_days,
        donors=donors,
    )
    if not result.fills:
        return inputs
    return replace(
        inputs,
        data=replace(inputs.data, past_dynamic=result.frame),
        source_evidence=replace(
            source, weather_gap_fills=(*source.weather_gap_fills, *result.fills)
        ),
    )


def prepare_group_weather(
    inputs: GroupModelInputs,
    *,
    model: GroupForecastModel,
    model_id: ModelId,
    config: WeatherGapConfig,
) -> GroupModelInputs:
    from sapphire_flow.types.model import StationModelInputs

    sources = dict(inputs.source_evidence)
    frames: list[pl.DataFrame] = []
    changed = False
    for sid in inputs.station_ids:
        station = StationModelInputs(
            station_id=sid,
            data=inputs.for_station(sid),
            issue_time=inputs.issue_time,
            forecast_horizon_steps=inputs.forecast_horizon_steps,
            time_step=inputs.time_step,
            forcing_route=inputs.forcing_route,
            source_evidence=sources.get(sid),
        )
        prepared = prepare_station_weather(
            station, model=model, model_id=model_id, config=config
        )
        changed |= prepared is not station
        frames.append(
            prepared.data.past_dynamic.with_columns(
                pl.lit(str(sid)).alias("station_id")
            )
        )
        if prepared.source_evidence is not None:
            sources[sid] = prepared.source_evidence
    if not changed:
        return inputs
    return replace(
        inputs,
        past_dynamic=pl.concat(frames, how="diagonal_relaxed"),
        source_evidence=tuple(
            (sid, sources[sid]) for sid in inputs.station_ids if sid in sources
        ),
    )
