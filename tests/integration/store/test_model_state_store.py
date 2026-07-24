from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from sapphire_flow.db.metadata import models, stations
from sapphire_flow.store.model_state_store import PgModelStateStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

_T0 = ensure_utc(datetime(2026, 1, 1, 0, tzinfo=UTC))
_T1 = ensure_utc(datetime(2026, 1, 1, 1, tzinfo=UTC))
_T2 = ensure_utc(datetime(2026, 1, 1, 2, tzinfo=UTC))


def _seed_station(conn: sa.Connection) -> StationId:
    sid = StationId(uuid.uuid4())
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"MSS-{sid.hex[:6]}",
            name="Model State Test Station",
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


def _seed_model(conn: sa.Connection, model_id: str = "test_model_v1") -> ModelId:
    mid = ModelId(model_id)
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Test Model",
            artifact_scope="station",
            description="Integration test model",
        )
    )
    return mid


class TestPgModelStateStore:
    def test_store_and_fetch(self, db_connection: sa.Connection) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection)
        store = PgModelStateStore(db_connection)
        payload = b"\x00\x01\x02\x03\xff"

        store.store_state(station_id, model_id, _T0, payload)
        result = store.fetch_latest_state(station_id, model_id)

        assert result is not None
        issue_time, state_bytes = result
        assert issue_time == _T0
        assert state_bytes == payload

    def test_fetch_latest_returns_most_recent(
        self, db_connection: sa.Connection
    ) -> None:
        station_id = _seed_station(db_connection)
        model_id = _seed_model(db_connection, "test_model_ordering")
        store = PgModelStateStore(db_connection)

        store.store_state(station_id, model_id, _T1, b"older")
        store.store_state(station_id, model_id, _T2, b"newer")

        result = store.fetch_latest_state(station_id, model_id)

        assert result is not None
        issue_time, state_bytes = result
        assert issue_time == _T2
        assert state_bytes == b"newer"

    def test_fetch_latest_nonexistent(self, db_connection: sa.Connection) -> None:
        store = PgModelStateStore(db_connection)
        result = store.fetch_latest_state(
            StationId(uuid.uuid4()), ModelId("no_such_model")
        )
        assert result is None
