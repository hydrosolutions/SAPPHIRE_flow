"""Plan 401 T1 — upgrade/downgrade acceptance tests for migration 0061
(the `reviewer` access-token role).

Real Alembic upgrade/downgrade against a throwaway PostGIS container
(mirrors ``test_migration_0049_scope_mode.py``). Rows are inserted with raw
SQL so the database constraints — not ``AccessToken.__post_init__`` — are
what is exercised.
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

from alembic import command

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_EXPIRES = ensure_utc(_NOW + timedelta(days=30))
_PRIOR = "0060"
_CONSTRAINTS = (
    "ck_access_tokens_role",
    "ck_access_tokens_role_tenant",
    "ck_access_tokens_tenant_mode_is_consumer",
)
_CONSTRAINT_DEFS = sa.text(
    "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
    "WHERE conrelid = 'access_tokens'::regclass AND conname = ANY(:names)"
)
# Quoted, so it does not also match `ck_access_tokens_role_tenant`.
_ROLE_CHECK = '"ck_access_tokens_role"'
_OPERATOR_DELETE_STATEMENTS = (
    "DELETE FROM access_token_stations\n"
    "  WHERE token_id IN (SELECT id FROM access_tokens WHERE role = 'reviewer');\n"
    "DELETE FROM access_tokens WHERE role = 'reviewer';"
)


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_401_test",
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


def _constraint_defs(engine: sa.Engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(_CONSTRAINT_DEFS, {"names": list(_CONSTRAINTS)}).all()
    return {row[0]: row[1] for row in rows}


def _seed_tenant(engine: sa.Engine) -> uuid.UUID:
    tenant_id = uuid.uuid4()
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
    return tenant_id


def _seed_station(engine: sa.Engine, tenant_id: uuid.UUID) -> uuid.UUID:
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
                "code": "9401",
                "name": "migration-401-fixture",
                "tenant_id": tenant_id,
            },
        )
    return station_id


def _insert_token(
    engine: sa.Engine,
    *,
    role: str,
    tenant_id: uuid.UUID | None,
    scope_mode: str = "stations",
) -> uuid.UUID:
    token_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO access_tokens "
                "(id, token_hash, key_prefix, name, role, tenant_id, "
                "pepper_version, expires_at, created_at, scope_mode) "
                "VALUES (:id, :token_hash, :key_prefix, :name, :role, :tenant_id, "
                "1, :expires_at, :created_at, :scope_mode)"
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
                "scope_mode": scope_mode,
            },
        )
    return token_id


class TestMigration0061Upgrade:
    def test_reviewer_rows_are_tenant_bound_and_may_use_tenant_mode(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, _PRIOR)
        tenant_id = _seed_tenant(engine)

        command.upgrade(cfg, "head")

        _insert_token(engine, role="reviewer", tenant_id=tenant_id)
        _insert_token(engine, role="reviewer", tenant_id=tenant_id, scope_mode="tenant")
        with pytest.raises(sa.exc.IntegrityError, match="ck_access_tokens_role_tenant"):
            _insert_token(engine, role="reviewer", tenant_id=None)
        with pytest.raises(
            sa.exc.IntegrityError, match="ck_access_tokens_tenant_mode_is_consumer"
        ):
            _insert_token(engine, role="admin", tenant_id=None, scope_mode="tenant")
        with pytest.raises(sa.exc.IntegrityError, match=_ROLE_CHECK):
            _insert_token(engine, role="operator", tenant_id=tenant_id)

        with engine.connect() as conn:
            roles = sorted(
                conn.execute(sa.text("SELECT role FROM access_tokens")).scalars()
            )
        assert roles == ["reviewer", "reviewer"]


class TestMigration0061Downgrade:
    def test_refuses_while_a_reviewer_row_exists_even_revoked(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")
        tenant_id = _seed_tenant(engine)
        station_id = _seed_station(engine, tenant_id)
        reviewer_id = _insert_token(engine, role="reviewer", tenant_id=tenant_id)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO access_token_stations (token_id, station_id) "
                    "VALUES (:token_id, :station_id)"
                ),
                {"token_id": reviewer_id, "station_id": station_id},
            )

        with pytest.raises(RuntimeError, match="reviewer") as refused:
            command.downgrade(cfg, _PRIOR)
        assert _OPERATOR_DELETE_STATEMENTS in str(refused.value)
        assert "psql -U ${DB_USER:-sapphire} -d sapphire" in str(refused.value)

        with engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE access_tokens SET disabled_at = :now WHERE id = :id"),
                {"now": _NOW, "id": reviewer_id},
            )
        with pytest.raises(RuntimeError, match="reviewer"):
            command.downgrade(cfg, _PRIOR)

        with engine.connect() as conn:
            head = conn.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert head != _PRIOR

    def test_succeeds_without_reviewer_rows_and_restores_prior_constraints(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, _PRIOR)
        prior_defs = _constraint_defs(engine)
        assert set(prior_defs) == set(_CONSTRAINTS)

        command.upgrade(cfg, "head")
        tenant_id = _seed_tenant(engine)
        station_id = _seed_station(engine, tenant_id)
        reviewer_id = _insert_token(engine, role="reviewer", tenant_id=tenant_id)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO access_token_stations (token_id, station_id) "
                    "VALUES (:token_id, :station_id)"
                ),
                {"token_id": reviewer_id, "station_id": station_id},
            )
            # The operator step the refusal prints, run as the owner role.
            conn.exec_driver_sql(_OPERATOR_DELETE_STATEMENTS)

        command.downgrade(cfg, _PRIOR)

        with pytest.raises(sa.exc.IntegrityError, match=_ROLE_CHECK):
            _insert_token(engine, role="reviewer", tenant_id=tenant_id)
        assert _constraint_defs(engine) == prior_defs
