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
    from pathlib import Path

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


class TestProductionEra5:
    def test_real_deployment_has_only_required_strings(self) -> None:
        from sapphire_flow.cli.register_deployments import _all_specs

        deployment = module.ingest_operational_era5_history_flow.to_deployment(
            name="ingest-operational-era5-history",
            work_pool_name="ingest",
            concurrency_limit=1,
        )
        assert deployment.enforce_parameter_schema
        schema = module.ingest_operational_era5_history_flow.parameters.model_dump()
        assert set(schema["required"]) == {"start", "end", "weather_scope"}
        assert {name: item["type"] for name, item in schema["properties"].items()} == {
            "start": "string",
            "end": "string",
            "weather_scope": "string",
        }
        spec = next(
            s
            for s in _all_specs()
            if s.deployment_name == "ingest-operational-era5-history"
        )
        assert (spec.cron, spec.concurrency_limit, spec.work_pool_name) == (
            None,
            1,
            "ingest",
        )

    @pytest.mark.parametrize("failure", ["none", "admission", "second_batch"])
    def test_transaction_and_resource_lifecycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        import recap_client
        import sqlalchemy as sa

        from sapphire_flow.config import (
            deployment_identity,
            nepal_weather,
            recap_gateway,
        )
        from sapphire_flow.db import engine as engine_module

        engine = sa.create_engine(f"sqlite:///{tmp_path / 'production.db'}")
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE batches (ordinal INTEGER)"))
        monkeypatch.setattr(engine_module, "create_engine_from_env", lambda: engine)
        monkeypatch.setattr(
            deployment_identity, "load_deployment_identity_config", lambda _: IDENTITY
        )
        monkeypatch.setattr(
            nepal_weather,
            "load_weather_scope",
            lambda *a: SimpleNamespace(scope=object()),
        )
        monkeypatch.setattr(
            recap_gateway, "load_recap_gateway_config", lambda _: object()
        )
        monkeypatch.setattr(recap_gateway, "load_recap_api_key", lambda: "test")
        monkeypatch.setattr(
            recap_gateway, "build_recap_client_config", lambda **kw: object()
        )
        sessions = []

        class Session:
            closed = False

            def close(self) -> None:
                self.closed = True

        def client(config: object) -> SimpleNamespace:
            session = Session()
            sessions.append(session)
            return SimpleNamespace(
                ecmwf=SimpleNamespace(_http=SimpleNamespace(_session=session))
            )

        monkeypatch.setattr(recap_client, "RecapClient", client)

        def admit(*args: object, **kwargs: object) -> None:
            if failure == "admission":
                raise ConfigurationError("inadmissible roster")

        monkeypatch.setattr(module, "operational_weather_stations", admit)
        calls = 0
        logged: list[int] = []

        def record_coverage(event: str, **fields: object) -> None:
            assert event == "nepal_onboarding.history"
            with engine.connect() as observer:
                committed = observer.scalar(sa.text("SELECT count(*) FROM batches"))
            assert committed == calls
            logged.append(committed)

        monkeypatch.setattr(module, "log", SimpleNamespace(info=record_coverage))

        def ingest(
            conn: Connection, *args: object, **kwargs: object
        ) -> tuple[HistoryCoverage, ...]:
            nonlocal calls
            calls += 1
            conn.execute(
                sa.text("INSERT INTO batches VALUES (:ordinal)"), {"ordinal": calls}
            )
            if failure == "second_batch" and calls == 2:
                raise ConfigurationError("provider failure")
            return (
                HistoryCoverage(
                    station_id=StationId(uuid4()),
                    code="fixture",
                    parameter="temperature",
                    rows=1,
                    start=START,
                    end=START,
                ),
            )

        monkeypatch.setattr(module, "_ingest_history", ingest)
        if failure == "none":
            module.ingest_operational_era5_history_flow.fn(
                "2020-01-01T00:00:00Z", "2020-03-11T00:00:00Z", "test"
            )
        else:
            with pytest.raises(
                ConfigurationError, match="Operational ERA5 history failed"
            ):
                module.ingest_operational_era5_history_flow.fn(
                    "2020-01-01T00:00:00Z", "2020-03-11T00:00:00Z", "test"
                )
        with engine.connect() as conn:
            assert (
                conn.scalar(sa.text("SELECT count(*) FROM batches"))
                == {"none": 3, "admission": 0, "second_batch": 1}[failure]
            )
        assert (
            logged == {"none": [1, 2, 3], "admission": [], "second_batch": [1]}[failure]
        )
        assert all(session.closed for session in sessions)
        if failure == "admission":
            assert not sessions
        engine.dispose()

    def test_sdk_secret_never_reaches_prefect_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import traceback

        secret = "synthetic-only-secret-7293"

        def fail(*args: object) -> tuple[HistoryCoverage, ...]:
            try:
                raise ValueError(f"request https://example.invalid?api_key={secret}")
            except ValueError as exc:
                raise RuntimeError(f"provider rejected {secret}") from exc

        monkeypatch.setattr(module, "_run_operational_history", fail)
        with pytest.raises(
            ConfigurationError, match="Operational ERA5 history failed"
        ) as captured:
            module.ingest_operational_era5_history_flow.fn("start", "end", "scope")
        error = captured.value
        assert error.__context__ is None
        assert error.__cause__ is None
        rendered = "".join(traceback.format_exception(error))
        assert secret not in rendered
        frames = traceback.extract_tb(error.__traceback__)
        assert not any(frame.name == "fail" for frame in frames)

    def test_pinned_sdk_session_cleanup_path_without_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import requests
        from recap_client import ApiClientConfig, RecapClient

        closed: list[requests.Session] = []

        def close(session: requests.Session) -> None:
            closed.append(session)

        def forbidden(*args: object, **kwargs: object) -> None:
            pytest.fail("lifecycle check must not issue HTTP")

        monkeypatch.setattr(requests.Session, "close", close)
        monkeypatch.setattr(requests.Session, "request", forbidden)
        client = RecapClient(
            ApiClientConfig(base_url="https://example.invalid", api_key="synthetic")
        )
        client.ecmwf._http._session.close()
        assert len(closed) == 1
