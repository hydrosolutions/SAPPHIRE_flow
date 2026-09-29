from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.adapters.dhm_files import parse_daily_flow, parse_rating_tables
from sapphire_flow.adapters.nepal_local_day import nepal_day_start
from sapphire_flow.cli.import_dhm_delivery import (
    DELIVERY_ID,
    bootstrap_tenant,
    load_station_metadata,
    register_stations,
    replace_delivery,
    run_delivery_qc,
)
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.db.metadata import audit_log as audit_log_table
from sapphire_flow.db.metadata import forecasts as forecasts_table
from sapphire_flow.db.metadata import models as models_table
from sapphire_flow.db.metadata import observations as observations_table
from sapphire_flow.db.metadata import rating_curves as rating_curves_table
from sapphire_flow.db.metadata import stations as stations_table
from sapphire_flow.db.metadata import tenants as tenants_table
from sapphire_flow.exceptions import ConfigurationError, DeliveryCurveDependencyError
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId, TenantId
from sapphire_flow.types.observation import RawObservation
from tests.conftest import make_station_config

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/dhm"
_NOW = ensure_utc(datetime(2026, 9, 28, tzinfo=UTC))
_ADMIN = DeploymentIdentityConfig(writable_tenants=frozenset(), global_admin=True)
_CHWRR = DeploymentIdentityConfig(
    writable_tenants=frozenset({"chwrr"}), global_admin=False
)


class _FailingObservationStore(PgObservationStore):
    def store_raw_observations(
        self, observations: list[RawObservation]
    ) -> list[ObservationId]:
        raise RuntimeError("injected observation insert failure")


class _FailingQcStore(PgObservationStore):
    def __init__(self, conn: sa.Connection) -> None:
        super().__init__(conn)
        self.updated = 0

    def update_delivery_qc(
        self,
        observation_id: ObservationId,
        delivery_id: str,
        qc_status: QcStatus,
        qc_flags: list[QcFlag],
        qc_rule_version: str | None,
    ) -> bool:
        updated = super().update_delivery_qc(
            observation_id, delivery_id, qc_status, qc_flags, qc_rule_version
        )
        self.updated += 1
        if self.updated == 3:
            raise RuntimeError("injected QC update failure")
        return updated


class _RacingObservationStore(PgObservationStore):
    def __init__(self, conn: sa.Connection) -> None:
        super().__init__(conn)
        self.injected = False

    def _assert_delivery_collisions(
        self,
        entries: list[tuple[tuple[StationId, UtcDatetime, str, str], str | None]],
    ) -> None:
        super()._assert_delivery_collisions(entries)
        if self.injected:
            return
        station_id, timestamp, parameter, source = entries[0][0]
        self._conn.execute(
            sa.insert(observations_table).values(
                id=uuid4(),
                station_id=station_id,
                timestamp=timestamp,
                parameter=parameter,
                value=9.0,
                source=source,
                delivery_id="unrelated-delivery",
            )
        )
        self.injected = True


def test_failed_replacement_rolls_back_curves_and_observations(
    db_connection: sa.Connection,
) -> None:
    tenant_store = PgTenantStore(db_connection)
    station_store = PgStationStore(db_connection)
    curve_store = PgRatingCurveStore(db_connection)
    obs_store = PgObservationStore(db_connection)
    bootstrap_tenant(tenant_store, _ADMIN, tenant_code="chwrr", now=_NOW)
    metadata = load_station_metadata(_FIXTURES / "stations.toml")
    register_stations(
        tenant_store,
        station_store,
        _CHWRR,
        metadata,
        audit_log_store=PgAuditLogStore(db_connection),
        now=_NOW,
    )
    daily = parse_daily_flow((_FIXTURES / "synthetic_daily_flow.txt").read_text())
    rating = parse_rating_tables(
        (_FIXTURES / "synthetic_rating_tables.txt").read_text()
    )
    files = {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations
    }
    assert replace_delivery(
        tenant_store,
        station_store,
        curve_store,
        obs_store,
        _CHWRR,
        files,
        audit_log_store=PgAuditLogStore(db_connection),
        now=_NOW,
    ) == (12, 18)
    station = station_store.fetch_station_by_code("447", "dhm")
    assert station is not None
    original_curves = curve_store.fetch_delivery_curves(DELIVERY_ID, [station.id])
    original_rows = obs_store.fetch_delivery_observations(DELIVERY_ID, [station.id])

    def shifted(day: date) -> UtcDatetime:
        return ensure_utc(nepal_day_start(day) + timedelta(hours=1))

    nested = db_connection.begin_nested()
    with pytest.raises(RuntimeError, match="injected"):
        replace_delivery(
            tenant_store,
            station_store,
            curve_store,
            _FailingObservationStore(db_connection),
            _CHWRR,
            files,
            audit_log_store=PgAuditLogStore(db_connection),
            now=_NOW,
            day_start=shifted,
        )
    nested.rollback()
    assert (
        curve_store.fetch_delivery_curves(DELIVERY_ID, [station.id]) == original_curves
    )
    assert (
        obs_store.fetch_delivery_observations(DELIVERY_ID, [station.id])
        == original_rows
    )


