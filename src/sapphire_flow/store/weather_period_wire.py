from __future__ import annotations

import json
from datetime import timedelta
from typing import TypeVar, cast
from uuid import UUID

from pydantic import TypeAdapter

from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import ForecastId, StationId
from sapphire_flow.types.operational_weather_v2 import (
    DerivedWeatherUseV2,
    OperationalWeatherWindowV2,
    WeatherCacheCompletionV2,
    WeatherCacheEntryV2,
    WeatherUseV2,
)
from sapphire_flow.types.time_grid import TimeGrid
from sapphire_flow.types.weather import WeatherForecastRecord
from sapphire_flow.types.weather_gap import GapWeatherParameter

T = TypeVar("T")
_NAMESPACE = {
    "UtcDatetime": UtcDatetime,
    "StationId": StationId,
    "ForecastId": ForecastId,
    "UUID": UUID,
    "SpatialRepresentation": SpatialRepresentation,
    "WeatherForecastRecord": WeatherForecastRecord,
    "GapWeatherParameter": GapWeatherParameter,
}
WINDOW_V2 = TypeAdapter(OperationalWeatherWindowV2)
ENTRY_V2 = TypeAdapter(WeatherCacheEntryV2)
COMPLETION_V2 = TypeAdapter(WeatherCacheCompletionV2)
USE_V2 = TypeAdapter(WeatherUseV2)
DERIVED_V2 = TypeAdapter(DerivedWeatherUseV2)
for _adapter in (WINDOW_V2, ENTRY_V2, COMPLETION_V2, USE_V2, DERIVED_V2):
    _adapter.rebuild(_types_namespace=_NAMESPACE)
_GRID = TypeAdapter(TimeGrid)


def _wire_grids(value: object) -> object:
    if isinstance(value, list):
        return [_wire_grids(item) for item in cast("list[object]", value)]
    if not isinstance(value, dict):
        return value
    result = {
        key: _wire_grids(item) for key, item in cast("dict[str, object]", value).items()
    }
    if "grid" in result:
        grid = _GRID.validate_json(
            json.dumps(result["grid"]), strict=True, extra="forbid"
        )
        result["grid"] = {
            "step_seconds": int(grid.step.total_seconds()),
            "phase_offset_seconds": int(grid.phase.total_seconds()),
        }
    return result


def _domain_grids(value: object) -> object:
    if isinstance(value, list):
        return [_domain_grids(item) for item in cast("list[object]", value)]
    if not isinstance(value, dict):
        return value
    result = {
        key: _domain_grids(item)
        for key, item in cast("dict[str, object]", value).items()
    }
    if "grid" in result:
        grid = result["grid"]
        if not isinstance(grid, dict) or set(cast("dict[str, object]", grid)) != {
            "step_seconds",
            "phase_offset_seconds",
        }:
            raise ValueError("v2 weather requires explicit grid fields")
        fields = cast("dict[str, object]", grid)
        step, phase = fields["step_seconds"], fields["phase_offset_seconds"]
        if type(step) is not int or type(phase) is not int or step != 86400:
            raise ValueError("v2 weather grid requires integer daily seconds")
        TimeGrid(step=timedelta(seconds=step), phase=timedelta(seconds=phase))
        result["grid"] = {"step": f"PT{step}S", "phase": f"PT{phase}S"}
    return result


def dump_period_wire(adapter: TypeAdapter[T], value: T) -> dict[str, object]:
    return cast(
        "dict[str, object]", _wire_grids(adapter.dump_python(value, mode="json"))
    )


def load_period_wire(adapter: TypeAdapter[T], raw: object) -> T:
    return adapter.validate_json(
        json.dumps(_domain_grids(raw), allow_nan=False), strict=True, extra="forbid"
    )


def period_use_wire(use: WeatherUseV2 | DerivedWeatherUseV2) -> dict[str, object]:
    if isinstance(use, WeatherUseV2):
        return dump_period_wire(USE_V2, use)
    return dump_period_wire(DERIVED_V2, use)
