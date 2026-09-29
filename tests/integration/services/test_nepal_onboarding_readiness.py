from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.cli.import_dhm_delivery import bootstrap_tenant
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.flows.ingest_recap_era5_reanalysis import (
    ingest_recap_era5_reanalysis_flow,
)
from sapphire_flow.services.basin_package_loader import load_basin_package
from sapphire_flow.services.nepal_onboarding import (
    history_binding,
    import_nepal_package,
    qualify_discharge_targets,
)
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.enums import (
    ObservationSource,
    QcStatus,
    SpatialRepresentation,
    StationStatus,
)
from sapphire_flow.types.historical_forcing import RawHistoricalForcing
from sapphire_flow.types.nepal_onboarding import (
    GATEWAY_HISTORY_SOURCE,
    GATEWAY_HRU,
    GAUGE_POLYGONS,
    ReadinessStatus,
    TrainingWindow,
)
from tests.conftest import make_observation, make_station_config
from tests.fakes.fake_models import FakeStationForecastModel

if TYPE_CHECKING:
    from sapphire_flow.types.basin_package import LoadedBasinPackage
    from sapphire_flow.types.station import StationConfig, StationWeatherSource

NOW = ensure_utc(datetime(2026, 9, 29, tzinfo=UTC))
START = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
IDENTITY = DeploymentIdentityConfig(
    writable_tenants=frozenset({"chwrr"}), global_admin=False
)
WINDOW = TrainingWindow(
    start=START,
    end=ensure_utc(START + timedelta(days=10)),
    time_step=timedelta(days=1),
    minimum_samples=2,
)


def model() -> FakeStationForecastModel:
    result = FakeStationForecastModel()
    result.data_requirements = replace(
        result.data_requirements,
        lookback_steps=2,
        forecast_horizon_steps=1,
        spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
    )
    return result


@pytest.fixture
def cohort(db_connection: sa.Connection) -> list[StationConfig]:
    tenant = bootstrap_tenant(
        PgTenantStore(db_connection),
        DeploymentIdentityConfig(writable_tenants=frozenset(), global_admin=True),
        tenant_code="chwrr",
        now=NOW,
    )
    stations = [
        make_station_config(
            code=code,
            network="dhm",
            station_id=uuid4(),
            tenant_id=tenant,
            station_status=StationStatus.ONBOARDING,
            forecast_targets=None,
        )
        for code, _ in GAUGE_POLYGONS
    ]
    for station in stations:
        PgStationStore(db_connection).store_station(station)
    return stations


def package() -> LoadedBasinPackage:
    base = load_basin_package(Path("tests/fixtures/basin_static/nepal-dhm-basins"))
    basins = tuple(
        replace(
            base.basins[0],
            station_code=code,
            basin_code=code,
            gauge_id=code,
            gateway_hru_name=GATEWAY_HRU,
            name=polygon,
        )
        for code, polygon in GAUGE_POLYGONS
    )
    checks = tuple(
        replace(
            base.validation_report.basins[0],
            station_code=code,
            basin_code=code,
            gateway_hru_name=GATEWAY_HRU,
            name=polygon,
        )
        for code, polygon in GAUGE_POLYGONS
    )
    attrs = next(iter(base.static_attributes.values()))
    return replace(
        base,
        manifest=replace(
            base.manifest,
            package_id=f"synthetic-nepal-{uuid4()}",
            gateway_hru_names=frozenset({GATEWAY_HRU}),
        ),
        basins=basins,
        bands=None,
        validation_report=replace(base.validation_report, passed=6, basins=checks),
        static_attributes={code: attrs for code, _ in GAUGE_POLYGONS},
    )


def seed_history(
    conn: sa.Connection,
    stations: list[StationConfig],
    status: QcStatus = QcStatus.QC_PASSED,
) -> None:
    observations = [
        replace(
            make_observation(
                station_id=s.id,
                timestamp=ensure_utc(START + timedelta(days=i)),
                qc_status=status,
            ),
            id=uuid4(),
            source=ObservationSource.MANUAL_IMPORT,
            delivery_id=DELIVERY_ID,
            qc_rule_version="1.2",
        )
        for s in stations
        for i in range(10)
    ]
    PgObservationStore(conn).store_observations(observations)
    forcing = [
        RawHistoricalForcing(
            station_id=s.id,
            source=GATEWAY_HISTORY_SOURCE,
            version="synthetic-v1",
            valid_time=ensure_utc(START + timedelta(days=i)),
            parameter=p,
            spatial_type=SpatialRepresentation.BASIN_AVERAGE,
            band_id=None,
            member_id=None,
            value=10.0,
        )
        for s in stations
        for i in range(10)
        for p in ("precipitation", "temperature")
    ]
    PgHistoricalForcingStore(conn).store_forcing(forcing)


