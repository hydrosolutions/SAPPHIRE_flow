from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from sapphire_flow.cli import create_station as cs
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.services.qc_datum import obs_skipped_rules
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import WaterLevelGaugeType
from sapphire_flow.types.ids import TenantId
from tests.conftest import make_station_config

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
IDENTITY = DeploymentIdentityConfig(
    writable_tenants=frozenset({"sandbox"}), global_admin=False
)
ARGS = [
    "--tenant", "sandbox", "--code", "00001", "--network", "research",
    "--name", "Trishuli", "--lon", "85.34", "--lat", "28.16",
    "--timezone", "Asia/Kathmandu", "--parameters", "water_level",
    "--gateway-name", "trishuli_00001", "--apply",
    "--gauge-type", "bridge-sensor", "--datum-masl", "1480", "--sensor-masl", "1490",
]  # fmt: skip


def _run(conn: sa.Connection, args: list[str]) -> int:
    return cs.run_create(
        cs._parse_args(args),  # pyright: ignore[reportPrivateUsage]
        identity=IDENTITY,
        tenants=PgTenantStore(conn),
        stations=PgStationStore(conn),
        audit=PgAuditLogStore(conn),
        ask=None,
        new_id=uuid4,
        clock=lambda: NOW,
    )


class TestCreateStationCli:
    def test_apply_round_trips_and_second_run_is_refused(
        self, db_connection: sa.Connection
    ) -> None:
        PgTenantStore(db_connection).ensure_tenant(
            tenant_id=TenantId(uuid4()), code="sandbox", name="Sandbox"
        )
        assert _run(db_connection, ARGS) == 0
        station = PgStationStore(db_connection).fetch_station_by_code(
            "00001", "research"
        )
        assert station is not None
        assert station.water_level_gauge_type is WaterLevelGaugeType.BRIDGE_SENSOR
        assert (station.water_level_datum_masl, station.water_level_sensor_masl) == (
            1480.0,
            1490.0,
        )
        assert (
            obs_skipped_rules("water_level", station.water_level_datum_masl)
            == frozenset()
        )
        audited = db_connection.scalar(
            sa.text("SELECT count(*) FROM audit_log WHERE target_id = :i"),
            {"i": str(station.id)},
        )
        assert audited == 1
        assert _run(db_connection, ARGS) == 1


class TestStationGaugeColumns:
    def test_database_refuses_sensor_on_staff_gauge(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(
            water_level_datum_masl=1480.0,
            water_level_gauge_type=WaterLevelGaugeType.STAFF_OR_PRESSURE,
        )
        store = PgStationStore(db_connection)
        store.store_station(station)
        assert store.fetch_station(station.id) == station
        with pytest.raises(sa.exc.IntegrityError, match="ck_stations_water_level"):
            db_connection.execute(
                sa.text(
                    "UPDATE stations SET water_level_sensor_masl = 1 WHERE id = :i"
                ),
                {"i": station.id},
            )

    def test_database_refuses_inconsistent_gauge_rows(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config()
        PgStationStore(db_connection).store_station(station)
        bad_updates = {
            "ck_stations_water_level_gauge_type": "water_level_gauge_type='radar', "
            "water_level_datum_masl=1",
            "ck_stations_water_level_sensor_pairing": "water_level_sensor_masl=5",
            "ck_stations_water_level_gauge_datum": "water_level_gauge_type="
            "'staff_or_pressure'",
            "ck_stations_water_level_bridge_sensor": "water_level_gauge_type="
            "'bridge_sensor', water_level_datum_masl=1",
        }
        for name, assignment in bad_updates.items():
            with db_connection.begin_nested() as savepoint:
                with pytest.raises(sa.exc.IntegrityError, match=name):
                    db_connection.execute(
                        sa.text(f"UPDATE stations SET {assignment} WHERE id = :i"),
                        {"i": station.id},
                    )
                savepoint.rollback()

    def test_update_station_writes_gauge_columns(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config()
        store = PgStationStore(db_connection)
        store.store_station(station)
        updated = replace(
            station,
            water_level_datum_masl=1480.0,
            water_level_gauge_type=WaterLevelGaugeType.BRIDGE_SENSOR,
            water_level_sensor_masl=1490.0,
        )
        store.update_station(updated)
        fetched = store.fetch_station(station.id)
        assert fetched is not None
        assert fetched.water_level_gauge_type is WaterLevelGaugeType.BRIDGE_SENSOR
        assert fetched.water_level_sensor_masl == 1490.0

    def test_migration_0087_down_and_up(self, db_connection: sa.Connection) -> None:
        revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(
            "0087"
        )
        assert revision is not None
        columns = (
            "SELECT count(*) FROM information_schema.columns WHERE "
            "table_name = 'stations' AND column_name IN "
            "('water_level_gauge_type', 'water_level_sensor_masl')"
        )
        assert db_connection.scalar(sa.text(columns)) == 2
        with Operations.context(MigrationContext.configure(db_connection)):
            revision.module.downgrade()
            assert db_connection.scalar(sa.text(columns)) == 0
            revision.module.upgrade()
        assert db_connection.scalar(sa.text(columns)) == 2
