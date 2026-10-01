from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from queue import Queue
from time import monotonic
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from sapphire_flow.db.metadata import (
    measurement_feed_evidence,
    provisional_discharge_permissions,
    provisional_discharges,
    rating_curves,
    rating_reference_proofs,
    tenants,
)
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.types.ids import TenantId
from tests.integration.store.test_provisional_discharge_store import (
    NOW,
    convert,
    permit_fixture,
    raw_values,
    seed,
    seed_reference,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.provisional_discharge import ProvisionalDischarge
    from sapphire_flow.types.rating_curve import RatingCurve


@pytest.fixture(scope="module")
def isolation_engine() -> Iterator[sa.Engine]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="provisional_isolation",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            command.upgrade(Config("alembic.ini"), "head")
            yield engine
        finally:
            engine.dispose()
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous


def seed_case(engine: sa.Engine) -> tuple[ProvisionalDischarge, RatingCurve]:
    with engine.begin() as conn:
        tenant_id = TenantId(uuid4())
        conn.execute(
            sa.insert(tenants).values(
                id=tenant_id, code=tenant_id.hex, name="isolation fixture"
            )
        )
        args = seed(conn, tenant_id=tenant_id)
        result = convert(*args)
        permit_fixture(conn, tenant_id)
    return result, args[1]


def append(conn: sa.Connection, result: ProvisionalDischarge, writer: str) -> None:
    if writer == "store":
        PgProvisionalDischargeStore(conn).store_provisional_discharge(
            result, captured_at=NOW
        )
    else:
        conn.execute(
            sa.insert(provisional_discharges).values(
                **raw_values(result), captured_at=NOW
            )
        )


def change_parent(
    conn: sa.Connection, result: ProvisionalDischarge, curve: RatingCurve, change: str
) -> None:
    if change == "disable":
        conn.execute(
            sa.update(provisional_discharge_permissions)
            .where(provisional_discharge_permissions.c.tenant_id == result.tenant_id)
            .values(state="disabled")
        )
    else:
        PgRatingCurveStore(conn).store_rating_curve(
            replace(
                curve,
                id=uuid4(),
                version=2,
                valid_from=curve.valid_from + timedelta(days=1),
            )
        )


def establish_snapshot(conn: sa.Connection, result: ProvisionalDischarge) -> None:
    assert (
        conn.scalar(
            sa.select(provisional_discharge_permissions.c.state).where(
                provisional_discharge_permissions.c.tenant_id == result.tenant_id
            )
        )
        == "enabled"
    )
    assert (
        conn.scalar(
            sa.select(sa.func.count())
            .select_from(rating_curves)
            .where(rating_curves.c.station_id == result.station_id)
        )
        == 1
    )


def assert_no_append(engine: sa.Engine, result: ProvisionalDischarge) -> None:
    with engine.connect() as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(provisional_discharges)
                .where(provisional_discharges.c.station_id == result.station_id)
            )
            == 0
        )


