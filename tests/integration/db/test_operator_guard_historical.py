"""Exercise 0067→0066 in fresh clusters, never through later irreversible guards.

Only migration-era SQL grants/probes are used. No current store or role bootstrap
runs here; cluster-wide operator roles cannot affect the current-head harness.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from alembic import command

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def historical_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.Engine]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", url)
        engine = sa.create_engine(url, poolclass=NullPool)
        try:
            command.upgrade(Config("alembic.ini"), "0067")
            yield engine
        finally:
            engine.dispose()


def _state(engine: sa.Engine) -> tuple[object, ...]:
    with engine.connect() as conn:
        return (
            conn.scalar(sa.text("SELECT version_num FROM alembic_version")),
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname LIKE "
                    "'%operator_guard%'"
                )
            ),
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_proc WHERE proname LIKE 'operator_guard%'"
                )
            ),
        )


def _dml(engine: sa.Engine) -> list[tuple[str, str]]:
    with engine.connect() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                sa.text(
                    "SELECT t.name, p.name FROM "
                    "(VALUES ('observations'), ('rating_curves'), "
                    "('stations')) t(name) "
                    "CROSS JOIN (VALUES ('INSERT'), ('UPDATE'), ('DELETE'), "
                    "('TRUNCATE')) "
                    "p(name) WHERE has_table_privilege('sapphire_operator', "
                    "t.name, p.name) "
                    "ORDER BY 1, 2"
                )
            )
        ]


class TestHistoricalGuardMigrationDowngrade:
    def test_active_operator_loses_dml_before_the_guard_is_removed(
        self, historical_db: sa.Engine
    ) -> None:
        assert _state(historical_db) == ("0067", 10, 3)
        with historical_db.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE ROLE sapphire_operator LOGIN PASSWORD 'historical-test-pw'"
                )
            )
            conn.execute(sa.text("GRANT USAGE ON SCHEMA public TO sapphire_operator"))
            conn.execute(
                sa.text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE "
                    "ON observations, rating_curves, stations TO sapphire_operator"
                )
            )
            # Include the archive dependency so the TRUNCATE reaches the guard.
            conn.execute(
                sa.text("GRANT TRUNCATE ON observation_versions TO sapphire_operator")
            )
        operator = sa.create_engine(
            historical_db.url.set(
                username="sapphire_operator", password="historical-test-pw"
            ),
            poolclass=NullPool,
        )
        try:
            with operator.connect() as active:
                assert (
                    active.scalar(sa.text("SELECT session_user")) == "sapphire_operator"
                )
                active.commit()
                assert len(_dml(historical_db)) == 12
                # The migration-era guard is active, independently of table grants.
                with pytest.raises(ProgrammingError, match="may not TRUNCATE"):
                    active.execute(
                        sa.text("TRUNCATE observations, observation_versions")
                    )
                active.rollback()
                command.downgrade(Config("alembic.ini"), "0066")
                assert _state(historical_db) == ("0066", 0, 0)
                assert _dml(historical_db) == []
                assert active.scalar(sa.text("SELECT 1")) == 1
                with pytest.raises(ProgrammingError, match="permission denied"):
                    active.execute(sa.text("DELETE FROM observations"))
                active.rollback()
            command.upgrade(Config("alembic.ini"), "0067")
            assert _state(historical_db) == ("0067", 10, 3)
            # Reinstalling triggers must not silently restore privileges.
            assert _dml(historical_db) == []
        finally:
            operator.dispose()

    def test_no_operator_role_is_required_to_remove_and_restore_the_guard(
        self, historical_db: sa.Engine
    ) -> None:
        with historical_db.connect() as conn:
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_roles WHERE "
                        "rolname='sapphire_operator'"
                    )
                )
                == 0
            )
        assert _state(historical_db) == ("0067", 10, 3)
        command.downgrade(Config("alembic.ini"), "0066")
        assert _state(historical_db) == ("0066", 0, 0)
        command.upgrade(Config("alembic.ini"), "0067")
        assert _state(historical_db) == ("0067", 10, 3)
