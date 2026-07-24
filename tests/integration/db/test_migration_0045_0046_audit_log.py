"""Plan 147 Slice B — LOCKED upgrade/downgrade acceptance tests for
migrations 0045 (audit_log table) and 0046 (append-only guard trigger).

Real Alembic upgrades/downgrades against a throwaway PostGIS container
(mirrors ``tests/integration/db/test_migration_0041_0044_tenant_model.py``).
Reviewer finding (Codex, post-implementation review): the committed suite
only ever ran ``upgrade head`` — no test exercised ``downgrade`` through
0046/0045, so a broken trigger/function drop or table downgrade could pass
the complete added suite. This file closes that gap, matching the pattern
already used for migrations 0041-0044 (F14: every migration has a tested
downgrade).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.db.metadata import audit_log
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Iterator

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """Throwaway PostGIS container so a real Alembic upgrade/downgrade can
    mutate the schema without disturbing the shared session-scoped engine."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_147b_test",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            yield engine, url
        finally:
            engine.dispose()
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior


def _alembic_cfg(url: str) -> object:
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


class TestMigration0045And0046UpgradeDowngradeRoundTrip:
    def test_upgrade_creates_table_and_guards_then_downgrade_removes_them(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)

        # 1. Upgrade to head (0044 -> 0045 -> 0046).
        command.upgrade(cfg, "head")

        with engine.begin() as conn:
            inspector = sa.inspect(conn)
            assert "audit_log" in inspector.get_table_names()
            conn.execute(
                sa.insert(audit_log).values(
                    event_type="model_promoted",
                    actor_type="system",
                    actor_id=None,
                    created_at=_NOW,
                )
            )

        # The append-only guard (0046) rejects UPDATE/DELETE/TRUNCATE even
        # for the table-owning migration role.
        with (
            engine.connect() as conn,
            pytest.raises(sa.exc.DBAPIError, match="append-only"),
        ):
            conn.execute(sa.update(audit_log).values(event_type="model_rejected"))
            conn.commit()

        with (
            engine.connect() as conn,
            pytest.raises(sa.exc.DBAPIError, match="append-only"),
        ):
            conn.execute(sa.delete(audit_log))
            conn.commit()

        # The pairing CHECK constraint (0045) rejects an invalid combination.
        with engine.connect() as conn, pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.insert(audit_log).values(
                    event_type="model_promoted",
                    actor_type="system",
                    actor_id=uuid.uuid4(),
                    created_at=_NOW,
                )
            )
            conn.commit()

        # 2. Downgrade all the way through 0046 and 0045.
        command.downgrade(cfg, "0044")

        with engine.begin() as conn:
            inspector = sa.inspect(conn)
            assert "audit_log" not in inspector.get_table_names()

            functions = conn.execute(
                sa.text(
                    "SELECT proname FROM pg_proc "
                    "WHERE proname = 'reject_audit_log_mutation'"
                )
            ).all()
            assert functions == []

        # 3. Re-upgrading from the downgraded state must succeed cleanly —
        # proves the downgrade left no orphaned trigger/function/constraint
        # behind that would collide with a fresh upgrade.
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            inspector = sa.inspect(conn)
            assert "audit_log" in inspector.get_table_names()