class TestNepalOnboarding:
    @pytest.mark.parametrize("missing", ["source", "polygon"])
    def test_requalification_clears_target_after_binding_loss(
        self, db_connection: sa.Connection, cohort: list[StationConfig], missing: str
    ) -> None:
        from sapphire_flow.db.metadata import (
            recap_gateway_polygon_bindings,
            station_weather_sources,
        )

        import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)
        seed_history(db_connection, cohort)
        qualify_discharge_targets(db_connection, IDENTITY, model(), WINDOW, now=NOW)
        table = (
            station_weather_sources
            if missing == "source"
            else recap_gateway_polygon_bindings
        )
        db_connection.execute(
            sa.delete(table).where(table.c.station_id == cohort[0].id)
        )
        reports = qualify_discharge_targets(
            db_connection, IDENTITY, model(), WINDOW, now=NOW
        )
        assert len(reports) == 6
        assert reports[0].status is ReadinessStatus.HELD
        assert any("binding" in r or "mapping" in r for r in reports[0].reasons)
        assert (
            PgStationStore(db_connection).fetch_station(cohort[0].id).forecast_targets
            is None
        )
        assert all(r.status is ReadinessStatus.READY for r in reports[1:])

    @pytest.mark.parametrize("defect", ["missing", "duplicate", "polygon", "hru"])
    def test_invalid_package_is_refused_before_writes(
        self, db_connection: sa.Connection, cohort: list[StationConfig], defect: str
    ) -> None:
        loaded = package()
        variants = {
            "missing": loaded.basins[:-1],
            "duplicate": (*loaded.basins[:-1], loaded.basins[0]),
            "polygon": (replace(loaded.basins[0], name="wrong"), *loaded.basins[1:]),
            "hru": (
                replace(loaded.basins[0], gateway_hru_name="wrong"),
                *loaded.basins[1:],
            ),
        }
        with pytest.raises(ConfigurationError, match="six DHM gauges|mapping"):
            import_nepal_package(
                db_connection,
                replace(loaded, basins=variants[defect]),
                IDENTITY,
                clock=lambda: NOW,
            )
        assert all(
            PgStationStore(db_connection).fetch_station(s.id).basin_id is None
            for s in cohort
        )

    def test_conflicting_source_binding_is_preserved_and_refused(
        self, db_connection: sa.Connection, cohort: list[StationConfig]
    ) -> None:
        from sapphire_flow.types.enums import WeatherSourceStatus

        store = PgStationStore(db_connection)
        source = replace(
            history_binding(cohort[0].id), status=WeatherSourceStatus.INACTIVE
        )
        store.store_weather_source(source)
        with pytest.raises(ConfigurationError, match="conflicting reanalysis"):
            import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)
        assert store.fetch_weather_sources(cohort[0].id) == [source]
        assert store.fetch_station(cohort[0].id).basin_id is None

    @pytest.mark.parametrize(
        "identity",
        [
            DeploymentIdentityConfig(writable_tenants=frozenset(), global_admin=True),
            DeploymentIdentityConfig(
                writable_tenants=frozenset({"sapphire"}), global_admin=False
            ),
        ],
    )
    def test_requires_chwrr_only_authority(
        self,
        db_connection: sa.Connection,
        cohort: list[StationConfig],
        identity: DeploymentIdentityConfig,
    ) -> None:
        with pytest.raises(ConfigurationError, match="CHWRR-only"):
            import_nepal_package(db_connection, package(), identity, clock=lambda: NOW)

    def test_qualification_waits_for_delivery_tenant_lock(
        self, db_engine: sa.Engine
    ) -> None:
        from sapphire_flow.db.metadata import tenants

        with db_engine.begin() as setup:
            tenant_id = bootstrap_tenant(
                PgTenantStore(setup),
                DeploymentIdentityConfig(
                    writable_tenants=frozenset(), global_admin=True
                ),
                tenant_code="chwrr",
                now=NOW,
            )
        try:
            with db_engine.connect() as delivery, db_engine.connect() as readiness:
                delivery_tx = delivery.begin()
                # The same row lock acquired by delivery replacement and QC.
                PgTenantStore(delivery).lock_tenant(tenant_id)
                readiness.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(sa.exc.OperationalError, match="lock timeout"):
                    qualify_discharge_targets(
                        readiness, IDENTITY, model(), WINDOW, now=NOW
                    )
                readiness.rollback()
                delivery_tx.rollback()
                with pytest.raises(ConfigurationError, match="missing DHM station"):
                    qualify_discharge_targets(
                        readiness, IDENTITY, model(), WINDOW, now=NOW
                    )
                readiness.rollback()
        finally:
            with db_engine.begin() as cleanup:
                cleanup.execute(sa.delete(tenants).where(tenants.c.id == tenant_id))

    @pytest.mark.parametrize("missing", ["static", "radiation", "spatial"])
    def test_unmet_model_requirements_hold_all_stations(
        self, db_connection: sa.Connection, cohort: list[StationConfig], missing: str
    ) -> None:
        import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)
        seed_history(db_connection, cohort)
        selected = model()
        changes = {
            "static": {"static_features": frozenset({"absent_attribute"})},
            "radiation": {"past_dynamic_features": frozenset({"global_radiation"})},
            "spatial": {"spatial_input_type": SpatialRepresentation.POINT},
        }[missing]
        selected.data_requirements = replace(selected.data_requirements, **changes)
        reports = qualify_discharge_targets(
            db_connection, IDENTITY, selected, WINDOW, now=NOW
        )
        assert all(r.status is ReadinessStatus.HELD and r.reasons for r in reports)

    @pytest.mark.parametrize("change", ["delivery", "qc_version", "short"])
    def test_unqualified_rows_cannot_complete_training_windows(
        self, db_connection: sa.Connection, cohort: list[StationConfig], change: str
    ) -> None:
        from sapphire_flow.db.metadata import observations

        import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)
        seed_history(db_connection, cohort)
        values = {
            "delivery": {"delivery_id": "other-delivery"},
            "qc_version": {"qc_rule_version": "obsolete"},
            "short": {"qc_status": QcStatus.QC_FAILED.value},
        }[change]
        db_connection.execute(
            sa.update(observations)
            .where(observations.c.timestamp >= START + timedelta(days=2))
            .values(**values)
        )
        reports = qualify_discharge_targets(
            db_connection, IDENTITY, model(), WINDOW, now=NOW
        )
        assert all(r.status is ReadinessStatus.HELD for r in reports)
        assert all(r.complete_samples == 0 for r in reports)

    def test_target_failure_rolls_back_all_six(
        self,
        db_connection: sa.Connection,
        cohort: list[StationConfig],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)
        seed_history(db_connection, cohort)
        original = PgStationStore.update_station
        calls = 0

        def fail(store: PgStationStore, station: StationConfig) -> None:
            nonlocal calls
            original(store, station)
            calls += 1
            if calls == 3:
                raise RuntimeError("injected target failure")

        monkeypatch.setattr(PgStationStore, "update_station", fail)
        with pytest.raises(RuntimeError, match="injected target failure"):
            qualify_discharge_targets(db_connection, IDENTITY, model(), WINDOW, now=NOW)
        assert all(
            PgStationStore(db_connection).fetch_station(s.id).forecast_targets is None
            for s in cohort
        )

    def test_rollback_dry_run_leaves_no_basins_or_sources(
        self, db_connection: sa.Connection, cohort: list[StationConfig]
    ) -> None:
        with db_connection.begin_nested() as dry_run:
            import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)
            dry_run.rollback()
        assert all(
            PgStationStore(db_connection).fetch_station(s.id).basin_id is None
            and not PgStationStore(db_connection).fetch_weather_sources(s.id)
            for s in cohort
        )

    def test_import_readiness_and_repeat_preserve_observations(
        self, db_connection: sa.Connection, cohort: list[StationConfig]
    ) -> None:
        loaded = package()
        import_nepal_package(db_connection, loaded, IDENTITY, clock=lambda: NOW)
        import_nepal_package(db_connection, loaded, IDENTITY, clock=lambda: NOW)
        seed_history(db_connection, cohort)
        obs = PgObservationStore(db_connection)
        before = obs.fetch_delivery_observations(DELIVERY_ID, [s.id for s in cohort])
        reports = qualify_discharge_targets(
            db_connection, IDENTITY, model(), WINDOW, now=NOW
        )
        assert len(reports) == 6
        assert all(
            r.status is ReadinessStatus.READY and r.complete_samples == 8
            for r in reports
        )
        store = PgStationStore(db_connection)
        assert all(
            store.fetch_station(s.id).forecast_targets == frozenset({"discharge"})
            for s in cohort
        )
        assert all(
            store.fetch_station(s.id).station_status is StationStatus.ONBOARDING
            for s in cohort
        )
        assert (
            obs.fetch_delivery_observations(DELIVERY_ID, [s.id for s in cohort])
            == before
        )

    @pytest.mark.parametrize(
        "status",
        [QcStatus.RAW, QcStatus.QC_UNCHECKED, QcStatus.QC_FAILED, QcStatus.QC_SUSPECT],
    )
    def test_unusable_history_clears_target(
        self,
        db_connection: sa.Connection,
        cohort: list[StationConfig],
        status: QcStatus,
    ) -> None:
        import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)
        seed_history(db_connection, cohort, status)
        store = PgStationStore(db_connection)
        station = store.fetch_station(cohort[0].id)
        store.update_station(
            replace(station, forecast_targets=frozenset({"discharge"}))
        )
        reports = qualify_discharge_targets(
            db_connection, IDENTITY, model(), WINDOW, now=NOW
        )
        assert all(r.status is ReadinessStatus.HELD for r in reports)
        assert store.fetch_station(cohort[0].id).forecast_targets is None

    def test_missing_forcing_cannot_borrow_another_station_history(
        self, db_connection: sa.Connection, cohort: list[StationConfig]
    ) -> None:
        import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)
        seed_history(db_connection, cohort)
        from sapphire_flow.db.metadata import historical_forcing

        db_connection.execute(
            sa.delete(historical_forcing).where(
                historical_forcing.c.station_id == cohort[0].id
            )
        )
        reports = qualify_discharge_targets(
            db_connection, IDENTITY, model(), WINDOW, now=NOW
        )
        assert reports[0].status is ReadinessStatus.HELD
        assert all(r.status is ReadinessStatus.READY for r in reports[1:])

    def test_import_failure_rolls_back_all_bindings(
        self,
        db_connection: sa.Connection,
        cohort: list[StationConfig],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original = PgStationStore.store_weather_source
        calls = 0

        def fail(store: PgStationStore, source: StationWeatherSource) -> None:
            nonlocal calls
            calls += 1
            original(store, source)
            if calls == 3:
                raise RuntimeError("injected binding failure")

        monkeypatch.setattr(PgStationStore, "store_weather_source", fail)
        with pytest.raises(RuntimeError, match="injected binding failure"):
            import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)
        store = PgStationStore(db_connection)
        assert all(
            store.fetch_station(s.id).basin_id is None
            and store.fetch_reanalysis_bindings(s.id) == []
            for s in cohort
        )

    @pytest.mark.parametrize("change", ["status", "target", "tenant"])
    def test_refuses_unexpected_station_state(
        self, db_connection: sa.Connection, cohort: list[StationConfig], change: str
    ) -> None:
        from sapphire_flow.db.metadata import stations
        from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

        values = {
            "status": {"station_status": "operational"},
            "target": {"forecast_targets": ["water_level"]},
            "tenant": {"tenant_id": DEFAULT_TENANT_ID},
        }[change]
        db_connection.execute(
            sa.update(stations).where(stations.c.id == cohort[0].id).values(**values)
        )
        with pytest.raises(
            (ConfigurationError, TenantIsolationError),
            match="onboarding|target|authorized",
        ):
            import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)

    def test_history_import_is_bounded_and_idempotent(
        self, db_connection: sa.Connection, cohort: list[StationConfig]
    ) -> None:
        import_nepal_package(db_connection, package(), IDENTITY, clock=lambda: NOW)

        class Adapter:
            def fetch_reanalysis(
                self,
                station_configs: list[StationWeatherSource],
                start: UtcDatetime,
                end: UtcDatetime,
                parameters: list[str],
            ) -> list[RawHistoricalForcing]:
                assert end - start <= timedelta(days=31)
                return [
                    RawHistoricalForcing(
                        station_id=b.station_id,
                        source=GATEWAY_HISTORY_SOURCE,
                        version="synthetic-v1",
                        valid_time=start,
                        parameter=p,
                        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                        band_id=None,
                        member_id=None,
                        value=10.0,
                    )
                    for b in station_configs
                    for p in parameters
                ]

        first = ingest_recap_era5_reanalysis_flow.fn(
            db_connection, IDENTITY, Adapter(), WINDOW, now=NOW
        )
        second = ingest_recap_era5_reanalysis_flow.fn(
            db_connection, IDENTITY, Adapter(), WINDOW, now=NOW
        )
        assert first == second
        assert len(first) == 12
        assert all(item.rows == 1 for item in first)
