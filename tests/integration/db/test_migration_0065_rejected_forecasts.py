"""Plan 404 T1 — migration 0065: `rejected_forecasts`, append-only."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

if TYPE_CHECKING:
    from collections.abc import Iterator

from alembic import command


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_rejected_forecasts_migration_test",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            yield engine, url
        finally:
            engine.dispose()
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous


def test_migration_upgrade_and_downgrade(
    migration_engine: tuple[sa.Engine, str],
) -> None:
    engine, url = migration_engine
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "0064")
    with engine.connect() as connection:
        assert "rejected_forecasts" not in sa.inspect(connection).get_table_names()

    command.upgrade(config, "0065")
    with engine.connect() as connection:
        assert "rejected_forecasts" in sa.inspect(connection).get_table_names()
        triggers = (
            connection.execute(
                sa.text(
                    "SELECT tgname FROM pg_trigger WHERE tgname LIKE "
                    "'trg_rejected_forecasts%append_only%'"
                )
            )
            .scalars()
            .all()
        )
        assert len(triggers) == 2
        indexes = sa.inspect(connection).get_indexes("rejected_forecasts")
        assert any(ix["column_names"] == ["station_id", "issued_at"] for ix in indexes)

    command.downgrade(config, "0064")
    with engine.connect() as connection:
        assert "rejected_forecasts" not in sa.inspect(connection).get_table_names()
        function_count = connection.scalar(
            sa.text(
                "SELECT count(*) FROM pg_proc "
                "WHERE proname = 'reject_rejected_forecast_mutation'"
            )
        )
        assert function_count == 0
