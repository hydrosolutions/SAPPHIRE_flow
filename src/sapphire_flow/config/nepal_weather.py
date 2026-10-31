from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sapphire_flow.config._overlay import (
    _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
    load_merged_toml,
)
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.ids import StationGroupId, StationId
from sapphire_flow.types.nepal_onboarding import (
    OperationalWeatherAdmission,
    OperationalWeatherScope,
    WeatherStationPolygon,
)
from sapphire_flow.types.weather import GatewayHruName


class _Polygon(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_id: UUID
    polygon_name: str = Field(min_length=1)


class _Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: UUID
    original_gateway_hru_name: str = Field(min_length=1)
    runtime_gateway_hru_name: str = Field(min_length=1)
    station_polygons: list[_Polygon] = Field(min_length=1)


def load_weather_scope(config_path: Path, name: str) -> OperationalWeatherAdmission:
    data = cast(
        "dict[str, Any]", load_merged_toml(config_path, _resolve_overlay_paths())
    )
    try:
        parsed = _Scope.model_validate(data["weather_history"]["scopes"][name])
        return OperationalWeatherAdmission(
            original_gateway_hru_name=GatewayHruName(parsed.original_gateway_hru_name),
            scope=OperationalWeatherScope(
                group_id=StationGroupId(parsed.group_id),
                gateway_hru_name=GatewayHruName(parsed.runtime_gateway_hru_name),
                station_polygons=tuple(
                    WeatherStationPolygon(
                        station_id=StationId(p.station_id), polygon_name=p.polygon_name
                    )
                    for p in parsed.station_polygons
                ),
            ),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ConfigurationError("missing or invalid weather history scope") from exc
