from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from sapphire_flow.store.rating_reference_store import PgProtectedReferenceWriter
from sapphire_flow.types.ids import RatingReferenceProofId
from tests.integration.db.test_migration_0065_rejected_forecasts import (
    migration_engine as migration_engine,
)
from tests.integration.store.test_provisional_discharge_store import seed
from tests.unit.types.test_rating_reference import assumed_reference


class TestReferenceBasisMigration:
    def test_populated_genuine_legacy_upgrade_preserves_bytes(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "0072")
        with engine.begin() as conn:
            from sapphire_flow.db.metadata import provisional_discharges
            from tests.integration.store.test_provisional_discharge_store import (
                NOW,
                convert,
                permit_fixture,
                raw_values,
            )

            result = convert(*seed(conn))
            permit_fixture(conn, result.tenant_id)
            conn.execute(
                sa.insert(provisional_discharges).values(
                    **raw_values(result), captured_at=NOW
                )
            )
            provisional_before = conn.execute(
                sa.text("SELECT content, fingerprint FROM provisional_discharges")
            ).all()
            function_sql = sa.text(
                "SELECT proname, pg_get_functiondef(oid), proconfig, proacl "
                "FROM pg_proc WHERE pronamespace = 'public'::regnamespace "
                "AND proname IN ('provisional_reference_insert', "
                "'provisional_discharge_insert') "
                "ORDER BY proname"
            )
            functions_before = conn.execute(function_sql).all()
            before = conn.execute(
                sa.text("SELECT id, content, fingerprint FROM rating_reference_proofs")
            ).all()
            assert "reference_basis" not in {
                c["name"]
                for c in sa.inspect(conn).get_columns("rating_reference_proofs")
            }
        command.upgrade(cfg, "0073")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    sa.text(
                        "SELECT id, content, fingerprint FROM rating_reference_proofs"
                    )
                ).all()
                == before
            )
            assert (
                conn.scalar(
                    sa.text("SELECT reference_basis FROM rating_reference_proofs")
                )
                == "verified"
            )
            columns = {
                c["name"]: c
                for c in sa.inspect(conn).get_columns("rating_reference_proofs")
            }
            assert columns["reference_basis"]["default"] is None
            from sapphire_flow.store.provisional_discharge_store import (
                PgProvisionalDischargeStore,
            )

            assert (
                PgProvisionalDischargeStore(conn).fetch_provisional_discharge(
                    result.fingerprint
                )
                == result
            )
            assert (
                conn.execute(
                    sa.text("SELECT content, fingerprint FROM provisional_discharges")
                ).all()
                == provisional_before
            )
        command.downgrade(cfg, "0072")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    sa.text(
                        "SELECT id, content, fingerprint FROM rating_reference_proofs"
                    )
                ).all()
                == before
            )
            assert conn.execute(function_sql).all() == functions_before
            assert (
                conn.execute(
                    sa.text("SELECT content, fingerprint FROM provisional_discharges")
                ).all()
                == provisional_before
            )
        command.upgrade(cfg, "0073")

    def test_fresh_upgrade_has_no_default(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0073")
        with engine.connect() as conn:
            columns = {
                c["name"]: c
                for c in sa.inspect(conn).get_columns("rating_reference_proofs")
            }
            assert columns["reference_basis"]["default"] is None
            assert columns["reference_basis"]["nullable"] is False

    def test_downgrade_refuses_assumed_evidence(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0073")
        with engine.begin() as conn:
            reference = replace(
                assumed_reference(seed(conn)[3]), id=RatingReferenceProofId(uuid4())
            )
            PgProtectedReferenceWriter(conn).append_reference_evidence(reference)
        with pytest.raises(sa.exc.DBAPIError, match="downgrade refused"):
            command.downgrade(Config("alembic.ini"), "0072")

    def test_downgrade_waits_for_inflight_assumption_then_refuses(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from queue import Queue

        from tests.integration.store.test_provisional_discharge_isolation import (
            wait_for_database_lock,
        )

        engine, _ = migration_engine
        command.upgrade(Config("alembic.ini"), "0073")
        with engine.begin() as conn:
            reference = replace(
                assumed_reference(seed(conn)[3]), id=RatingReferenceProofId(uuid4())
            )
        started: Queue[int] = Queue()

        def observe_lock(
            conn: sa.Connection,
            cursor: object,
            statement: str,
            parameters: object,
            context: sa.ExecutionContext,
            executemany: bool,
        ) -> None:
            if statement.startswith("LOCK TABLE public.rating_reference_proofs"):
                started.put(conn.scalar(sa.text("SELECT pg_backend_pid()")))

        def downgrade() -> None:
            with pytest.raises(sa.exc.DBAPIError, match="downgrade refused"):
                command.downgrade(Config("alembic.ini"), "0072")

        sa.event.listen(sa.Engine, "before_cursor_execute", observe_lock)
        try:
            with engine.connect() as writer, ThreadPoolExecutor(max_workers=1) as pool:
                tx = writer.begin()
                try:
                    pid = writer.scalar(sa.text("SELECT pg_backend_pid()"))
                    PgProtectedReferenceWriter(writer).append_reference_evidence(
                        reference
                    )
                    outcome = pool.submit(downgrade)
                    blocked = started.get(timeout=10)
                    with engine.connect() as observer:
                        wait_for_database_lock(observer, blocked, pid)
                    tx.commit()
                    outcome.result(timeout=20)
                finally:
                    if tx.is_active:
                        tx.rollback()
        finally:
            sa.event.remove(sa.Engine, "before_cursor_execute", observe_lock)
