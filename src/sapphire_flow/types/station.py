from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import timedelta

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import GeoCoord
    from sapphire_flow.types.enums import (
        RegulationType,
        SpatialRepresentation,
        StationKind,
        StationOwnership,
        StationStatus,
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
    forecast_target: Literal["discharge", "water_level", "both"] | None
    measured_parameters: frozenset[str]
    station_status: StationStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime
    network: str
    ownership: StationOwnership
    wigos_id: str | None


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelAssignment:
    station_id: StationId
    model_id: ModelId
    time_step: timedelta
    is_active: bool
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
    # TODO(v0-store): convert to enum per "enums over booleans" rule
    active: bool
