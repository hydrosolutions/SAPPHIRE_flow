from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import GaugingStatus

if TYPE_CHECKING:
    from datetime import timedelta

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import GeoCoord
    from sapphire_flow.types.enums import (
        ModelAssignmentStatus,
        RegulationType,
        SpatialRepresentation,
        StationKind,
        StationOwnership,
        StationStatus,
        WeatherSourceStatus,
    )
    from sapphire_flow.types.ids import BasinId, ModelId, StationGroupId, StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class StationConfig:
    id: StationId
    code: str
    name: str
    location: GeoCoord
    station_kind: StationKind
    basin_id: BasinId | None
    timezone: str
    regulation_type: RegulationType | None
    forecast_targets: frozenset[str] | None
    measured_parameters: frozenset[str]
    station_status: StationStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime
    network: str
    ownership: StationOwnership
    wigos_id: str | None
    gauging_status: GaugingStatus = GaugingStatus.GAUGED
    water_level_datum_masl: float | None = None
    water_level_unit: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelAssignment:
    station_id: StationId
    model_id: ModelId
    time_step: timedelta
    status: ModelAssignmentStatus
    priority: int
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupModelAssignment:
    group_id: StationGroupId
    model_id: ModelId
    time_step: timedelta
    status: ModelAssignmentStatus
    priority: int
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class StationGroup:
    id: StationGroupId
    name: str
    station_ids: frozenset[StationId]
    description: str | None = None
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class StationWeatherSource:
    station_id: StationId
    nwp_source: str
    extraction_type: SpatialRepresentation
    status: WeatherSourceStatus
