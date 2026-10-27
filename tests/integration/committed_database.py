"""Disposable committed-write database, separate from rollback-based store tests."""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command


@pytest.fixture(scope="session")
def isolated_committed_db_engine() -> Iterator[sa.Engine]:
    """Share one extra container; committed rows never enter the rollback database."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_committed_test",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        # Alembic reads the environment, even when its Config supplies a URL.
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", url)
            command.upgrade(Config("alembic.ini"), "head")
        engine = sa.create_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()
