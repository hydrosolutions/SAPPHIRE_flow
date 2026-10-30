"""0071 ACL round trip with live runtime temp objects in a fresh cluster."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from alembic import command
from tests.integration.db.historical_seeds import assert_revision

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
            command.upgrade(Config("alembic.ini"), "0070")
            with engine.begin() as conn:
                for role in ("sapphire_api", "sapphire_worker"):
                    conn.execute(
                        sa.text(
                            f"CREATE ROLE {role} LOGIN PASSWORD 'historical-test-pw'"
                        )
                    )
                    conn.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {role}"))
                    conn.execute(sa.text(f"GRANT SELECT(id) ON stations TO {role}"))
            command.upgrade(Config("alembic.ini"), "0071")
            assert_revision(engine, "0071")
            yield engine
        finally:
            engine.dispose()


@pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
@pytest.mark.parametrize("kind", ["opaque", "parsed", "view"])
def test_live_temp_objects_survive_historical_acl_roundtrip(
    historical_db: sa.Engine,
    role: str,
    kind: str,
) -> None:
    engine = sa.create_engine(
        historical_db.url.set(username=role, password="historical-test-pw"),
        poolclass=NullPool,
    )
    statements = {
        "opaque": "CREATE FUNCTION pg_temp.runtime_probe() RETURNS text "
        "LANGUAGE sql SECURITY DEFINER AS 'SELECT current_user::text'",
        "parsed": "CREATE FUNCTION pg_temp.runtime_probe() RETURNS SETOF text "
        "LANGUAGE sql SECURITY DEFINER BEGIN ATOMIC "
        "SELECT input_lineage FROM public.forecasts; END",
        "view": "CREATE TEMP VIEW runtime_probe AS SELECT "
        "input_lineage FROM public.forecasts",
    }
    try:
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT session_user")) == role
            conn.execute(sa.text(statements[kind]))
            conn.commit()
            for index, revision in enumerate(("0071", "0070", "0071")):
                if revision == "0070":
                    command.downgrade(Config("alembic.ini"), revision)
                elif index == 2:
                    command.upgrade(Config("alembic.ini"), revision)
                assert_revision(historical_db, revision)
                conn.execute(sa.text("SELECT id FROM stations LIMIT 1"))
                conn.execute(sa.text("SELECT id FROM forecasts LIMIT 1"))
                conn.commit()
                with (
                    pytest.raises(sa.exc.DBAPIError, match="permission denied"),
                    conn.begin_nested(),
                ):
                    conn.execute(sa.text("SELECT input_lineage FROM forecasts"))
                conn.rollback()
                if kind != "opaque":
                    with (
                        pytest.raises(sa.exc.DBAPIError, match="permission denied"),
                        conn.begin_nested(),
                    ):
                        conn.execute(
                            sa.text(
                                "SELECT * FROM pg_temp.runtime_probe"
                                + ("()" if kind == "parsed" else "")
                            )
                        )
                    conn.rollback()
                else:
                    assert (
                        conn.scalar(sa.text("SELECT pg_temp.runtime_probe()")) == role
                    )
                    conn.commit()
    finally:
        engine.dispose()
