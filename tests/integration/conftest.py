from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

# Re-export make_historical_forcing_record from root conftest
from tests.conftest import (
    make_historical_forcing_record as make_historical_forcing_record,
)


@pytest.fixture(scope="session")
def db_engine():
    """Start a PostGIS container, run Alembic migrations, return engine."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_test",
    ) as postgres:
        url = postgres.get_connection_url()
        # testcontainers returns psycopg2 URL, use psycopg v3 driver instead
        url = url.replace("+psycopg2", "+psycopg")

        os.environ["DATABASE_URL"] = url

        engine = sa.create_engine(url)

        # Run Alembic migrations
        from alembic.config import Config

        from alembic import command

        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(alembic_cfg, "head")

        yield engine

        engine.dispose()


@pytest.fixture
def db_connection(db_engine: sa.Engine):
    """Per-test connection with transaction rollback for isolation."""
    with db_engine.connect() as conn:
        trans = conn.begin()
        yield conn
        trans.rollback()
