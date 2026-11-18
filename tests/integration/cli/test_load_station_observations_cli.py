from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from sapphire_flow.cli import load_station_observations as lso
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.config.qc_rules import resolve_qc_rules
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import WaterLevelGaugeType
from sapphire_flow.types.ids import StationId, TenantId
from tests.conftest import make_station_config

NOW = ensure_utc(datetime(2026, 11, 18, tzinfo=UTC))
IDENTITY = DeploymentIdentityConfig(
    writable_tenants=frozenset({"sandbox"}), global_admin=False
)
CSV = "t,v\n2026-11-01 06:00,2.0\n2026-11-02 06:00,3.0\n"


def _load(conn: sa.Connection, code: str) -> int:
    args = lso._parse_args(  # pyright: ignore[reportPrivateUsage]
        [
            "water-level", "--tenant", "sandbox", "--station-code", code,
            "--network", "research", "--csv", "x.csv", "--time-column", "t",
            "--value-column", "v", "--utc-offset-minutes", "345", "--apply",
        ]
    )  # fmt: skip
    return lso.run_load(
        args,
        text=CSV,
        identity=IDENTITY,
        tenants=PgTenantStore(conn),
        stations=PgStationStore(conn),
        observations=PgObservationStore(conn),
        audit=PgAuditLogStore(conn),
        baselines=PgClimBaselineStore(conn),
        qc_rules=resolve_qc_rules()[0],
        clock=lambda: NOW,
    )


class TestLoadStationObservationsCli:
    def test_staff_and_bridge_stations_round_trip_with_one_audit_row_each(
        self, db_connection: sa.Connection
    ) -> None:
        tenant = PgTenantStore(db_connection).ensure_tenant(
            tenant_id=TenantId(uuid4()), code="sandbox", name="Sandbox"
        )
        stations = PgStationStore(db_connection)
        staff = make_station_config(
            station_id=StationId(uuid4()),
            code="S1",
            network="research",
            tenant_id=tenant.id,
            water_level_datum_masl=1480.0,
            water_level_unit="m",
            water_level_gauge_type=WaterLevelGaugeType.STAFF_OR_PRESSURE,
        )
        bridge = make_station_config(
            station_id=StationId(uuid4()),
            code="B1",
            network="research",
            tenant_id=tenant.id,
            water_level_datum_masl=1480.0,
            water_level_unit="m",
            water_level_gauge_type=WaterLevelGaugeType.BRIDGE_SENSOR,
            water_level_sensor_masl=1490.0,
        )
        stations.store_station(staff)
        stations.store_station(bridge)

        assert _load(db_connection, "S1") == 0
        assert _load(db_connection, "B1") == 0

        store = PgObservationStore(db_connection)
        start, end = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)), NOW
        staff_values = sorted(
            o.value or 0.0
            for o in store.fetch_observations(staff.id, "water_level", start, end)
        )
        bridge_values = sorted(
            o.value or 0.0
            for o in store.fetch_observations(bridge.id, "water_level", start, end)
        )
        assert staff_values == [1482.0, 1483.0]
        assert bridge_values == [1487.0, 1488.0]
        for station in (staff, bridge):
            row = db_connection.execute(
                sa.text(
                    "SELECT event_type, detail FROM audit_log WHERE target_id = :i"
                ),
                {"i": str(station.id)},
            ).one()
            assert row.event_type == "observations_loaded"
            assert row.detail["rows"] == 2
