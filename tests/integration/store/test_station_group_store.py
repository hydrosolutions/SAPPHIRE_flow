from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.station import StationGroup
from tests.conftest import make_station_config

_NOW = datetime(2025, 1, 1, tzinfo=UTC)


def _seed_station(conn: sa.Connection, code: str) -> StationId:
    from sapphire_flow.store.station_store import PgStationStore

    station = make_station_config(station_id=StationId(uuid.uuid4()), code=code)
    PgStationStore(conn).store_station(station)
    return station.id


def _seed_model(conn: sa.Connection, model_id: str) -> None:
    from sapphire_flow.db.metadata import models

    conn.execute(
        sa.insert(models).values(
            id=model_id,
            display_name="Test Model",
            artifact_scope="group",
            description="Test",
        )
    )


def _seed_assignment(
    conn: sa.Connection, station_id: StationId, model_id: str, status: str = "active"
) -> None:
    from sapphire_flow.db.metadata import model_assignments

    conn.execute(
        sa.insert(model_assignments).values(
            station_id=station_id,
            model_id=model_id,
            time_step=timedelta(hours=1),
            status=status,
            priority=0,
            created_at=_NOW,
        )
    )


def _make_group(
    name: str,
    station_ids: frozenset[StationId] | None = None,
) -> StationGroup:
    return StationGroup(
        id=StationGroupId(uuid.uuid4()),
        name=name,
        station_ids=station_ids or frozenset(),
        description=None,
        created_at=_NOW,
    )


class TestStoreAndFetchGroup:
    def test_round_trip(self, db_connection: sa.Connection) -> None:
        s1 = _seed_station(db_connection, "G-001")
        s2 = _seed_station(db_connection, "G-002")

        group = _make_group("alpine", frozenset({s1, s2}))
        store = PgStationGroupStore(db_connection)
        store.store_group(group)

        fetched = store.fetch_group(group.id)
        assert fetched is not None
        assert fetched.id == group.id
        assert fetched.name == "alpine"
        assert fetched.station_ids == frozenset({s1, s2})
        assert fetched.description is None

    def test_description_stored(self, db_connection: sa.Connection) -> None:
        group = StationGroup(
            id=StationGroupId(uuid.uuid4()),
            name="with-desc",
            station_ids=frozenset(),
            description="A description",
            created_at=_NOW,
        )
        store = PgStationGroupStore(db_connection)
        store.store_group(group)

        fetched = store.fetch_group(group.id)
        assert fetched is not None
        assert fetched.description == "A description"


class TestFetchGroupByName:
    def test_lookup_by_name(self, db_connection: sa.Connection) -> None:
        group = _make_group("named-group")
        store = PgStationGroupStore(db_connection)
        store.store_group(group)

        fetched = store.fetch_group_by_name("named-group")
        assert fetched is not None
        assert fetched.id == group.id

    def test_missing_name_returns_none(self, db_connection: sa.Connection) -> None:
        store = PgStationGroupStore(db_connection)
        assert store.fetch_group_by_name("no-such-group") is None


class TestFetchGroupsForStation:
    def test_station_in_two_groups(self, db_connection: sa.Connection) -> None:
        s = _seed_station(db_connection, "SFG-001")
        g1 = _make_group("sfg-group-a", frozenset({s}))
        g2 = _make_group("sfg-group-b", frozenset({s}))
        other = _make_group("sfg-group-other")

        store = PgStationGroupStore(db_connection)
        store.store_group(g1)
        store.store_group(g2)
        store.store_group(other)

        results = store.fetch_groups_for_station(s)
        result_ids = {g.id for g in results}
        assert g1.id in result_ids
        assert g2.id in result_ids
        assert other.id not in result_ids

    def test_station_in_no_groups_returns_empty(
        self, db_connection: sa.Connection
    ) -> None:
        s = _seed_station(db_connection, "SFG-002")
        store = PgStationGroupStore(db_connection)
        assert store.fetch_groups_for_station(s) == []


class TestFetchGroupsForModel:
    def test_returns_groups_with_active_assignment(
        self, db_connection: sa.Connection
    ) -> None:
        s1 = _seed_station(db_connection, "FGM-001")
        s2 = _seed_station(db_connection, "FGM-002")
        _seed_model(db_connection, "ml_v1")

        g_with = _make_group("fgm-with", frozenset({s1}))
        g_without = _make_group("fgm-without", frozenset({s2}))

        store = PgStationGroupStore(db_connection)
        store.store_group(g_with)
        store.store_group(g_without)

        _seed_assignment(db_connection, s1, "ml_v1", status="active")

        results = store.fetch_groups_for_model(ModelId("ml_v1"))
        result_ids = {g.id for g in results}
        assert g_with.id in result_ids
        assert g_without.id not in result_ids

    def test_inactive_assignment_excluded(self, db_connection: sa.Connection) -> None:
        s = _seed_station(db_connection, "FGM-003")
        _seed_model(db_connection, "ml_v2")

        g = _make_group("fgm-inactive", frozenset({s}))
        store = PgStationGroupStore(db_connection)
        store.store_group(g)

        _seed_assignment(db_connection, s, "ml_v2", status="inactive")

        results = store.fetch_groups_for_model(ModelId("ml_v2"))
        assert results == []


class TestAddAndRemoveStation:
    def test_add_station_appears_in_fetch(self, db_connection: sa.Connection) -> None:
        s = _seed_station(db_connection, "AR-001")
        group = _make_group("ar-group")
        store = PgStationGroupStore(db_connection)
        store.store_group(group)

        store.add_station_to_group(group.id, s)

        fetched = store.fetch_group(group.id)
        assert fetched is not None
        assert s in fetched.station_ids

    def test_remove_station_gone_from_fetch(self, db_connection: sa.Connection) -> None:
        s = _seed_station(db_connection, "AR-002")
        group = _make_group("ar-remove-group", frozenset({s}))
        store = PgStationGroupStore(db_connection)
        store.store_group(group)

        store.remove_station_from_group(group.id, s)

        fetched = store.fetch_group(group.id)
        assert fetched is not None
        assert s not in fetched.station_ids

    def test_add_idempotent(self, db_connection: sa.Connection) -> None:
        s = _seed_station(db_connection, "AR-003")
        group = _make_group("ar-idem-group")
        store = PgStationGroupStore(db_connection)
        store.store_group(group)

        store.add_station_to_group(group.id, s)
        store.add_station_to_group(group.id, s)  # no error

        fetched = store.fetch_group(group.id)
        assert fetched is not None
        assert fetched.station_ids == frozenset({s})


class TestFetchNonexistent:
    def test_fetch_group_by_id_returns_none(self, db_connection: sa.Connection) -> None:
        store = PgStationGroupStore(db_connection)
        result = store.fetch_group(StationGroupId(uuid.uuid4()))
        assert result is None

    def test_fetch_group_by_name_returns_none(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgStationGroupStore(db_connection)
        result = store.fetch_group_by_name("ghost-group")
        assert result is None