def test_post_preflight_collision_rolls_back_replacement(
    db_connection: sa.Connection,
) -> None:
    tenants = PgTenantStore(db_connection)
    stations = PgStationStore(db_connection)
    curves = PgRatingCurveStore(db_connection)
    observations = PgObservationStore(db_connection)
    bootstrap_tenant(tenants, _ADMIN, tenant_code="chwrr", now=_NOW)
    metadata = load_station_metadata(_FIXTURES / "stations.toml")
    register_stations(
        tenants,
        stations,
        _CHWRR,
        metadata,
        audit_log_store=PgAuditLogStore(db_connection),
        now=_NOW,
    )
    daily = parse_daily_flow((_FIXTURES / "synthetic_daily_flow.txt").read_text())
    rating = parse_rating_tables(
        (_FIXTURES / "synthetic_rating_tables.txt").read_text()
    )
    files = {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations
    }
    replace_delivery(
        tenants,
        stations,
        curves,
        observations,
        _CHWRR,
        files,
        audit_log_store=PgAuditLogStore(db_connection),
        now=_NOW,
    )
    station = stations.fetch_station_by_code("447", "dhm")
    assert station is not None
    original_curves = curves.fetch_delivery_curves(DELIVERY_ID, [station.id])
    original_rows = observations.fetch_delivery_observations(DELIVERY_ID, [station.id])

    nested = db_connection.begin_nested()
    with pytest.raises(ConfigurationError, match="read-back differs"):
        replace_delivery(
            tenants,
            stations,
            curves,
            _RacingObservationStore(db_connection),
            _CHWRR,
            files,
            audit_log_store=PgAuditLogStore(db_connection),
            now=_NOW,
        )
    nested.rollback()

    assert curves.fetch_delivery_curves(DELIVERY_ID, [station.id]) == original_curves
    assert (
        observations.fetch_delivery_observations(DELIVERY_ID, [station.id])
        == original_rows
    )


def test_forecast_reference_blocks_replacement_without_partial_delete(
    db_connection: sa.Connection,
) -> None:
    tenants = PgTenantStore(db_connection)
    stations = PgStationStore(db_connection)
    curves = PgRatingCurveStore(db_connection)
    observations = PgObservationStore(db_connection)
    bootstrap_tenant(tenants, _ADMIN, tenant_code="chwrr", now=_NOW)
    metadata = load_station_metadata(_FIXTURES / "stations.toml")
    register_stations(
        tenants,
        stations,
        _CHWRR,
        metadata,
        audit_log_store=PgAuditLogStore(db_connection),
        now=_NOW,
    )
    daily = parse_daily_flow((_FIXTURES / "synthetic_daily_flow.txt").read_text())
    rating = parse_rating_tables(
        (_FIXTURES / "synthetic_rating_tables.txt").read_text()
    )
    files = {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations
    }
    replace_delivery(
        tenants,
        stations,
        curves,
        observations,
        _CHWRR,
        files,
        audit_log_store=PgAuditLogStore(db_connection),
        now=_NOW,
    )
    station = stations.fetch_station_by_code("447", "dhm")
    assert station is not None
    original_curves = curves.fetch_delivery_curves(DELIVERY_ID, [station.id])
    original_rows = observations.fetch_delivery_observations(DELIVERY_ID, [station.id])
    db_connection.execute(
        sa.insert(models_table).values(
            id="dhm-reference-test",
            display_name="DHM reference test",
            artifact_scope="station",
            description="Synthetic FK test",
        )
    )
    db_connection.execute(
        sa.insert(forecasts_table).values(
            id=uuid4(),
            station_id=station.id,
            model_id="dhm-reference-test",
            issued_at=_NOW,
            representation="members",
            parameter="discharge",
            units="m3/s",
            rating_curve_id=original_curves[0].id,
        )
    )

    nested = db_connection.begin_nested()
    with pytest.raises(DeliveryCurveDependencyError, match="dependent record"):
        replace_delivery(
            tenants,
            stations,
            curves,
            observations,
            _CHWRR,
            files,
            audit_log_store=PgAuditLogStore(db_connection),
            now=_NOW,
        )
    nested.rollback()

    assert curves.fetch_delivery_curves(DELIVERY_ID, [station.id]) == original_curves
    assert (
        observations.fetch_delivery_observations(DELIVERY_ID, [station.id])
        == original_rows
    )


