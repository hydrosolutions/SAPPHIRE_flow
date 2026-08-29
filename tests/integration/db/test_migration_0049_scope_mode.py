"""Plan 215 T4 — LOCKED upgrade/downgrade acceptance tests for migration
0049 (`access_tokens.scope_mode` column + its two CHECK constraints).

Real Alembic upgrade/downgrade against a throwaway PostGIS container
(mirrors ``tests/integration/db/test_migration_0045_0046_audit_log.py``).
Starts from a DB that already holds one consumer and one admin token row
(inserted under 0048, before 0049 exists) — the migration must backfill
both to ``scope_mode='stations'`` with no behaviour change, per D2.1.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Iterator

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_EXPIRES = ensure_utc(_NOW + timedelta(days=30))


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """Throwaway PostGIS container so a real Alembic upgrade/downgrade can
    mutate the schema without disturbing the shared session-scoped engine."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_215_test",
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


def _insert_token(
    conn: sa.Connection,
    *,
    token_id: uuid.UUID,
    role: str,
    tenant_id: uuid.UUID | None,
) -> None:
    """Raw INSERT via `sa.text` — pre-0049 the `access_tokens` table object
    imported from `db.metadata` already carries the new column/CHECKs
    (metadata.py is the target-state schema), so seeding through SQLAlchemy
    Core against a DB only migrated to 0048 would silently include a column
    that does not exist there yet. Raw SQL keeps this test honestly pinned
    to what the pre-0049 schema actually looks like."""
    conn.execute(
        sa.text(
            "INSERT INTO access_tokens "
            "(id, token_hash, key_prefix, name, role, tenant_id, "
            "pepper_version, expires_at, created_at) "
            "VALUES (:id, :token_hash, :key_prefix, :name, :role, :tenant_id, "
            "1, :expires_at, :created_at)"
        ),
        {
            "id": token_id,
            "token_hash": f"hash-{token_id.hex}",
            "key_prefix": f"pfx{token_id.hex[:8]}",
            "name": f"seed-{role}",
            "role": role,
            "tenant_id": tenant_id,
            "expires_at": _EXPIRES,
            "created_at": _NOW,
        },
    )


class TestMigration0049UpgradeDowngradeRoundTrip:
    def test_backfills_existing_rows_and_enforces_constraints(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)

        # 1. Upgrade to 0048 (one before this migration), seed a consumer +
        # an admin token row under the PRE-0049 schema.
        command.upgrade(cfg, "0048")

        # A real tenant row for the consumer token's FK.
        tenant_id = uuid.uuid4()
        consumer_id = uuid.uuid4()
        admin_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO tenants (id, code, name, created_at) "
                    "VALUES (:id, :code, :name, :created_at)"
                ),
                {
                    "id": tenant_id,
                    "code": f"t-{tenant_id.hex[:8]}",
                    "name": "Migration Test Tenant",
                    "created_at": _NOW,
                },
            )
            _insert_token(
                conn, token_id=consumer_id, role="consumer", tenant_id=tenant_id
            )
            _insert_token(conn, token_id=admin_id, role="admin", tenant_id=None)

        # 2. Upgrade to head (0049) — both rows must survive with
        # scope_mode='stations', no other behaviour change.
        # A station and an EXISTING materialised grant row, seeded under the
        # PRE-0049 schema. Without these the round trip below asserts only on
        # `access_tokens` rows and would not notice 0049 destroying existing
        # scope (independent Codex review) — 0049 must not touch
        # `access_token_stations` at all, in either direction.
        station_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO stations (id, code, name, location, station_kind, "
                    "timezone, measured_parameters, network, tenant_id) VALUES "
                    "(:id, :code, :name, ST_SetSRID(ST_MakePoint(7.0, 46.5), 4326), "
                    "'river', 'UTC', ARRAY['discharge'], 'bafu', :tenant_id)"
                ),
                {
                    "id": station_id,
                    "code": "9991",
                    "name": "migration-scope-fixture",
                    "tenant_id": tenant_id,
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO access_token_stations (token_id, station_id) "
                    "VALUES (:token_id, :station_id)"
                ),
                {"token_id": consumer_id, "station_id": station_id},
            )

        command.upgrade(cfg, "head")

        with engine.begin() as conn:
            rows = {
                row.id: row.scope_mode
                for row in conn.execute(
                    sa.text("SELECT id, scope_mode FROM access_tokens")
                ).all()
            }
        assert rows[consumer_id] == "stations"
        assert rows[admin_id] == "stations"

        # 3. The DB rejects scope_mode='tenant' on an admin row.
        with (
            engine.connect() as conn,
            pytest.raises(
                sa.exc.IntegrityError, match="ck_access_tokens_tenant_mode_is_consumer"
            ),
        ):
            conn.execute(
                sa.text(
                    "UPDATE access_tokens SET scope_mode = 'tenant' WHERE id = :id"
                ),
                {"id": admin_id},
            )
            conn.commit()

        # 4. The DB rejects an unknown scope_mode value entirely.
        with (
            engine.connect() as conn,
            pytest.raises(sa.exc.IntegrityError, match="ck_access_tokens_scope_mode"),
        ):
            conn.execute(
                sa.text(
                    "UPDATE access_tokens SET scope_mode = 'garbage' WHERE id = :id"
                ),
                {"id": consumer_id},
            )
            conn.commit()

        # A consumer row CAN move to tenant mode.
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "UPDATE access_tokens SET scope_mode = 'tenant' WHERE id = :id"
                ),
                {"id": consumer_id},
            )

        # 5. Downgrade removes both CHECKs then the column.
        command.downgrade(cfg, "0048")
        with engine.begin() as conn:
            inspector = sa.inspect(conn)
            columns = {c["name"] for c in inspector.get_columns("access_tokens")}
            assert "scope_mode" not in columns

            # Both rows survive the downgrade (rows are never dropped, only
            # the column).
            surviving = {
                row.id
                for row in conn.execute(sa.text("SELECT id FROM access_tokens")).all()
            }
            assert surviving == {consumer_id, admin_id}

        # 6. Re-upgrading from the downgraded state succeeds cleanly and
        # both rows land back on 'stations' (the column was dropped, so the
        # prior 'tenant' value on the consumer row is gone — this is the
        # downgrade-then-upgrade round trip the plan's exit criteria list).
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            rows = {
                row.id: row.scope_mode
                for row in conn.execute(
                    sa.text("SELECT id, scope_mode FROM access_tokens")
                ).all()
            }
        assert rows == {consumer_id: "stations", admin_id: "stations"}

        # The pre-existing materialised scope survives upgrade -> downgrade ->
        # re-upgrade untouched. This is the assertion that makes the round trip
        # discriminating: 0049 changes `access_tokens` only.
        with engine.begin() as conn:
            granted = [
                row.station_id
                for row in conn.execute(
                    sa.text(
                        "SELECT station_id FROM access_token_stations "
                        "WHERE token_id = :id"
                    ),
                    {"id": consumer_id},
                ).all()
            ]
        assert granted == [station_id]
