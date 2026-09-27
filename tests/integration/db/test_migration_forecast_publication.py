from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from sapphire_flow.store.forecast_store import PgForecastStore
from tests.integration.store.test_forecast_store import (
    _make_forecast,
    _seed_artifact,
    _seed_model,
    _seed_station,
    savepoint_factory,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_publication_migration_test",
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


def test_additive_publication_migration_and_rollback(
    migration_engine: tuple[sa.Engine, str],
) -> None:
    engine, url = migration_engine
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0062")
    with engine.begin() as conn:
        station_id = _seed_station(conn)
        model_id = _seed_model(conn)
        artifact_id = _seed_artifact(conn, station_id, model_id)
        forecast = _make_forecast(station_id, model_id, artifact_id)
        PgForecastStore(
            conn, transaction_factory=savepoint_factory(conn)
        ).store_forecast(forecast)
    with engine.connect() as conn:
        old_forecast_columns = {
            column["name"] for column in sa.inspect(conn).get_columns("forecasts")
        }
        assert (
            "forecast_publication_decisions" not in sa.inspect(conn).get_table_names()
        )

    command.upgrade(config, "0063")
    with engine.connect() as conn:
        assert old_forecast_columns == {
            column["name"] for column in sa.inspect(conn).get_columns("forecasts")
        }
        assert PgForecastStore(conn).fetch_forecast(forecast.id) is not None
        assert {
            "forecast_publication_decisions",
            "forecast_publication_selections",
            "forecast_publication_events",
            "forecast_publication_sequence",
            "protected_backup_health",
            "protected_backup_forecast_proofs",
        }.issubset(sa.inspect(conn).get_table_names())
        assert (
            conn.scalar(
                sa.text(
                    "SELECT next_value FROM forecast_publication_sequence WHERE id=1"
                )
            )
            == 1
        )
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname LIKE "
                    "'trg_forecast_publication_%'"
                )
            )
            == 5
        )

    command.downgrade(config, "0062")
    with engine.connect() as conn:
        assert (
            "forecast_publication_decisions" not in sa.inspect(conn).get_table_names()
        )
        assert old_forecast_columns == {
            column["name"] for column in sa.inspect(conn).get_columns("forecasts")
        }
