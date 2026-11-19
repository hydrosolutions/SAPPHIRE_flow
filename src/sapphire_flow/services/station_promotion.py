"""Plan 540 M7a: promote an ONBOARDING station to OPERATIONAL.

``plan_promotion`` is read-only (the dry run); ``apply_promotion`` writes the
targets, the status and one audit row on the caller's connection, so the CLI's
single transaction makes them atomic. The seven blocking conditions are
computed here from the shared fact readers, never from a readiness verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.station_readiness import (
    check_basin,
    check_datum,
    check_discharge_lookback,
    check_naming,
    check_statics,
    check_weather_bindings,
)
from sapphire_flow.services.write_principal import enforce_tenant_isolation
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.enums import (
    AuditEventType,
    ModelAssignmentStatus,
    StationStatus,
    WaterLevelGaugeType,
)
from sapphire_flow.types.station_readiness import CheckStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import AuditLogStore
    from sapphire_flow.services.station_readiness import ReadinessStores
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId
    from sapphire_flow.types.station import StationConfig
    from sapphire_flow.types.write_principal import WritePrincipal

BRIDGE_SENSOR_WARNING = (
    "live raw-distance feed for a bridge-sensor station is not supported by "
    "this plan; confirm the feed delivers converted values (m a.s.l.)"
)


class PromotionError(ConfigurationError):
    """Unknown station (a usage error, not a refusal)."""


class PromotionAction(Enum):
    PROMOTE = auto()
    ALREADY_OPERATIONAL = auto()
    REFUSED = auto()


@dataclass(frozen=True, kw_only=True, slots=True)
class PromotionCondition:
    number: int
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, kw_only=True, slots=True)
class PromotionPlan:
    station: StationConfig
    action: PromotionAction
    target: str
    models: tuple[ModelId, ...]
    conditions: tuple[PromotionCondition, ...]
    warnings: tuple[str, ...]

    def failing(self) -> tuple[PromotionCondition, ...]:
        return tuple(c for c in self.conditions if not c.ok)


def _cond(number: int, name: str, ok: bool, detail: str) -> PromotionCondition:
    return PromotionCondition(number=number, name=name, ok=ok, detail=detail)


def _active_models(station: StationConfig, stores: ReadinessStores) -> list[ModelId]:
    found = {
        a.model_id
        for group in stores.groups.fetch_groups_for_station(station.id)
        for a in stores.groups.fetch_group_model_assignments(group.id)
        if a.status is ModelAssignmentStatus.ACTIVE
    }
    return sorted(found)


def _passed(check_status: CheckStatus) -> bool:
    return check_status is not CheckStatus.FAIL


def _weather_condition(
    station: StationConfig,
    stores: ReadinessStores,
    scope_hru_name: str | None,
    scope_polygon_name: str | None,
) -> PromotionCondition:
    bindings = check_weather_bindings(station, stores.stations)
    if scope_hru_name is None:
        return _cond(
            5,
            "weather_bindings",
            False,
            f"{bindings.detail}; pass --weather-scope to compare the polygon name",
        )
    naming = check_naming(
        station,
        stores.polygons,
        scope_hru_name=scope_hru_name,
        scope_polygon_name=scope_polygon_name,
    )
    ok = _passed(bindings.status) and _passed(naming.status)
    return _cond(5, "weather_bindings", ok, f"{bindings.detail}; {naming.detail}")


def _model_conditions(
    station: StationConfig,
    stores: ReadinessStores,
    models: list[ModelId],
    resolve_model: Callable[[ModelId], ForecastModel | None],
    now: UtcDatetime,
) -> list[PromotionCondition]:
    statics: list[str] = []
    lookback: list[str] = []
    for model_id in models:
        model = resolve_model(model_id)
        if model is None:
            statics.append(f"{model_id}: model is not installed on this host")
            lookback.append(f"{model_id}: model is not installed on this host")
            continue
        s = check_statics(station, model, stores)
        lb = check_discharge_lookback(station, model, stores.observations, now=now)
        statics += [f"{model_id}: {s.detail}"] if s.status is CheckStatus.FAIL else []
        lookback += (
            [f"{model_id}: {lb.detail}"] if lb.status is CheckStatus.FAIL else []
        )
    derived = (
        "water_level" in station.measured_parameters
        and "discharge" not in station.measured_parameters
    )
    if derived and stores.ratings.fetch_active_curve(station.id) is None:
        lookback.append("discharge is derived from level but no active rating curve")
    no_model = "no model to check"
    return [
        _cond(
            6, "statics", bool(models) and not statics, "; ".join(statics) or no_model
        ),
        _cond(
            7,
            "discharge_lookback",
            bool(models) and not lookback,
            "; ".join(lookback) or no_model,
        ),
    ]


def plan_promotion(
    *,
    station_code: str,
    network: str,
    target: str,
    principal: WritePrincipal,
    stores: ReadinessStores,
    audit: AuditLogStore,
    resolve_model: Callable[[ModelId], ForecastModel | None],
    scope_hru_name: str | None,
    scope_polygon_name: str | None,
    clock: Callable[[], UtcDatetime],
) -> PromotionPlan:
    station = stores.stations.fetch_station_by_code(station_code, network)
    if station is None:
        raise PromotionError(f"no station ({station_code!r}, {network!r})")
    enforce_tenant_isolation(
        principal=principal,
        target_tenant_id=station.tenant_id,
        audit_log_store=audit,
        event_type=AuditEventType.STATION_STATUS_CHANGE,
        target_type="station",
        target_id=str(station.id),
        detail={"code": station.code, "to": "operational"},
        now=clock(),
    )
    if station.station_status is StationStatus.OPERATIONAL and station.forecast_targets:
        return PromotionPlan(
            station=station,
            action=PromotionAction.ALREADY_OPERATIONAL,
            target=target,
            models=(),
            conditions=(),
            warnings=(),
        )
    models = _active_models(station, stores)
    datum = check_datum(station)
    basin = check_basin(station, stores.basins)
    conditions = (
        _cond(
            1,
            "status",
            station.station_status is StationStatus.ONBOARDING,
            f"status is {station.station_status.value}; only onboarding is promoted",
        ),
        _cond(2, "datum", _passed(datum.status), datum.detail),
        _cond(3, "basin", _passed(basin.status), basin.detail),
        _cond(
            4,
            "group_and_model",
            bool(models),
            f"active models: {', '.join(models)}"
            if models
            else "station is in no group with an ACTIVE model assignment",
        ),
        _weather_condition(station, stores, scope_hru_name, scope_polygon_name),
        *_model_conditions(station, stores, models, resolve_model, clock()),
    )
    warnings = (
        (BRIDGE_SENSOR_WARNING,)
        if station.water_level_gauge_type is WaterLevelGaugeType.BRIDGE_SENSOR
        else ()
    )
    return PromotionPlan(
        station=station,
        action=(
            PromotionAction.REFUSED
            if any(not c.ok for c in conditions)
            else PromotionAction.PROMOTE
        ),
        target=target,
        models=tuple(models),
        conditions=conditions,
        warnings=warnings,
    )


def apply_promotion(
    plan: PromotionPlan,
    *,
    stores: ReadinessStores,
    audit: AuditLogStore,
    principal: WritePrincipal,
    clock: Callable[[], UtcDatetime],
) -> None:
    if plan.action is not PromotionAction.PROMOTE:
        raise PromotionError(f"plan is {plan.action.name}; nothing to apply")
    station = plan.station
    now = clock()
    stores.stations.update_station(
        replace(station, forecast_targets=frozenset({plan.target}), updated_at=now)
    )
    stores.stations.update_station_status(station.id, StationStatus.OPERATIONAL)
    audit.append_entry(
        AuditEntry.system(  # pyright: ignore[reportUnknownMemberType]
            event_type=AuditEventType.STATION_STATUS_CHANGE,
            target_type="station",
            target_id=str(station.id),
            detail={
                "code": station.code,
                "network": station.network,
                "from": station.station_status.value,
                "to": StationStatus.OPERATIONAL.value,
                "target": plan.target,
                "operator": principal.id,
                "conditions": {c.name: c.ok for c in plan.conditions},
            },
            ip_address=None,
            created_at=now,
        )
    )
