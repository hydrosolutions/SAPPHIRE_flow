from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import station_group_members, station_groups, tenants
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.types.enums import ModelAssignmentStatus
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId, TenantId
from sapphire_flow.types.station import GroupModelAssignment, StationGroup
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import make_station_config


class _SpyConn:
    """Proxy that records every statement executed through it."""

    def __init__(self, real: sa.Connection) -> None:
        self._real = real
        self.executed: list[object] = []

    def execute(self, stmt: object, *a: object, **k: object) -> object:
        self.executed.append(stmt)
        return self._real.execute(stmt, *a, **k)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


@contextmanager
def _savepoint_spy_factory(conn: sa.Connection):  # type: ignore[return]
    spy = _SpyConn(conn)
    with conn.begin_nested():
        yield spy


def savepoint_factory(conn: sa.Connection):
    return lambda: _savepoint_spy_factory(conn)


def _capturing_spy_factory(conn: sa.Connection) -> tuple[list[_SpyConn], object]:
    """Return (spies list, factory) so tests can inspect the captured spy."""
    spies: list[_SpyConn] = []

    @contextmanager
    def _factory():  # type: ignore[return]
        spy = _SpyConn(conn)
        spies.append(spy)
        with conn.begin_nested():
            yield spy

    return spies, _factory


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
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
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
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_group(group)

        fetched = store.fetch_group(group.id)
        assert fetched is not None
        assert fetched.description == "A description"


class TestFetchGroupByName:
    def test_lookup_by_name(self, db_connection: sa.Connection) -> None:
        group = _make_group("named-group")
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_group(group)

        fetched = store.fetch_group_by_name(DEFAULT_TENANT_ID, "named-group")
        assert fetched is not None
        assert fetched.id == group.id

    def test_missing_name_returns_none(self, db_connection: sa.Connection) -> None:
        store = PgStationGroupStore(db_connection)
        assert store.fetch_group_by_name(DEFAULT_TENANT_ID, "no-such-group") is None


class TestFetchGroupsForStation:
    def test_station_in_two_groups(self, db_connection: sa.Connection) -> None:
        s = _seed_station(db_connection, "SFG-001")
        g1 = _make_group("sfg-group-a", frozenset({s}))
        g2 = _make_group("sfg-group-b", frozenset({s}))
        other = _make_group("sfg-group-other")

        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
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
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
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

        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_group(g_with)
        store.store_group(g_without)

        store.store_group_model_assignment(
            _make_group_model_assignment(g_with.id, ModelId("ml_v1"))
        )

        results = store.fetch_groups_for_model(ModelId("ml_v1"))
        result_ids = {g.id for g in results}
        assert g_with.id in result_ids
        assert g_without.id not in result_ids

    def test_inactive_assignment_excluded(self, db_connection: sa.Connection) -> None:
        s = _seed_station(db_connection, "FGM-003")
        _seed_model(db_connection, "ml_v2")

        g = _make_group("fgm-inactive", frozenset({s}))
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_group(g)

        store.store_group_model_assignment(
            _make_group_model_assignment(
                g.id,
                ModelId("ml_v2"),
                status=ModelAssignmentStatus.INACTIVE,
            )
        )

        results = store.fetch_groups_for_model(ModelId("ml_v2"))
        assert results == []


class TestAddAndRemoveStation:
    def test_add_station_appears_in_fetch(self, db_connection: sa.Connection) -> None:
        s = _seed_station(db_connection, "AR-001")
        group = _make_group("ar-group")
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_group(group)

        store.add_station_to_group(group.id, s)

        fetched = store.fetch_group(group.id)
        assert fetched is not None
        assert s in fetched.station_ids

    def test_remove_station_gone_from_fetch(self, db_connection: sa.Connection) -> None:
        s = _seed_station(db_connection, "AR-002")
        group = _make_group("ar-remove-group", frozenset({s}))
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_group(group)

        store.remove_station_from_group(group.id, s)

        fetched = store.fetch_group(group.id)
        assert fetched is not None
        assert s not in fetched.station_ids

    def test_add_idempotent(self, db_connection: sa.Connection) -> None:
        s = _seed_station(db_connection, "AR-003")
        group = _make_group("ar-idem-group")
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
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
        result = store.fetch_group_by_name(DEFAULT_TENANT_ID, "ghost-group")
        assert result is None


