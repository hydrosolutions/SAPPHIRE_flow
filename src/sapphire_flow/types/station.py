from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NamedTuple

if TYPE_CHECKING:
    from datetime import timedelta

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import GeoCoord
    from sapphire_flow.types.enums import (
        RegulationType,
        SpatialRepresentation,
        StationKind,
        StationStatus,
    )
    from sapphire_flow.types.ids import BasinId, ModelId, StationGroupId, StationId


class StationConfig(NamedTuple):
    id: StationId
    code: str
    name: str
    location: GeoCoord
    station_kind: StationKind
    basin_id: BasinId | None
    timezone: str
    regulation_type: RegulationType | None
    forecast_target: Literal["discharge", "water_level", "both"] | None
    measured_parameters: frozenset[str]
    station_status: StationStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ModelAssignment(NamedTuple):
    station_id: StationId
    model_id: ModelId
    time_step: timedelta
    is_active: bool
    priority: int
    created_at: UtcDatetime


class StationGroup(NamedTuple):
    id: StationGroupId
    name: str
    station_ids: frozenset[StationId]
    created_at: UtcDatetime


class StationWeatherSource(NamedTuple):
    station_id: StationId
    nwp_source: str
    extraction_type: SpatialRepresentation
    active: bool