class TestProtectedWriteIsolation:
    @pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
    @pytest.mark.parametrize("writer", ["store", "sql"])
    @pytest.mark.parametrize("change", ["disable", "curve"])
    def test_fixed_snapshot_cannot_commit_after_parent_change(
        self,
        isolation_engine: sa.Engine,
        isolation: str,
        writer: str,
        change: str,
    ) -> None:
        result, curve = seed_case(isolation_engine)
        with isolation_engine.connect().execution_options(
            isolation_level=isolation
        ) as stale:
            transaction = stale.begin()
            try:
                establish_snapshot(stale, result)
                with isolation_engine.begin() as owner:
                    change_parent(owner, result, curve, change)
                error = ValueError if writer == "store" else sa.exc.DBAPIError
                with pytest.raises(error, match="READ COMMITTED"):
                    append(stale, result, writer)
                    transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()
        assert_no_append(isolation_engine, result)

    @pytest.mark.parametrize("writer", ["store", "sql"])
    @pytest.mark.parametrize("change", ["disable", "curve"])
    def test_read_committed_rechecks_previously_read_parent(
        self,
        isolation_engine: sa.Engine,
        writer: str,
        change: str,
    ) -> None:
        result, curve = seed_case(isolation_engine)
        with isolation_engine.connect().execution_options(
            isolation_level="READ COMMITTED"
        ) as conn:
            establish_snapshot(conn, result)
            with isolation_engine.begin() as owner:
                change_parent(owner, result, curve, change)
            with pytest.raises(
                (ValueError, sa.exc.DBAPIError), match="disabled|curve|disagreement"
            ):
                append(conn, result, writer)
        assert_no_append(isolation_engine, result)

    @pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
    @pytest.mark.parametrize(
        "table", ["measurement_feed_evidence", "rating_reference_proofs"]
    )
    def test_attestation_inserts_require_read_committed(
        self,
        isolation_engine: sa.Engine,
        isolation: str,
        table: str,
    ) -> None:
        result, _ = seed_case(isolation_engine)
        relation = (
            measurement_feed_evidence
            if table == "measurement_feed_evidence"
            else rating_reference_proofs
        )
        from sapphire_flow.store.rating_reference_store import PgRatingReferenceStore

        with isolation_engine.connect().execution_options(
            isolation_level=isolation
        ) as conn:
            store = PgRatingReferenceStore(conn)
            evidence = (
                store.fetch_feed_evidence(result.feed_evidence_id)
                if table == "measurement_feed_evidence"
                else store.fetch_reference_proof(result.reference_proof_id)
            )
            assert evidence is not None
            with pytest.raises(sa.exc.DBAPIError, match="READ COMMITTED"):
                seed_reference(conn, relation, replace(evidence, id=uuid4()))

    @pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
    @pytest.mark.parametrize("operation", ["insert", "disable"])
    def test_permission_writes_require_read_committed(
        self,
        isolation_engine: sa.Engine,
        isolation: str,
        operation: str,
    ) -> None:
        result, _ = seed_case(isolation_engine)
        with (
            isolation_engine.connect().execution_options(
                isolation_level=isolation
            ) as conn,
            pytest.raises(sa.exc.DBAPIError, match="READ COMMITTED"),
        ):
            if operation == "disable":
                conn.execute(
                    sa.update(provisional_discharge_permissions)
                    .where(
                        provisional_discharge_permissions.c.tenant_id
                        == result.tenant_id
                    )
                    .values(state="disabled")
                )
            else:
                conn.execute(
                    sa.insert(provisional_discharge_permissions).values(
                        tenant_id=result.tenant_id
                    )
                )


def wait_for_database_lock(
    observer: sa.Connection, blocked_pid: int, blocker_pid: int
) -> None:
    # Observe an actual server wait, not a sleep or a guessed client-side delay.
    deadline = monotonic() + 10
    while monotonic() < deadline:
        blockers = observer.scalar(
            sa.text("SELECT pg_blocking_pids(:pid)"), {"pid": blocked_pid}
        )
        if blocker_pid in blockers:
            return
    raise AssertionError("writer did not reach the expected database lock")


def append_in_thread(
    engine: sa.Engine, result: ProvisionalDischarge, writer: str, started: Queue[int]
) -> str | None:
    with engine.connect() as conn:
        started.put(conn.scalar(sa.text("SELECT pg_backend_pid()")))
        conn.execute(sa.text("SET LOCAL statement_timeout = '15s'"))
        try:
            append(conn, result, writer)
            conn.commit()
            return None
        except (ValueError, sa.exc.DBAPIError) as exc:
            conn.rollback()
            return str(exc)


class TestReadCommittedLockVisibility:
    @pytest.mark.parametrize("writer", ["store", "sql"])
    @pytest.mark.parametrize("change", ["disable", "curve"])
    def test_waiting_writer_sees_committed_disable_or_newest_curve(
        self,
        isolation_engine: sa.Engine,
        writer: str,
        change: str,
    ) -> None:
        result, curve = seed_case(isolation_engine)
        started: Queue[int] = Queue()
        with (
            isolation_engine.connect() as owner,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            transaction = owner.begin()
            try:
                owner_pid = owner.scalar(sa.text("SELECT pg_backend_pid()"))
                change_parent(owner, result, curve, change)
                outcome = pool.submit(
                    append_in_thread, isolation_engine, result, writer, started
                )
                writer_pid = started.get(timeout=10)
                with isolation_engine.connect() as observer:
                    wait_for_database_lock(observer, writer_pid, owner_pid)
                transaction.commit()
                error = outcome.result(timeout=10)
                assert error is not None
                assert (
                    "disabled" in error
                    if change == "disable"
                    else ("curve" in error or "disagreement" in error)
                )
            finally:
                if transaction.is_active:
                    transaction.rollback()
        assert_no_append(isolation_engine, result)