def _make_group_model_assignment(
    group_id: StationGroupId,
    model_id: ModelId,
    *,
    status: ModelAssignmentStatus = ModelAssignmentStatus.ACTIVE,
    priority: int = 0,
    time_step: timedelta = timedelta(hours=1),
) -> GroupModelAssignment:
    return GroupModelAssignment(
        group_id=group_id,
        model_id=model_id,
        time_step=time_step,
        status=status,
        priority=priority,
        created_at=_NOW,
    )


class TestStoreGroupModelAssignment:
    def test_happy_path(self, db_connection: sa.Connection) -> None:
        group = _make_group("gma-happy")
        _seed_model(db_connection, "gma-model-1")
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_group(group)

        assignment = _make_group_model_assignment(group.id, ModelId("gma-model-1"))
        store.store_group_model_assignment(assignment)

        results = store.fetch_group_model_assignments(group.id)
        assert len(results) == 1
        fetched = results[0]
        assert fetched.group_id == group.id
        assert fetched.model_id == ModelId("gma-model-1")
        assert fetched.time_step == timedelta(hours=1)
        assert fetched.status == ModelAssignmentStatus.ACTIVE
        assert fetched.priority == 0

    def test_upsert_second_write_wins(self, db_connection: sa.Connection) -> None:
        group = _make_group("gma-upsert")
        _seed_model(db_connection, "gma-model-2")
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_group(group)

        first = _make_group_model_assignment(
            group.id,
            ModelId("gma-model-2"),
            status=ModelAssignmentStatus.ACTIVE,
            priority=0,
            time_step=timedelta(hours=1),
        )
        store.store_group_model_assignment(first)

        second = _make_group_model_assignment(
            group.id,
            ModelId("gma-model-2"),
            status=ModelAssignmentStatus.INACTIVE,
            priority=5,
            time_step=timedelta(hours=6),
        )
        store.store_group_model_assignment(second)

        results = store.fetch_group_model_assignments(group.id)
        assert len(results) == 1
        fetched = results[0]
        assert fetched.status == ModelAssignmentStatus.INACTIVE
        assert fetched.priority == 5
        assert fetched.time_step == timedelta(hours=6)

    def test_empty_fetch_returns_empty_tuple(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgStationGroupStore(db_connection)
        results = store.fetch_group_model_assignments(StationGroupId(uuid.uuid4()))
        assert results == ()

    def test_fetch_only_returns_assignments_for_group(
        self, db_connection: sa.Connection
    ) -> None:
        g1 = _make_group("gma-filter-g1")
        g2 = _make_group("gma-filter-g2")
        _seed_model(db_connection, "gma-model-3")
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_group(g1)
        store.store_group(g2)

        store.store_group_model_assignment(
            _make_group_model_assignment(g1.id, ModelId("gma-model-3"))
        )
        store.store_group_model_assignment(
            _make_group_model_assignment(g2.id, ModelId("gma-model-3"))
        )

        results = store.fetch_group_model_assignments(g1.id)
        assert len(results) == 1
        assert results[0].group_id == g1.id


# ---------------------------------------------------------------------------
# Plan 038 locked atomicity tests
# ---------------------------------------------------------------------------


class TestStoreGroupAtomicityDefaultFactory:
    def test_default_factory_is_engine_begin(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgStationGroupStore(db_connection)
        # engine.begin is a bound method — new object each access;
        # compare via __self__/__func__ to avoid identity failure
        assert getattr(store._begin, "__self__", None) is db_connection.engine
        engine_cls = type(db_connection.engine)
        assert getattr(store._begin, "__func__", None) is engine_cls.begin


class TestStoreGroupAtomicityRollback:
    def test_members_insert_failure_rolls_back_header(
        self, db_connection: sa.Connection
    ) -> None:
        """Prove the members insert fires AND the header is absent after rollback.

        We use an FK violation on station_group_members (non-existent station_id)
        to trigger the failure.  The spy confirms the members INSERT was actually
        reached before failing, ruling out a pass caused by a header-level error.
        """
        import sqlalchemy.exc

        nonexistent_station = StationId(uuid.uuid4())
        group = StationGroup(
            id=StationGroupId(uuid.uuid4()),
            name="atomic-rollback-group",
            station_ids=frozenset({nonexistent_station}),
            description=None,
            created_at=_NOW,
        )

        hit_members_insert: dict[str, bool] = {"fired": False}

        @contextmanager
        def _failing_spy_factory():  # type: ignore[return]
            spy = _SpyConn(db_connection)
            real_spy_execute = spy.execute

            def _patched(stmt: object, *a: object, **k: object) -> object:
                if (
                    isinstance(stmt, sa.sql.dml.Insert)
                    and getattr(getattr(stmt, "table", None), "name", "")
                    == "station_group_members"
                ):
                    hit_members_insert["fired"] = True
                # Let the real execute run (FK violation will raise naturally)
                return real_spy_execute(stmt, *a, **k)

            spy.execute = _patched  # type: ignore[method-assign]
            with db_connection.begin_nested():
                yield spy

        store = PgStationGroupStore(
            db_connection, transaction_factory=_failing_spy_factory
        )

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            store.store_group(group)

        # The members insert must have fired (rules out a header-level short-circuit)
        assert hit_members_insert["fired"], (
            "station_group_members INSERT was never reached"
        )

        # Both header and members must be absent (rollback was atomic)
        row = db_connection.execute(
            sa.select(station_groups.c.id).where(station_groups.c.id == group.id)
        ).first()
        assert row is None

        member_row = db_connection.execute(
            sa.select(station_group_members.c.group_id).where(
                station_group_members.c.group_id == group.id
            )
        ).first()
        assert member_row is None


class TestStoreGroupAtomicitySuccess:
    def test_writes_routed_through_injected_txn(
        self, db_connection: sa.Connection
    ) -> None:
        """Prove both INSERTs go through the spy (not self._conn).

        A broken impl that bypasses the injected txn and writes directly on
        self._conn (= db_connection) would NOT appear in spy.executed.
        """
        s = _seed_station(db_connection, "ATOM-S-001")
        group = StationGroup(
            id=StationGroupId(uuid.uuid4()),
            name="atomic-success-group",
            station_ids=frozenset({s}),
            description=None,
            created_at=_NOW,
        )

        spies, factory = _capturing_spy_factory(db_connection)
        store = PgStationGroupStore(db_connection, transaction_factory=factory)

        store.store_group(group)

        assert len(spies) == 1, "factory must have been called exactly once"
        spy = spies[0]

        # The spy must have recorded at least 2 statements: header upsert + members
        assert len(spy.executed) >= 2, (
            f"expected ≥2 statements via txn spy, got {len(spy.executed)}"
        )

        table_names = {
            getattr(getattr(stmt, "table", None), "name", None) for stmt in spy.executed
        }
        assert "station_groups" in table_names, (
            "station_groups header INSERT missing from spy"
        )
        assert "station_group_members" in table_names, (
            "station_group_members INSERT missing from spy"
        )

        header_row = db_connection.execute(
            sa.select(station_groups.c.id).where(station_groups.c.id == group.id)
        ).first()
        assert header_row is not None

        member_count = db_connection.execute(
            sa.select(sa.func.count()).where(
                station_group_members.c.group_id == group.id
            )
        ).scalar_one()
        assert member_count == 1


class TestStoreGroupIsolationHolds:
    def test_rolled_back_savepoint_invisible_from_fresh_connection(
        self, db_connection: sa.Connection, db_engine: sa.Engine
    ) -> None:
        """Prove a rolled-back savepoint write is invisible from a fresh connection."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        group = StationGroup(
            id=StationGroupId(uuid.uuid4()),
            name="isolation-holds-group",
            station_ids=frozenset(),
            description=None,
            created_at=_NOW,
        )

        # Write inside a savepoint then explicitly roll it back
        with db_connection.begin_nested() as sp:
            db_connection.execute(
                pg_insert(station_groups).values(
                    id=group.id,
                    name=group.name,
                    description=group.description,
                    created_at=group.created_at,
                    tenant_id=DEFAULT_TENANT_ID,
                )
            )
            sp.rollback()

        # Verify the write is invisible from a separate connection
        with db_engine.connect() as fresh_conn:
            row = fresh_conn.execute(
                sa.select(station_groups.c.id).where(station_groups.c.id == group.id)
            ).first()
        assert row is None, "rolled-back savepoint write leaked to a fresh connection"


def _seed_tenant(conn: sa.Connection, tenant_id: TenantId, code: str) -> None:
    conn.execute(
        sa.insert(tenants).values(id=tenant_id, code=code, name=f"Tenant {code}")
    )


class TestStoreGroupTenantImmutable:
    """MAJOR 2 (Plan 147 Slice A): a persisted group's tenant is IMMUTABLE. A
    re-store under a DIFFERENT tenant is rejected (never silently keeping the
    stored tenant, never mutating another tenant's group metadata); a re-store
    under the SAME tenant stays idempotent.

    Soundness: fails against the pre-fix ``store_group`` (whose ON CONFLICT DO
    UPDATE ignored tenant_id — the mismatched re-store would silently succeed,
    leaving the persisted tenant disagreeing with the supplied StationGroup).
    """

    def test_restore_with_different_tenant_raises(
        self, db_connection: sa.Connection
    ) -> None:
        other_tenant = TenantId(uuid.uuid4())
        _seed_tenant(db_connection, other_tenant, "dhm")
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        gid = StationGroupId(uuid.uuid4())
        store.store_group(
            StationGroup(
                id=gid,
                name="immutable-group",
                station_ids=frozenset(),
                created_at=_NOW,
                tenant_id=DEFAULT_TENANT_ID,
            )
        )

        with pytest.raises(ConfigurationError, match="immutable"):
            store.store_group(
                StationGroup(
                    id=gid,
                    name="immutable-group",
                    station_ids=frozenset(),
                    created_at=_NOW,
                    tenant_id=other_tenant,
                )
            )

        # The persisted tenant is unchanged — the mismatched re-store was refused.
        stored_tenant = db_connection.execute(
            sa.select(station_groups.c.tenant_id).where(station_groups.c.id == gid)
        ).scalar_one()
        assert TenantId(stored_tenant) == DEFAULT_TENANT_ID

    def test_restore_with_same_tenant_is_idempotent(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgStationGroupStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        gid = StationGroupId(uuid.uuid4())
        store.store_group(
            StationGroup(
                id=gid,
                name="idempotent-group",
                station_ids=frozenset(),
                description="first",
                created_at=_NOW,
                tenant_id=DEFAULT_TENANT_ID,
            )
        )
        # Same id + same tenant, updated metadata: succeeds and updates in place.
        store.store_group(
            StationGroup(
                id=gid,
                name="idempotent-group-renamed",
                station_ids=frozenset(),
                description="second",
                created_at=_NOW,
                tenant_id=DEFAULT_TENANT_ID,
            )
        )

        fetched = store.fetch_group(gid)
        assert fetched is not None
        assert fetched.name == "idempotent-group-renamed"
        assert fetched.description == "second"
        assert fetched.tenant_id == DEFAULT_TENANT_ID


class TestStoreGroupConditionalUpsertDirect:
    """Drive the conditional-upsert statement's RETURNING contract directly.

    A conflict against a row whose stored tenant_id disagrees with the
    proposed tenant_id must make `WHERE station_groups.tenant_id =
    EXCLUDED.tenant_id` exclude the DO UPDATE, so RETURNING yields zero rows
    — which is exactly what ``store_group`` treats as "raise
    ConfigurationError". This isolates that SQL-level contract from the
    Python control flow around it (already covered end-to-end by
    ``test_restore_with_different_tenant_raises`` above).
    """

    def test_conflict_with_mismatched_tenant_returns_no_row(
        self, db_connection: sa.Connection
    ) -> None:
        other_tenant = TenantId(uuid.uuid4())
        _seed_tenant(db_connection, other_tenant, "direct")

        gid = StationGroupId(uuid.uuid4())
        db_connection.execute(
            sa.insert(station_groups).values(
                id=gid,
                name="direct-original",
                description=None,
                created_at=_NOW,
                tenant_id=DEFAULT_TENANT_ID,
            )
        )

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        result = db_connection.execute(
            pg_insert(station_groups)
            .values(
                id=gid,
                name="direct-hijacked",
                description="attempted cross-tenant write",
                created_at=_NOW,
                tenant_id=other_tenant,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"name": "direct-hijacked", "description": "hijacked"},
                where=station_groups.c.tenant_id == other_tenant,
            )
            .returning(station_groups.c.id)
        )
        assert result.first() is None, (
            "conditional upsert must return no row on a tenant mismatch"
        )

        # And the stored row is untouched by the excluded update.
        row = db_connection.execute(
            sa.select(station_groups.c.name, station_groups.c.tenant_id).where(
                station_groups.c.id == gid
            )
        ).one()
        assert row.name == "direct-original"
        assert TenantId(row.tenant_id) == DEFAULT_TENANT_ID

    def test_soundness_unguarded_do_update_would_silently_hijack(
        self, db_connection: sa.Connection
    ) -> None:
        """Prove the WHERE guard is load-bearing.

        Same setup as above, but without ``where=`` on the DO UPDATE clause
        — reproducing the bug this fix closes. The update must succeed and
        RETURNING must yield a row, demonstrating that dropping the guard
        (or reverting to the pre-fix statement shape) reopens the hijack.
        """
        other_tenant = TenantId(uuid.uuid4())
        _seed_tenant(db_connection, other_tenant, "unguarded")

        gid = StationGroupId(uuid.uuid4())
        db_connection.execute(
            sa.insert(station_groups).values(
                id=gid,
                name="unguarded-original",
                description=None,
                created_at=_NOW,
                tenant_id=DEFAULT_TENANT_ID,
            )
        )

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        result = db_connection.execute(
            pg_insert(station_groups)
            .values(
                id=gid,
                name="unguarded-hijacked",
                description="attempted cross-tenant write",
                created_at=_NOW,
                tenant_id=other_tenant,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"name": "unguarded-hijacked", "description": "hijacked"},
                # deliberately no `where=` — this is the pre-fix shape.
            )
            .returning(station_groups.c.id)
        )
        assert result.first() is not None, (
            "without the WHERE guard the cross-tenant update silently succeeds "
            "(proves the guard, not incidental DB behavior, blocks the hijack)"
        )
        row = db_connection.execute(
            sa.select(station_groups.c.name).where(station_groups.c.id == gid)
        ).one()
        assert row.name == "unguarded-hijacked"


class TestStoreGroupConcurrentTenantRace:
    """Real two-connection concurrency test for the TOCTOU this fix closes.

    Two threads, each on its OWN connection/transaction (the default
    ``store_group`` transaction factory is ``engine.begin`` — a fresh
    connection per call), attempt to ``store_group`` the SAME new group id
    under DIFFERENT tenants at (as close to) the same instant, synchronized
    via a barrier. Postgres serializes the two conflicting INSERTs on the
    row's primary key: whichever commits first wins; the second blocks until
    the first commits, then re-evaluates the conditional DO UPDATE against
    the now-committed row and (since the tenant differs) gets no row back —
    which ``store_group`` turns into ``ConfigurationError``.

    This is only reachable because the guard is now part of the single
    atomic upsert statement: the old SELECT-then-Python-check let both
    threads observe "no row yet" and both proceed to insert/update.
    """

    def test_concurrent_store_different_tenants_exactly_one_wins(
        self, db_engine: sa.Engine
    ) -> None:
        other_tenant = TenantId(uuid.uuid4())
        with db_engine.connect() as seed_conn:
            _seed_tenant(seed_conn, other_tenant, "race")
            seed_conn.commit()

        gid = StationGroupId(uuid.uuid4())
        barrier = threading.Barrier(2)
        outcomes: dict[str, object] = {}

        def _attempt(tenant_id: TenantId, name: str, key: str) -> None:
            with db_engine.connect() as conn:
                store = PgStationGroupStore(conn)
                barrier.wait(timeout=10)
                try:
                    store.store_group(
                        StationGroup(
                            id=gid,
                            name=name,
                            station_ids=frozenset(),
                            description=None,
                            created_at=_NOW,
                            tenant_id=tenant_id,
                        )
                    )
                    outcomes[key] = "ok"
                except ConfigurationError as exc:
                    outcomes[key] = exc

        t_a = threading.Thread(target=_attempt, args=(DEFAULT_TENANT_ID, "race-a", "a"))
        t_b = threading.Thread(target=_attempt, args=(other_tenant, "race-b", "b"))
        t_a.start()
        t_b.start()
        t_a.join(timeout=15)
        t_b.join(timeout=15)

        assert not t_a.is_alive() and not t_b.is_alive(), "race threads hung"
        assert set(outcomes) == {"a", "b"}

        oks = [k for k, v in outcomes.items() if v == "ok"]
        errs = [
            (k, v) for k, v in outcomes.items() if isinstance(v, ConfigurationError)
        ]
        assert len(oks) == 1, f"expected exactly one winner, got {outcomes}"
        assert len(errs) == 1, (
            f"expected exactly one ConfigurationError, got {outcomes}"
        )
        assert "immutable" in str(errs[0][1])

        winner_key = oks[0]
        winner_tenant = DEFAULT_TENANT_ID if winner_key == "a" else other_tenant
        winner_name = "race-a" if winner_key == "a" else "race-b"

        with db_engine.connect() as check_conn:
            row = check_conn.execute(
                sa.select(station_groups.c.tenant_id, station_groups.c.name).where(
                    station_groups.c.id == gid
                )
            ).one()
        # The loser's write never landed — persisted row matches the winner only.
        assert TenantId(row.tenant_id) == winner_tenant
        assert row.name == winner_name
