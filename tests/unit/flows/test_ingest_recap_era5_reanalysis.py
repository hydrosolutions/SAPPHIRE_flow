from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, TypeVar, cast
from uuid import uuid4

import pandas as pd
import pytest

from sapphire_flow.adapters.recap_gateway import (
    RecapClientLike,
    RecapGatewayReanalysisAdapter,
    StoreBackedGatewayPolygonResolver,
)
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.flows import ingest_recap_era5_reanalysis as module
from sapphire_flow.services import nepal_onboarding as service
from sapphire_flow.services.nepal_onboarding import history_binding
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    StationStatus,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import BasinId, StationGroupId, StationId, TenantId
from sapphire_flow.types.nepal_onboarding import (
    GATEWAY_HISTORY_SOURCE,
    HistoryCoverage,
    HistoryWindow,
    OperationalWeatherScope,
    WeatherStationPolygon,
)
from sapphire_flow.types.station import (
    GatewayPolygonBindingRow,
    StationConfig,
    StationGroup,
)
from sapphire_flow.types.weather import GatewayHruName
from tests.conftest import make_raw_historical_forcing, make_station_config
from tests.fakes.fake_stores import (
    FakeHistoricalForcingStore,
    FakeStationGroupStore,
    FakeStationStore,
    FakeTenantStore,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Connection

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.station import StationWeatherSource

START = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
WINDOW = HistoryWindow(start=START, end=ensure_utc(START + timedelta(days=70)))
IDENTITY = DeploymentIdentityConfig(
    writable_tenants=frozenset({"chwrr"}), global_admin=False
)


T = TypeVar("T")


def injected(value: T) -> Callable[[object], T]:
    def construct(_: object) -> T:
        return value

    return construct


class TestIngestRecapEra5Reanalysis:
    @pytest.mark.parametrize("invalid", [None, "source", "station", "end", "nan"])
    def test_bounds_provenance_and_missing_parameter_coverage(
        self, monkeypatch: pytest.MonkeyPatch, invalid: str | None
    ) -> None:
        station = make_station_config()
        store = FakeHistoricalForcingStore()

        def legacy_stations(*args: object, **kwargs: object) -> list[StationConfig]:
            return [station]

        def legacy_bindings(*args: object) -> list[StationWeatherSource]:
            return [history_binding(station.id)]

        monkeypatch.setattr(module, "chwrr_stations", legacy_stations)
        monkeypatch.setattr(
            module,
            "require_history_bindings",
            legacy_bindings,
        )
        monkeypatch.setattr(module, "PgHistoricalForcingStore", injected(store))
        windows: list[tuple[UtcDatetime, UtcDatetime]] = []

        class Adapter:
            def fetch_reanalysis(
                self,
                station_configs: list[StationWeatherSource],
                start: UtcDatetime,
                end: UtcDatetime,
                parameters: list[str],
            ) -> list[RawHistoricalForcing]:
                windows.append((start, end))
                assert parameters == ["precipitation", "temperature"]
                row = make_raw_historical_forcing(
                    station_id=station.id,
                    source=GATEWAY_HISTORY_SOURCE,
                    parameter="temperature",
                    valid_time=start,
                    spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                )
                changes = {
                    "source": {"source": "forecast_fill"},
                    "station": {"station_id": uuid4()},
                    "end": {"valid_time": end},
                    "nan": {"value": float("nan")},
                }
                return [replace(row, **changes[invalid]) if invalid else row]

        if invalid:
            with pytest.raises(ConfigurationError, match="history contract"):
                module.ingest_recap_era5_reanalysis_flow.fn(
                    cast("Connection", None), IDENTITY, Adapter(), WINDOW, now=START
                )
            assert (
                store.fetch_forcing(
                    station.id, GATEWAY_HISTORY_SOURCE, START, WINDOW.end
                )
                == []
            )
        else:
            report = module.ingest_recap_era5_reanalysis_flow.fn(
                cast("Connection", None), IDENTITY, Adapter(), WINDOW, now=START
            )
            assert [int((end - start).days) for start, end in windows] == [31, 31, 8]
            assert [(r.parameter, r.rows) for r in report] == [
                ("precipitation", 0),
                ("temperature", 3),
            ]


@dataclass
class WeatherFixture:
    stations: list[StationConfig]
    station_store: FakeStationStore
    group: StationGroup
    group_store: FakeStationGroupStore
    polygons: dict[StationId, list[GatewayPolygonBindingRow]]
    scope: OperationalWeatherScope
    forcing: FakeHistoricalForcingStore
    calls: list[tuple[str, str]]
    adapter: RecapGatewayReanalysisAdapter


@pytest.fixture
def operational_weather(monkeypatch: pytest.MonkeyPatch) -> WeatherFixture:
    tenant_id = TenantId(uuid4())
    tenants = FakeTenantStore()
    tenants.ensure_tenant(tenant_id=tenant_id, code="chwrr", name="CHWRR")
    stations = [
        make_station_config(
            station_id=StationId(uuid4()),
            basin_id=BasinId(uuid4()),
            code=str(index),
            tenant_id=tenant_id,
            network="dhm",
            station_status=StationStatus.ONBOARDING
            if index % 2
            else StationStatus.OPERATIONAL,
        )
        for index in range(21)
    ]
    station_store = FakeStationStore()
    for station in stations:
        station_store.store_station(station)
        station_store.store_weather_source(history_binding(station.id))
    group = StationGroup(
        id=StationGroupId(uuid4()),
        name="fixture",
        tenant_id=tenant_id,
        station_ids=frozenset(s.id for s in stations),
        created_at=START,
    )
    groups = FakeStationGroupStore()
    groups.store_group(group)
    scope = OperationalWeatherScope(
        group_id=group.id,
        gateway_hru_name=GatewayHruName("nepal21_20261012"),
        station_polygons=tuple(
            WeatherStationPolygon(station_id=s.id, polygon_name=f"g_{s.code}")
            for s in stations
        ),
    )
    polygons = {
        s.id: [
            GatewayPolygonBindingRow(
                station_id=s.id,
                basin_id=cast("BasinId", s.basin_id),
                gateway_hru_name=scope.gateway_hru_name,
                name=f"g_{s.code}",
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
            )
        ]
        for s in stations
    }

    class PolygonStore:
        def fetch_bindings_for_station(
            self, station_id: StationId
        ) -> list[GatewayPolygonBindingRow]:
            return polygons.get(station_id, [])

    calls: list[tuple[str, str]] = []

    class Ecmwf:
        def era5_land_reanalysis(
            self,
            *,
            variable: str,
            hru_code: str,
            start_date: object,
            end_date: object,
            **kwargs: object,
        ) -> pd.DataFrame:
            calls.append((hru_code, variable))
            return pd.DataFrame(
                {
                    **{p.polygon_name: [1.0] for p in scope.station_polygons},
                    "source": ["era5_land"],
                    "source_run": [pd.Timestamp(START)],
                },
                index=pd.DatetimeIndex([START], name="time"),
            )

    adapter = RecapGatewayReanalysisAdapter(
        client=cast("RecapClientLike", SimpleNamespace(ecmwf=Ecmwf())),
        resolver=StoreBackedGatewayPolygonResolver(PolygonStore()),
    )
    forcing = FakeHistoricalForcingStore()
    monkeypatch.setattr(service, "PgTenantStore", injected(tenants))
    monkeypatch.setattr(service, "PgStationGroupStore", injected(groups))
    monkeypatch.setattr(service, "PgStationStore", injected(station_store))
    monkeypatch.setattr(service, "RecapGatewayPolygonStore", injected(PolygonStore()))
    monkeypatch.setattr(module, "PgHistoricalForcingStore", injected(forcing))
    return WeatherFixture(
        stations, station_store, group, groups, polygons, scope, forcing, calls, adapter
    )


def run_scoped(
    fixture: WeatherFixture, identity: DeploymentIdentityConfig = IDENTITY
) -> tuple[HistoryCoverage, ...]:
    return module.ingest_recap_era5_reanalysis_flow.fn(
        cast("Connection", None),
        identity,
        fixture.adapter,
        HistoryWindow(start=START, end=ensure_utc(START + timedelta(days=1))),
        now=START,
        scope=fixture.scope,
    )


class TestOperationalWeatherScopeFlow:
    def test_native_adapter_uses_persisted_alias_for_mixed_status_group(
        self, operational_weather: WeatherFixture
    ) -> None:
        f = operational_weather
        report = run_scoped(f)
        assert f.calls == [
            ("nepal21_20261012", "total_precipitation"),
            ("nepal21_20261012", "2m_temperature"),
        ]
        assert len(report) == 42
        assert all(r.rows == 1 for r in report)
        assert [f.station_store.fetch_station(s.id) for s in f.stations] == f.stations
        assert f.group_store.fetch_group(f.group.id) == f.group
        assert all(
            f.station_store.fetch_weather_sources(s.id) == [history_binding(s.id)]
            for s in f.stations
        )

    @pytest.mark.parametrize(
        "fault",
        [
            "group",
            "member",
            "group_tenant",
            "station_tenant",
            "status",
            "hru",
            "polygon",
            "basin",
            "weather_source",
        ],
    )
    def test_admission_rejects_before_any_provider_call(
        self, operational_weather: WeatherFixture, fault: str
    ) -> None:
        f = operational_weather
        station = f.stations[0]
        if fault == "group":
            f.scope = replace(f.scope, group_id=StationGroupId(uuid4()))
        elif fault == "member":
            f.group_store.store_group(
                replace(f.group, station_ids=f.group.station_ids - {station.id})
            )
        elif fault == "group_tenant":
            f.group_store.store_group(replace(f.group, tenant_id=TenantId(uuid4())))
        elif fault == "station_tenant":
            f.station_store.store_station(replace(station, tenant_id=TenantId(uuid4())))
        elif fault == "status":
            f.station_store.store_station(
                replace(station, station_status=StationStatus.SUSPENDED)
            )
        elif fault == "weather_source":
            f.station_store.store_weather_source(
                replace(
                    history_binding(station.id), status=WeatherSourceStatus.INACTIVE
                )
            )
        else:
            field, value = {
                "hru": ("gateway_hru_name", "nepal_dhm_v1"),
                "polygon": ("name", "wrong"),
                "basin": ("basin_id", BasinId(uuid4())),
            }[fault]
            f.polygons[station.id] = [
                replace(f.polygons[station.id][0], **{field: value})
            ]
        with pytest.raises(
            (ConfigurationError, TenantIsolationError),
            match="group|tenant|weather|reanalysis|isolation",
        ):
            run_scoped(f)
        assert f.calls == []

    def test_global_admin_is_not_chwrr_authority(
        self, operational_weather: WeatherFixture
    ) -> None:
        with pytest.raises(ConfigurationError, match="CHWRR-only"):
            run_scoped(
                operational_weather,
                DeploymentIdentityConfig(
                    global_admin=True, writable_tenants=frozenset()
                ),
            )
        assert operational_weather.calls == []

    def test_unscoped_path_uses_legacy_six_station_resolver(
        self, operational_weather: WeatherFixture
    ) -> None:
        # No automatic switch from the legacy resolver to operational admission.
        with pytest.raises(ConfigurationError, match="missing DHM station"):
            module.ingest_recap_era5_reanalysis_flow.fn(
                cast("Connection", None),
                IDENTITY,
                operational_weather.adapter,
                WINDOW,
                now=START,
            )
        assert operational_weather.calls == []

    def test_active_model_assignment_does_not_block_history_ingest(
        self, operational_weather: WeatherFixture
    ) -> None:
        from sapphire_flow.types.enums import ModelAssignmentStatus
        from sapphire_flow.types.ids import ModelId
        from sapphire_flow.types.station import ModelAssignment

        f = operational_weather
        assignment = ModelAssignment(
            station_id=f.stations[0].id,
            model_id=ModelId("fixture-model"),
            time_step=timedelta(days=1),
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=START,
        )
        f.station_store.store_model_assignment(assignment)
        run_scoped(f)
        assert f.station_store.fetch_model_assignments(f.stations[0].id) == [assignment]
