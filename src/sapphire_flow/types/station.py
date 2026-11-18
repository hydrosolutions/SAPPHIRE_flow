from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import GaugingStatus, WaterLevelGaugeType
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

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
        WeatherSourceRole,
        WeatherSourceStatus,
    )
    from sapphire_flow.types.ids import (
        BasinId,
        ModelId,
        PackageId,
        StationGroupId,
        StationId,
        TenantId,
    )


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
    water_level_gauge_type: WaterLevelGaugeType | None = None
    water_level_sensor_masl: float | None = None
    # Plan 147 Slice A: canonical tenant ownership (R4 LOCKED). REQUIRED — no
    # default (parse-don't-validate): every constructor, production or test,
    # must resolve and name a tenant explicitly. Onboarding resolves the config
    # tenant code to a TenantId at the boundary (services.tenant_boundary
    # .resolve_tenant_code); the Pg store reads it from the DB row. The Swiss
    # `sapphire` default lives ONLY in the one-time migration backfill, never as
    # an ongoing domain fallback.
    tenant_id: TenantId

    def __post_init__(self) -> None:
        gauge_type = self.water_level_gauge_type
        datum = self.water_level_datum_masl
        sensor = self.water_level_sensor_masl
        if gauge_type is not None and datum is None:
            raise ValueError("a gauge type needs the gauge-zero datum (m a.s.l.)")
        if sensor is None:
            if gauge_type is WaterLevelGaugeType.BRIDGE_SENSOR:
                raise ValueError("a bridge sensor needs the sensor elevation")
            return
        if gauge_type is not WaterLevelGaugeType.BRIDGE_SENSOR:
            raise ValueError("a sensor elevation applies to bridge sensors only")
        assert datum is not None  # guaranteed by the gauge-type rule above
        if not (math.isfinite(sensor) and sensor > datum):
            raise ValueError(
                f"sensor elevation {sensor} must be above the gauge zero {datum}"
            )


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
    # Plan 147 Slice A: a group belongs to exactly one tenant (additive,
    # per-tenant-unique name). A default is retained here — unlike
    # StationConfig — because a StationGroup is NEVER constructed with an
    # implicit tenant on any production path: the ONLY non-test constructor is
    # PgStationGroupStore._build_group, which reads tenant_id from the DB row
    # (explicit by construction). The default is a test-authoring convenience
    # only; store_group additionally rejects any attempt to change a persisted
    # group's tenant (immutable-on-upsert).
    tenant_id: TenantId = DEFAULT_TENANT_ID


@dataclass(frozen=True, kw_only=True, slots=True)
class StationWeatherSource:
    station_id: StationId
    nwp_source: str
    extraction_type: SpatialRepresentation
    status: WeatherSourceStatus
    role: WeatherSourceRole


@dataclass(frozen=True, kw_only=True, slots=True)
class GatewayPolygonBindingRow:
    """One §5a mapping-table row (``04-basin-static-artifact-contract.md`` §5a).

    Maps a recap Data Gateway forcing column back to a SAP3 station/band.
    Keyed by ``station_id + gateway_hru_name + name`` (Plan 082 Task 2D).
    Schema owner: 082 (this type + the additive table). Population owner:
    Plan 120 (the §5a basin/static package importer).
    """

    station_id: StationId
    basin_id: BasinId
    gateway_hru_name: str
    name: str
    spatial_type: SpatialRepresentation
    band_id: int | None
    # Plan 120 Task 2B: additive provenance columns (owner: 120), optional so
    # 082's own fixture callers that omit them still compile.
    package_id: PackageId | None = None
    imported_at: UtcDatetime | None = None
