from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sapphire_flow.store.alert_store import PgAlertStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AlertSource, AlertStatus
from sapphire_flow.types.ids import AlertId
from tests.conftest import make_alert, make_station_config

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.types.ids import StationId


def _seed_station(conn: sa.Connection) -> StationId:
    station = make_station_config(rng=random.Random(1))
    PgStationStore(conn).store_station(station)
    return station.id


class TestUpsertNewAlert:
    def test_upsert_new_alert(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)
        alert = make_alert(station_id=sid)

        returned_id = store.upsert_alert(alert)

        assert returned_id == alert.id


class TestUpsertExistingActiveAlert:
    def test_upsert_existing_active_alert(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)
        rng = random.Random(2)
        first = make_alert(station_id=sid, alert_level="High", rng=rng)
        first_id = store.upsert_alert(first)

        second = make_alert(
            station_id=sid,
            alert_level="High",
            source=AlertSource.FORECAST,
            rng=random.Random(3),
        )
        second_id = store.upsert_alert(second)

        assert second_id == first_id

        active = store.fetch_active_alerts(station_id=sid)
        assert len(active) == 1
        assert active[0].id == first_id
        assert active[0].trigger_probability == second.trigger_probability


class TestUpsertResolvedAlwaysInserts:
    def test_upsert_resolved_always_inserts(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)
        rng = random.Random(4)
        alert = make_alert(station_id=sid, status=AlertStatus.RESOLVED, rng=rng)

        id1 = store.upsert_alert(alert)

        alert2 = replace(alert, id=AlertId(uuid4()))
        id2 = store.upsert_alert(alert2)

        assert id1 != id2


class TestFetchActiveAlerts:
    def test_fetch_active_alerts(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)
        raised = make_alert(station_id=sid, rng=random.Random(5))
        resolved = make_alert(
            station_id=sid,
            alert_level="Extreme",
            status=AlertStatus.RESOLVED,
            rng=random.Random(6),
        )
        store.upsert_alert(raised)
        store.upsert_alert(resolved)

        active = store.fetch_active_alerts(station_id=sid)

        assert len(active) == 1
        assert active[0].id == raised.id
        assert active[0].status == AlertStatus.RAISED


class TestFetchActiveAlertsFilter:
    def test_fetch_active_alerts_filter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)
        forecast_alert = make_alert(
            station_id=sid,
            source=AlertSource.FORECAST,
            alert_level="High",
            rng=random.Random(7),
        )
        obs_alert = make_alert(
            station_id=sid,
            source=AlertSource.OBSERVATION,
            alert_level="Moderate",
            rng=random.Random(8),
        )
        store.upsert_alert(forecast_alert)
        store.upsert_alert(obs_alert)

        results = store.fetch_active_alerts(station_id=sid, source=AlertSource.FORECAST)

        assert len(results) == 1
        assert results[0].source == AlertSource.FORECAST


class TestResolveAlert:
    def test_resolve_alert(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)
        alert = make_alert(station_id=sid, rng=random.Random(9))
        alert_id = store.upsert_alert(alert)

        store.resolve_alert(alert_id)

        active = store.fetch_active_alerts(station_id=sid)
        assert len(active) == 0

        history = store.fetch_alert_history(
            station_id=sid,
            start=ensure_utc(datetime(2024, 12, 31, tzinfo=UTC)),
            end=ensure_utc(datetime(2025, 1, 2, tzinfo=UTC)),
        )
        resolved = next(a for a in history if a.id == alert_id)
        assert resolved.status == AlertStatus.RESOLVED
        assert resolved.resolved_at is not None


class TestAcknowledgeAlert:
    def test_acknowledge_alert(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)
        alert = make_alert(station_id=sid, rng=random.Random(10))
        alert_id = store.upsert_alert(alert)
        ack_by = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        store.acknowledge_alert(alert_id, ack_by)

        active = store.fetch_active_alerts(station_id=sid)
        assert len(active) == 1
        acked = active[0]
        assert acked.status == AlertStatus.ACKNOWLEDGED
        assert acked.acknowledged_by == ack_by
        assert acked.acknowledged_at is not None


class TestFetchAlertHistory:
    def test_fetch_alert_history_half_open(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        def _alert_at(hour: int, level: str, rng_seed: int) -> None:
            a = make_alert(
                station_id=sid,
                alert_level=level,
                status=AlertStatus.RESOLVED,
                rng=random.Random(rng_seed),
            )
            a = replace(
                a,
                triggered_at=ensure_utc(datetime(2025, 1, 1, hour, tzinfo=UTC)),
            )
            store.upsert_alert(a)

        _alert_at(0, "Low", 11)
        _alert_at(6, "Moderate", 12)
        _alert_at(12, "High", 13)
        _alert_at(23, "Extreme", 14)

        start = ensure_utc(datetime(2025, 1, 1, 6, tzinfo=UTC))
        end = ensure_utc(datetime(2025, 1, 1, 23, tzinfo=UTC))

        results = store.fetch_alert_history(station_id=sid, start=start, end=end)

        levels = {r.alert_level for r in results}
        assert "Moderate" in levels
        assert "High" in levels
        assert "Low" not in levels
        assert "Extreme" not in levels
