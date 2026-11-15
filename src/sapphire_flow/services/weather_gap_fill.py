from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, cast

import polars as pl

from sapphire_flow.adapters.forecast_interface import ForecastInterfaceAdapter
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.forecast_evidence import StationSourceEvidence
from sapphire_flow.types.weather_gap import (
    GAP_WEATHER_PARAMETERS,
    GapWeatherParameter,
    WeatherGapEndpoint,
    WeatherGapFill,
    WeatherGapMethod,
    WeatherGapMode,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sapphire_flow.config.weather_gap import WeatherGapConfig
    from sapphire_flow.protocols.forecast_model import ForecastModel, GroupForecastModel
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.model import GroupModelInputs, StationModelInputs
    from sapphire_flow.types.weather import WeatherForecastRecord


@dataclass(frozen=True, kw_only=True, slots=True)
class FilledWeather:
    frame: pl.DataFrame
    fills: tuple[WeatherGapFill, ...]


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


def stored_weather_donors(
    records: Sequence[WeatherForecastRecord],
    *,
    station_id: StationId,
    issue_time: UtcDatetime,
    parameters: frozenset[str],
) -> dict[tuple[str, UtcDatetime], WeatherGapFill]:
    grouped: dict[tuple[str, UtcDatetime, UtcDatetime], list[WeatherForecastRecord]] = (
        defaultdict(list)
    )
    for row in records:
        if (
            row.station_id == station_id
            and row.parameter in parameters
            and row.nwp_source == "ifs_ecmwf"
            and row.cycle_time <= issue_time
            and row.valid_time < issue_time
            and row.member_id == 0
        ):
            day = ensure_utc(
                row.valid_time.replace(hour=0, minute=0, second=0, microsecond=0)
            )
            grouped[(row.parameter, day, row.cycle_time)].append(row)
    result: dict[tuple[str, UtcDatetime], WeatherGapFill] = {}
    for (parameter, day, _cycle), rows in sorted(
        grouped.items(), key=lambda item: item[0][2]
    ):
        try:
            value = math.fsum(row.value for row in rows)
            if parameter == "temperature":
                value /= len(rows)
            fill = WeatherGapFill(
                parameter=cast("GapWeatherParameter", parameter),
                day=day,
                value=value,
                method=WeatherGapMethod.STORED_FORECAST,
                donor_records=tuple(sorted(rows, key=lambda row: row.valid_time)),
            )
        except ValueError:
            continue
        result[(parameter, day)] = fill
    return result


def fill_daily_weather(
    frame: pl.DataFrame,
    *,
    issue_time: UtcDatetime,
    lookbacks: Mapping[str, int],
    max_gap_days: int,
    donors: Mapping[tuple[str, UtcDatetime], WeatherGapFill] | None = None,
    precipitation_lower_bounds: Mapping[UtcDatetime, float] | None = None,
) -> FilledWeather:
    if not lookbacks:
        return FilledWeather(frame=frame, fills=())
    if not 1 <= max_gap_days <= 7:
        raise ValueError("weather gap limit must be between one and seven days")
    anchor = ensure_utc(issue_time.replace(hour=0, minute=0, second=0, microsecond=0))
    rows = {row["timestamp"]: dict(row) for row in frame.iter_rows(named=True)}
    if len(rows) != frame.height:
        raise ValueError("weather filling refuses duplicate daily timestamps")
    fills: list[WeatherGapFill] = []
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
                    fill = WeatherGapFill(
                        parameter=cast("GapWeatherParameter", parameter),
                        day=day,
                        value=value,
                        method=WeatherGapMethod.LINEAR,
                        endpoints=(
                            WeatherGapEndpoint(day=days[start - 1], value=left),
                            WeatherGapEndpoint(day=days[stop], value=right),
                        ),
                    )
                if fill is None:
                    continue
                if parameter == "precipitation" and fill.value < (
                    precipitation_lower_bounds or {}
                ).get(day, 0):
                    continue
                rows.setdefault(day, {"timestamp": day})[parameter] = fill.value
                fills.append(fill)
    if not fills:
        return FilledWeather(frame=frame, fills=())
    return FilledWeather(
        frame=pl.DataFrame(
            [rows[key] for key in sorted(rows)], infer_schema_length=None
        ),
        fills=tuple(fills),
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
