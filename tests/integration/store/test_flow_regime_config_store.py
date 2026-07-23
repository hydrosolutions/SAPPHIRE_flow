from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from sapphire_flow.db.metadata import stations
from sapphire_flow.store.flow_regime_config_store import PgFlowRegimeConfigStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.skill import FlowRegimeConfig
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _seed_station(conn: sa.Connection) -> StationId:
    sid = StationId(uuid.uuid4())
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code="FRC-001",
            name="Flow Regime Test Station",
            location="SRID=4326;POINT(8.5 47.4)",
            station_kind="river",
            network="bafu",
            timezone="Europe/Zurich",
            measured_parameters=["discharge"],
            ownership="own",
            tenant_id=DEFAULT_TENANT_ID,
        )
    )
    return sid


def _make_config(
    station_id: StationId,
    *,
    version: int = 1,
    p50: float = 50.0,
    p90: float = 90.0,
    parameter: str = "discharge",
) -> FlowRegimeConfig:
    return FlowRegimeConfig(
        id=uuid.uuid4(),
        station_id=station_id,
        parameter=parameter,
        p50=p50,
        p90=p90,
        computed_at=_NOW,
        observation_count=1000,
        version=version,
        created_at=_NOW,
    )


class TestPgFlowRegimeConfigStore:
    def test_store_and_fetch_latest(self, db_connection: sa.Connection) -> None:
        station_id = _seed_station(db_connection)
        store = PgFlowRegimeConfigStore(db_connection)
        config = _make_config(station_id, p50=55.5, p90=92.3)

        store.store_config(config)
        result = store.fetch_latest(station_id, "discharge")

        assert result is not None
        assert result.id == config.id
        assert result.station_id == station_id
        assert result.p50 == 55.5
        assert result.p90 == 92.3
        assert result.observation_count == 1000
        assert result.version == 1

    def test_fetch_latest_returns_highest_version(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        store = PgFlowRegimeConfigStore(db_connection)
        config_v1 = _make_config(station_id, version=1, p50=10.0, p90=20.0)
        config_v2 = _make_config(station_id, version=2, p50=30.0, p90=60.0)

        store.store_config(config_v1)
        store.store_config(config_v2)

        result = store.fetch_latest(station_id, "discharge")

        assert result is not None
        assert result.version == 2
        assert result.p50 == 30.0

    def test_fetch_latest_nonexistent(self, db_connection: sa.Connection) -> None:
        store = PgFlowRegimeConfigStore(db_connection)
        result = store.fetch_latest(StationId(uuid.uuid4()), "discharge")
        assert result is None
