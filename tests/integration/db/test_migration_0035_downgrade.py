from __future__ import annotations

import os
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from tests.integration.store.test_forecast_store import (
    _NOW,
    _seed_rating_curve,
    _seed_station,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """Throwaway container so a real Alembic downgrade can mutate the schema
    without disturbing the session-scoped integration engine."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_test",
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


class TestMigration0035Downgrade:
    """Plan 035 Task 2: migration 0035 must stay reversible even after
    rating-curve-derived observations exist. The upgrade widens the
    ``observations.source`` CHECK to admit ``'rating_curve_derived'`` /
    ``'component_derived'``; the downgrade must coerce those rows back to
    ``'measured'`` BEFORE restoring the two-value CHECK.

    RED before the downgrade coercion: re-creating the two-value CHECK raises a
    violation on the rating-curve-derived row, so the downgrade aborts.
    """

    def test_downgrade_coerces_new_source_rows(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        observation_id = uuid4()
        with engine.begin() as conn:
            station_id = _seed_station(conn)
            curve_id = _seed_rating_curve(conn, station_id)
            conn.execute(
                sa.text(
                    "INSERT INTO observations "
                    "(id, station_id, timestamp, parameter, value, source, "
                    "rating_curve_id, rating_curve_correction_version, qc_status) "
                    "VALUES (:id, :sid, :ts, 'discharge', 12.3, "
                    "'rating_curve_derived', :cid, 'corr-v1', 'raw')"
                ),
                {
                    "id": observation_id,
                    "sid": station_id,
                    "ts": _NOW,
                    "cid": curve_id,
                },
            )

        # The reversibility contract: this must NOT raise despite the
        # rating-curve-derived row that only migration 0035 made representable.
        command.downgrade(cfg, "0034")

        with engine.connect() as conn:
            (source,) = conn.execute(
                sa.text("SELECT source FROM observations WHERE id = :id"),
                {"id": observation_id},
            ).one()
            columns = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'observations'"
                    )
                )
            }

        assert source == "measured"
        assert "rating_curve_id" not in columns
        assert "rating_curve_correction_version" not in columns

        # Re-upgrade must succeed on the coerced data (round-trip).
        command.upgrade(cfg, "head")
