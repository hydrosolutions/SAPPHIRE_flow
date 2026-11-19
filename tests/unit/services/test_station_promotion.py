from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import TenantIsolationError
from sapphire_flow.services.station_promotion import (
    BRIDGE_SENSOR_WARNING,
    PromotionAction,
    PromotionError,
    PromotionPlan,
    apply_promotion,
    plan_promotion,
)
from sapphire_flow.services.station_readiness import ReadinessStores
from sapphire_flow.types.enums import (
    AuditEventType,
    ModelAssignmentStatus,
    SpatialRepresentation,
    StationStatus,
    WaterLevelGaugeType,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.station import (
    GatewayPolygonBindingRow,
    StationWeatherSource,
)
from sapphire_flow.types.write_principal import WritePrincipal
from tests.fakes.fake_stores import FakeAuditLogStore
from tests.unit.services.test_station_readiness import (
    MODEL_ID,
    NOW,
    TestGroupAndModel,
    World,
    _basin,  # pyright: ignore[reportPrivateUsage]
    _model,  # pyright: ignore[reportPrivateUsage]
)

HRU = "hru1"
ANY = WritePrincipal(id=None, tenant_id=None)


def _ready(**station_kwargs: object) -> World:
    kwargs: dict[str, object] = {"station_status": StationStatus.ONBOARDING}
    kwargs.update(station_kwargs)
    basin = _basin({})
    world = World(basin_id=basin.id, **kwargs)
    world.basins.store_basin(basin)
    TestGroupAndModel()._group(world, ModelAssignmentStatus.ACTIVE)  # pyright: ignore[reportPrivateUsage]
    for role in WeatherSourceRole:
        world.stations.store_weather_source(
            StationWeatherSource(
                station_id=world.station.id,
                nwp_source=f"src_{role.value}",
                extraction_type=SpatialRepresentation.BASIN_AVERAGE,
                status=WeatherSourceStatus.ACTIVE,
                role=role,
            )
        )
    world.polygons.store_binding(
        GatewayPolygonBindingRow(
            station_id=world.station.id,
            basin_id=basin.id,
            gateway_hru_name=HRU,
            name=HRU,
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
        )
    )
    world.add_discharge(5)
    return world


def _stores(world: World) -> ReadinessStores:
    return ReadinessStores(
        stations=world.stations,
        basins=world.basins,
        observations=world.observations,
        forcing=world.forcing,
        ratings=world.ratings,
        groups=world.groups,
        polygons=world.polygons,
    )


def _plan(
    world: World,
    audit: FakeAuditLogStore,
    *,
    principal: WritePrincipal = ANY,
    scope: str | None = HRU,
    code: str | None = None,
    statics: frozenset[str] = frozenset(),
    installed: bool = True,
) -> PromotionPlan:
    return plan_promotion(
        station_code=code or world.station.code,
        network="research",
        target="discharge",
        principal=principal,
        stores=_stores(world),
        audit=audit,
        resolve_model=lambda _m: _model(statics) if installed else None,  # type: ignore[return-value]
        scope_hru_name=scope,
        scope_polygon_name=scope,
        clock=lambda: NOW,
    )


def _apply(world: World, audit: FakeAuditLogStore, plan: PromotionPlan) -> None:
    apply_promotion(
        plan, stores=_stores(world), audit=audit, principal=ANY, clock=lambda: NOW
    )


class TestPromote:
    def test_ready_station_is_promoted_with_one_audit_row(self) -> None:
        world, audit = _ready(), FakeAuditLogStore()
        plan = _plan(world, audit)
        assert plan.action is PromotionAction.PROMOTE
        assert plan.models == (MODEL_ID,)
        _apply(world, audit, plan)
        stored = world.stations.fetch_station_by_code(world.station.code, "research")
        assert stored is not None
        assert stored.station_status is StationStatus.OPERATIONAL
        assert stored.forecast_targets == frozenset({"discharge"})
        (entry,) = audit.entries
        assert entry.event_type is AuditEventType.STATION_STATUS_CHANGE
        assert entry.detail is not None
        assert (entry.detail["from"], entry.detail["to"]) == (
            "onboarding",
            "operational",
        )
        assert entry.detail["conditions"] == {c.name: True for c in plan.conditions}

    def test_second_run_is_a_noop(self) -> None:
        world, audit = _ready(), FakeAuditLogStore()
        _apply(world, audit, _plan(world, audit))
        again = _plan(world, audit)
        assert again.action is PromotionAction.ALREADY_OPERATIONAL
        with pytest.raises(PromotionError):
            _apply(world, audit, again)
        assert len(audit.entries) == 1

    def test_bridge_sensor_warns_without_blocking(self) -> None:
        world = _ready()
        _set_station(
            world,
            water_level_gauge_type=WaterLevelGaugeType.BRIDGE_SENSOR,
            water_level_datum_masl=100.0,
            water_level_sensor_masl=105.0,
            measured_parameters=frozenset({"discharge", "water_level"}),
        )
        plan = _plan(world, FakeAuditLogStore())
        assert plan.action is PromotionAction.PROMOTE
        assert plan.warnings == (BRIDGE_SENSOR_WARNING,)


def _set_station(w: World, **changes: object) -> None:
    station = dataclasses.replace(w.station, **changes)  # type: ignore[arg-type]
    w.stations.update_station(station)
    w.station = station


def _break_status(w: World) -> None:
    w.stations.update_station_status(w.station.id, StationStatus.SUSPENDED)


def _break_datum(w: World) -> None:
    _set_station(w, measured_parameters=frozenset({"discharge", "water_level"}))


def _break_basin(w: World) -> None:
    _set_station(w, basin_id=None)


def _break_group(w: World) -> None:
    w.groups = type(w.groups)()


def _break_weather(w: World) -> None:
    w.polygons.rows.clear()


def _break_lookback(w: World) -> None:
    w.observations = type(w.observations)()


BREAKERS = {
    1: _break_status,
    2: _break_datum,
    3: _break_basin,
    4: _break_group,
    5: _break_weather,
    7: _break_lookback,
}


class TestRefusals:
    @pytest.mark.parametrize("number", sorted(BREAKERS))
    def test_each_condition_blocks_and_nothing_is_written(self, number: int) -> None:
        world, audit = _ready(), FakeAuditLogStore()
        BREAKERS[number](world)
        plan = _plan(world, audit)
        assert plan.action is PromotionAction.REFUSED
        assert number in {c.number for c in plan.failing()}
        assert audit.entries == []

    def test_statics_missing_blocks(self) -> None:
        plan = _plan(_ready(), FakeAuditLogStore(), statics=frozenset({"area"}))
        assert [c.number for c in plan.failing()] == [6]

    def test_every_failing_condition_is_listed(self) -> None:
        world = _ready()
        for number in (3, 4, 1):
            BREAKERS[number](world)
        plan = _plan(world, FakeAuditLogStore())
        assert {1, 3, 4} <= {c.number for c in plan.failing()}

    def test_missing_weather_scope_blocks_condition_5(self) -> None:
        plan = _plan(_ready(), FakeAuditLogStore(), scope=None)
        assert [c.number for c in plan.failing()] == [5]

    def test_uninstalled_model_blocks_statics_and_lookback(self) -> None:
        plan = _plan(_ready(), FakeAuditLogStore(), installed=False)
        assert [c.number for c in plan.failing()] == [6, 7]

    def test_decommissioned_station_is_refused(self) -> None:
        world = _ready()
        world.stations.update_station_status(
            world.station.id, StationStatus.DECOMMISSIONED
        )
        assert _plan(world, FakeAuditLogStore()).action is PromotionAction.REFUSED

    def test_apply_of_a_refused_plan_raises(self) -> None:
        world, audit = _ready(), FakeAuditLogStore()
        _break_basin(world)
        with pytest.raises(PromotionError):
            _apply(world, audit, _plan(world, audit))


class TestGates:
    def test_cross_tenant_raises_audits_and_changes_nothing(self) -> None:
        world, audit = _ready(), FakeAuditLogStore()
        principal = WritePrincipal(id=None, tenant_id=TenantId(uuid4()))
        with pytest.raises(TenantIsolationError):
            _plan(world, audit, principal=principal)
        (entry,) = audit.entries
        assert entry.detail is not None
        assert entry.detail["outcome"] == "rejected_tenant_mismatch"
        stored = world.stations.fetch_station_by_code(world.station.code, "research")
        assert stored is not None
        assert stored.station_status is StationStatus.ONBOARDING

    def test_unknown_station_is_a_clear_error(self) -> None:
        with pytest.raises(PromotionError, match="no station"):
            _plan(_ready(), FakeAuditLogStore(), code="nope")
