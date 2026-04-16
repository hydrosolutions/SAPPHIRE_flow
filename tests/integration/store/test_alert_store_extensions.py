from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sapphire_flow.store.alert_store import PgAlertStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AlertSource,
    AlertStatus,
    ModelCombinationStrategy,
)
from sapphire_flow.types.ids import AlertId, ModelId
from tests.conftest import make_alert, make_station_config

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.types.ids import StationId


def _seed_station(conn: sa.Connection) -> StationId:
    station = make_station_config(rng=random.Random(1))
    PgStationStore(conn).store_station(station)
    return station.id


class TestFetchAlertRoundTrip:
    def test_all_fields_round_trip(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        alert = make_alert(
            station_id=sid,
            alert_level="High",
            status=AlertStatus.ACKNOWLEDGED,
            rng=random.Random(100),
        )
        alert = replace(
            alert,
            model_ids=(ModelId("model_a"), ModelId("model_b")),
            alert_model_strategy=ModelCombinationStrategy.POOLED,
            acknowledged_at=ensure_utc(datetime(2025, 1, 1, 12, tzinfo=UTC)),
            acknowledged_by=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            first_detected_at=ensure_utc(datetime(2025, 1, 1, 6, tzinfo=UTC)),
            notified_at=ensure_utc(datetime(2025, 1, 1, 7, tzinfo=UTC)),
        )
        store.upsert_alert(alert)

        fetched = store.fetch_alert(alert.id)

        assert fetched is not None
        assert fetched.id == alert.id
        assert fetched.station_id == alert.station_id
        assert fetched.source is AlertSource.FORECAST
        assert fetched.alert_level == "High"
        assert fetched.status is AlertStatus.ACKNOWLEDGED
        assert fetched.trigger_probability == alert.trigger_probability
        assert fetched.trigger_value == alert.trigger_value
        assert fetched.triggered_at == alert.triggered_at
        assert fetched.triggered_at.tzinfo is not None
        assert fetched.acknowledged_at == alert.acknowledged_at
        assert fetched.acknowledged_at is not None
        assert fetched.acknowledged_at.tzinfo is not None
        assert fetched.acknowledged_by == UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        assert fetched.first_detected_at == alert.first_detected_at
        assert fetched.first_detected_at is not None
        assert fetched.first_detected_at.tzinfo is not None
        assert fetched.notified_at == alert.notified_at
        assert fetched.notified_at is not None
        assert fetched.notified_at.tzinfo is not None
        assert fetched.created_at.tzinfo is not None
        assert fetched.model_ids == (ModelId("model_a"), ModelId("model_b"))
        assert fetched.alert_model_strategy is ModelCombinationStrategy.POOLED


class TestFetchAlertNotFound:
    def test_returns_none_for_missing_id(self, db_connection: sa.Connection) -> None:
        store = PgAlertStore(db_connection)

        result = store.fetch_alert(AlertId(uuid4()))

        assert result is None


class TestFetchAlertsUnfiltered:
    def test_returns_all_seeded_alerts(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        alerts = []
        for i, (level, source, status) in enumerate(
            [
                ("High", AlertSource.FORECAST, AlertStatus.RAISED),
                ("Moderate", AlertSource.OBSERVATION, AlertStatus.ACKNOWLEDGED),
                ("Low", AlertSource.FORECAST, AlertStatus.RESOLVED),
                ("Extreme", AlertSource.OBSERVATION, AlertStatus.RESOLVED),
            ]
        ):
            a = make_alert(
                station_id=sid,
                alert_level=level,
                source=source,
                status=status,
                rng=random.Random(200 + i),
            )
            store.upsert_alert(a)
            alerts.append(a)

        items, total = store.fetch_alerts()

        assert total == 4
        fetched_ids = {item.id for item in items}
        expected_ids = {a.id for a in alerts}
        assert fetched_ids == expected_ids


class TestFetchAlertsFilterStatus:
    def test_filter_by_raised(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        make_and_upsert = [
            ("High", AlertStatus.RAISED, 300),
            ("Moderate", AlertStatus.ACKNOWLEDGED, 301),
            ("Low", AlertStatus.RESOLVED, 302),
        ]
        for level, status, seed in make_and_upsert:
            a = make_alert(
                station_id=sid,
                alert_level=level,
                status=status,
                rng=random.Random(seed),
            )
            store.upsert_alert(a)

        items, total = store.fetch_alerts(status=AlertStatus.RAISED)

        assert total == 1
        assert items[0].status is AlertStatus.RAISED

    def test_filter_by_resolved(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        make_and_upsert = [
            ("High", AlertStatus.RAISED, 310),
            ("Moderate", AlertStatus.ACKNOWLEDGED, 311),
            ("Low", AlertStatus.RESOLVED, 312),
        ]
        for level, status, seed in make_and_upsert:
            a = make_alert(
                station_id=sid,
                alert_level=level,
                status=status,
                rng=random.Random(seed),
            )
            store.upsert_alert(a)

        items, total = store.fetch_alerts(status=AlertStatus.RESOLVED)

        assert total == 1
        assert items[0].status is AlertStatus.RESOLVED


class TestFetchAlertsFilterSource:
    def test_filter_by_observation(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        forecast = make_alert(
            station_id=sid,
            source=AlertSource.FORECAST,
            alert_level="High",
            rng=random.Random(400),
        )
        observation = make_alert(
            station_id=sid,
            source=AlertSource.OBSERVATION,
            alert_level="Moderate",
            rng=random.Random(401),
        )
        store.upsert_alert(forecast)
        store.upsert_alert(observation)

        items, total = store.fetch_alerts(source=AlertSource.OBSERVATION)

        assert total == 1
        assert len(items) == 1
        assert items[0].source is AlertSource.OBSERVATION


class TestFetchAlertsFilterLevel:
    def test_filter_by_level(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        for level, seed in [("High", 500), ("Moderate", 501), ("Low", 502)]:
            a = make_alert(
                station_id=sid,
                alert_level=level,
                rng=random.Random(seed),
            )
            store.upsert_alert(a)

        items, total = store.fetch_alerts(level="High")

        assert total == 1
        assert len(items) == 1
        assert items[0].alert_level == "High"


class TestFetchAlertsSystemAlerts:
    def test_system_alerts_included_in_unfiltered(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        system_alert = make_alert(
            alert_level="System",
            rng=random.Random(600),
        )
        system_alert = replace(system_alert, station_id=None)

        station_alert = make_alert(
            station_id=sid,
            alert_level="High",
            rng=random.Random(601),
        )
        store.upsert_alert(system_alert)
        store.upsert_alert(station_alert)

        items, total = store.fetch_alerts()

        assert total == 2
        ids = {item.id for item in items}
        assert system_alert.id in ids
        assert station_alert.id in ids

    def test_station_filter_excludes_system_alerts(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        system_alert = make_alert(
            alert_level="System",
            rng=random.Random(610),
        )
        system_alert = replace(system_alert, station_id=None)

        station_alert = make_alert(
            station_id=sid,
            alert_level="High",
            rng=random.Random(611),
        )
        store.upsert_alert(system_alert)
        store.upsert_alert(station_alert)

        items, total = store.fetch_alerts(station_id=sid)

        assert total == 1
        assert items[0].id == station_alert.id


class TestFetchAlertsOrdering:
    def test_newest_first(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        t1 = ensure_utc(datetime(2025, 1, 1, 0, tzinfo=UTC))
        t2 = ensure_utc(datetime(2025, 1, 1, 6, tzinfo=UTC))
        t3 = ensure_utc(datetime(2025, 1, 1, 12, tzinfo=UTC))

        for ts, level, seed in [
            (t1, "Low", 700),
            (t2, "Moderate", 701),
            (t3, "High", 702),
        ]:
            a = make_alert(
                station_id=sid,
                alert_level=level,
                status=AlertStatus.RESOLVED,
                rng=random.Random(seed),
            )
            a = replace(a, triggered_at=ts)
            store.upsert_alert(a)

        items, total = store.fetch_alerts()

        assert total == 3
        assert items[0].triggered_at == t3
        assert items[1].triggered_at == t2
        assert items[2].triggered_at == t1


class TestFetchAlertsPagination:
    def test_paginated_results(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        store = PgAlertStore(db_connection)

        timestamps = [ensure_utc(datetime(2025, 1, 1, h, tzinfo=UTC)) for h in range(5)]
        levels = ["Low", "Moderate", "High", "Extreme", "Critical"]
        seeded_alerts = []
        for i, (ts, level) in enumerate(zip(timestamps, levels, strict=True)):
            a = make_alert(
                station_id=sid,
                alert_level=level,
                status=AlertStatus.RESOLVED,
                rng=random.Random(800 + i),
            )
            a = replace(a, triggered_at=ts)
            store.upsert_alert(a)
            seeded_alerts.append(a)

        t1, t2, t3, t4, t5 = timestamps

        # Page 1
        items_p1, total_p1 = store.fetch_alerts(limit=2, offset=0)
        assert total_p1 == 5
        assert len(items_p1) == 2
        assert items_p1[0].triggered_at == t5
        assert items_p1[1].triggered_at == t4

        # Page 2
        items_p2, total_p2 = store.fetch_alerts(limit=2, offset=2)
        assert total_p2 == 5
        assert len(items_p2) == 2
        assert items_p2[0].triggered_at == t3
        assert items_p2[1].triggered_at == t2

        # Page 3 (partial)
        items_p3, total_p3 = store.fetch_alerts(limit=2, offset=4)
        assert total_p3 == 5
        assert len(items_p3) == 1
        assert items_p3[0].triggered_at == t1

        # Verify complete coverage with no overlap
        all_ids = (
            {a.id for a in items_p1}
            | {a.id for a in items_p2}
            | {a.id for a in items_p3}
        )
        expected_ids = {a.id for a in seeded_alerts}
        assert all_ids == expected_ids
        assert len(all_ids) == 5