def test_qc_rolls_back_partial_updates_then_persists_versions(
    db_connection: sa.Connection,
) -> None:
    tenants = PgTenantStore(db_connection)
    stations = PgStationStore(db_connection)
    curves = PgRatingCurveStore(db_connection)
    observations = PgObservationStore(db_connection)
    bootstrap_tenant(tenants, _ADMIN, tenant_code="chwrr", now=_NOW)
    metadata = load_station_metadata(_FIXTURES / "stations.toml")
    register_stations(
        tenants,
        stations,
        _CHWRR,
        metadata,
        audit_log_store=PgAuditLogStore(db_connection),
        now=_NOW,
    )
    swiss = make_station_config(code="2009")
    stations.store_station(swiss)
    observations.store_raw_observations(
        [
            RawObservation(
                station_id=swiss.id,
                timestamp=nepal_day_start(date(1985, 1, 1)),
                parameter="discharge",
                value=25000.0,
                source=ObservationSource.MANUAL_IMPORT,
            )
        ]
    )
    daily = parse_daily_flow((_FIXTURES / "synthetic_daily_flow.txt").read_text())
    rating = parse_rating_tables(
        (_FIXTURES / "synthetic_rating_tables.txt").read_text()
    )
    files = {
        spec.code: (
            replace(
                daily,
                station_code=spec.code,
                values=(
                    (
                        replace(daily.values[0], discharge_m3s=25000.0)
                        if spec.code == "447"
                        else daily.values[0]
                    ),
                    *daily.values[1:],
                ),
            ),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations
    }
    replace_delivery(
        tenants,
        stations,
        curves,
        observations,
        _CHWRR,
        files,
        audit_log_store=PgAuditLogStore(db_connection),
        now=_NOW,
    )
    station_ids = [
        station.id
        for spec in metadata.stations
        if (station := stations.fetch_station_by_code(spec.code, "dhm")) is not None
    ]
    config_path = Path(__file__).resolve().parents[3] / "config.toml"

    nested = db_connection.begin_nested()
    with pytest.raises(RuntimeError, match="injected QC update failure"):
        run_delivery_qc(
            tenants,
            stations,
            _FailingQcStore(db_connection),
            _CHWRR,
            config_path,
            audit_log_store=PgAuditLogStore(db_connection),
            now=_NOW,
        )
    nested.rollback()
    assert {
        row.qc_status
        for row in observations.fetch_delivery_observations(DELIVERY_ID, station_ids)
    } == {QcStatus.RAW}

    counts = run_delivery_qc(
        tenants,
        stations,
        observations,
        _CHWRR,
        config_path,
        audit_log_store=PgAuditLogStore(db_connection),
        now=_NOW,
    )
    assert sum(counts.values()) == 18
    station_447 = stations.fetch_station_by_code("447", "dhm")
    assert station_447 is not None
    row = next(
        row
        for row in observations.fetch_delivery_observations(
            DELIVERY_ID, [station_447.id]
        )
        if row.timestamp == nepal_day_start(date(1985, 1, 1))
    )
    assert row.qc_status is QcStatus.QC_FAILED
    assert row.qc_rule_version == "1.2"
    assert [(flag.rule_id, flag.rule_version) for flag in row.qc_flags] == [
        ("range_check", "1.0.0")
    ]
    assert (
        observations.fetch_observations(
            swiss.id,
            "discharge",
            nepal_day_start(date(1985, 1, 1)),
            nepal_day_start(date(1985, 1, 2)),
        )[0].qc_status
        is QcStatus.RAW
    )


def test_replacement_and_qc_tenant_lock_serializes(db_engine: sa.Engine) -> None:
    metadata = load_station_metadata(_FIXTURES / "stations.toml")
    daily = parse_daily_flow((_FIXTURES / "synthetic_daily_flow.txt").read_text())
    rating = parse_rating_tables(
        (_FIXTURES / "synthetic_rating_tables.txt").read_text()
    )
    files = {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations
    }
    with db_engine.begin() as setup:
        tenant_store = PgTenantStore(setup)
        station_store = PgStationStore(setup)
        tenant_id = bootstrap_tenant(
            tenant_store, _ADMIN, tenant_code="chwrr", now=_NOW
        )
        register_stations(
            tenant_store,
            station_store,
            _CHWRR,
            metadata,
            audit_log_store=PgAuditLogStore(setup),
            now=_NOW,
        )
        replace_delivery(
            tenant_store,
            station_store,
            PgRatingCurveStore(setup),
            PgObservationStore(setup),
            _CHWRR,
            files,
            audit_log_store=PgAuditLogStore(setup),
            now=_NOW,
        )
        station_ids = [
            station.id
            for spec in metadata.stations
            if (station := station_store.fetch_station_by_code(spec.code, "dhm"))
            is not None
        ]
    try:
        with db_engine.connect() as replacement, db_engine.connect() as qc:
            replacement_txn = replacement.begin()
            qc_txn = qc.begin()
            replace_delivery(
                PgTenantStore(replacement),
                PgStationStore(replacement),
                PgRatingCurveStore(replacement),
                PgObservationStore(replacement),
                _CHWRR,
                files,
                audit_log_store=PgAuditLogStore(replacement),
                now=_NOW,
            )
            qc.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(sa.exc.OperationalError, match="lock timeout"):
                run_delivery_qc(
                    PgTenantStore(qc),
                    PgStationStore(qc),
                    PgObservationStore(qc),
                    _CHWRR,
                    Path(__file__).resolve().parents[3] / "config.toml",
                    audit_log_store=PgAuditLogStore(qc),
                    now=_NOW,
                )
            qc_txn.rollback()
            replacement_txn.rollback()

            retry = qc.begin()
            counts = run_delivery_qc(
                PgTenantStore(qc),
                PgStationStore(qc),
                PgObservationStore(qc),
                _CHWRR,
                Path(__file__).resolve().parents[3] / "config.toml",
                audit_log_store=PgAuditLogStore(qc),
                now=_NOW,
            )
            assert sum(counts.values()) == 18
            retry.rollback()
    finally:
        with db_engine.begin() as cleanup:
            cleanup.execute(
                sa.delete(observations_table).where(
                    observations_table.c.station_id.in_(station_ids)
                )
            )
            cleanup.execute(
                sa.delete(rating_curves_table).where(
                    rating_curves_table.c.station_id.in_(station_ids)
                )
            )
            cleanup.execute(
                sa.delete(stations_table).where(stations_table.c.id.in_(station_ids))
            )
            cleanup.execute(
                sa.delete(tenants_table).where(tenants_table.c.id == tenant_id)
            )


class _FailingAuditStore:
    def append_entry(self, entry: AuditEntry) -> None:
        raise RuntimeError("injected audit failure")


def _audit_count(conn: sa.Connection) -> int:
    return conn.scalar(sa.select(sa.func.count()).select_from(audit_log_table)) or 0


def _seed_chwrr(conn: sa.Connection) -> None:
    PgTenantStore(conn).ensure_tenant(
        tenant_id=TenantId(uuid4()), code="chwrr", name="CHWRR Nepal"
    )


def test_failed_audit_write_rolls_back_the_station_registration(
    db_connection: sa.Connection,
) -> None:
    _seed_chwrr(db_connection)
    metadata = load_station_metadata(_FIXTURES / "stations.toml")
    nested = db_connection.begin_nested()
    with pytest.raises(RuntimeError, match="injected audit"):
        register_stations(
            PgTenantStore(db_connection),
            PgStationStore(db_connection),
            _CHWRR,
            metadata,
            audit_log_store=_FailingAuditStore(),
            now=_NOW,
        )
    nested.rollback()
    assert PgStationStore(db_connection).fetch_station_by_code("447", "dhm") is None


def test_failed_audit_write_rolls_back_the_replacement(
    db_connection: sa.Connection,
) -> None:
    _seed_chwrr(db_connection)
    tenants = PgTenantStore(db_connection)
    stations = PgStationStore(db_connection)
    curves = PgRatingCurveStore(db_connection)
    observations = PgObservationStore(db_connection)
    audit = PgAuditLogStore(db_connection)
    metadata = load_station_metadata(_FIXTURES / "stations.toml")
    register_stations(
        tenants, stations, _CHWRR, metadata, audit_log_store=audit, now=_NOW
    )
    daily = parse_daily_flow((_FIXTURES / "synthetic_daily_flow.txt").read_text())
    rating = parse_rating_tables(
        (_FIXTURES / "synthetic_rating_tables.txt").read_text()
    )
    files = {
        spec.code: (
            replace(daily, station_code=spec.code),
            replace(rating, station_code=spec.code),
        )
        for spec in metadata.stations
    }
    nested = db_connection.begin_nested()
    with pytest.raises(RuntimeError, match="injected audit"):
        replace_delivery(
            tenants,
            stations,
            curves,
            observations,
            _CHWRR,
            files,
            audit_log_store=_FailingAuditStore(),
            now=_NOW,
        )
    nested.rollback()
    station = stations.fetch_station_by_code("447", "dhm")
    assert station is not None
    assert observations.fetch_delivery_observations(DELIVERY_ID, [station.id]) == []
    assert curves.fetch_delivery_curves(DELIVERY_ID, [station.id]) == []


def test_success_appends_one_audit_row_in_the_same_transaction(
    db_connection: sa.Connection,
) -> None:
    _seed_chwrr(db_connection)
    before = _audit_count(db_connection)
    nested = db_connection.begin_nested()
    register_stations(
        PgTenantStore(db_connection),
        PgStationStore(db_connection),
        _CHWRR,
        load_station_metadata(_FIXTURES / "stations.toml"),
        audit_log_store=PgAuditLogStore(db_connection),
        now=_NOW,
    )
    assert _audit_count(db_connection) == before + 1
    nested.rollback()
    assert _audit_count(db_connection) == before


class TestDryRunWritesNoAuditRow:
    def test_dry_run_leaves_neither_stations_nor_an_audit_row(
        self, db_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.cli.import_dhm_delivery import main

        repo = Path(__file__).resolve().parents[3]
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(repo / "config.toml"))
        monkeypatch.setenv(
            "SAPPHIRE_CONFIG_OVERLAY", str(repo / "config/overlays/chwrr-import.toml")
        )
        monkeypatch.setenv(
            "DATABASE_URL", db_engine.url.render_as_string(hide_password=False)
        )
        with db_engine.begin() as setup:
            _seed_chwrr(setup)
        try:
            with db_engine.connect() as probe:
                before = _audit_count(probe)
            assert main(["stations", "--tenant", "chwrr", "--dry-run"]) == 0
            with db_engine.connect() as probe:
                assert _audit_count(probe) == before
                assert PgStationStore(probe).fetch_station_by_code("447", "dhm") is None
            assert main(["stations", "--tenant", "chwrr"]) == 0
            with db_engine.connect() as probe:
                assert _audit_count(probe) == before + 1
        finally:
            with db_engine.begin() as cleanup:
                cleanup.execute(
                    sa.delete(stations_table).where(stations_table.c.network == "dhm")
                )
                cleanup.execute(
                    sa.delete(tenants_table).where(tenants_table.c.code == "chwrr")
                )
