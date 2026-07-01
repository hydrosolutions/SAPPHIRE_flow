from __future__ import annotations

import os
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from tests.integration.store.test_forecast_store import (
    _ISSUED_A,
    _seed_model,
    _seed_station,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """A throwaway PostGIS container so a real Alembic downgrade can mutate the
    schema without disturbing the session-scoped integration engine (which is
    migrated to head once and shared by every other integration test)."""
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


class TestMigration0026Downgrade:
    """epic-088 M4: migration 0026 must stay reversible even after runoff-only
    forecasts exist. The upgrade admits ``nwp_cycle_source='runoff_only'`` with a
    NULL ``nwp_cycle_reference_time``; the downgrade must coerce those rows back
    into the pre-0026 two-value world BEFORE restoring the old CHECK + NOT NULL.

    RED before the downgrade fix: re-creating the two-value CHECK raises a
    violation on the runoff-only row (and the NOT NULL alter raises on its NULL
    reference time), so the downgrade aborts.
    """

    def test_downgrade_coerces_runoff_only_rows(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command
        from sapphire_flow.db.metadata import forecasts

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        forecast_id = uuid4()
        with engine.begin() as conn:
            station_id = _seed_station(conn)
            model_id = _seed_model(conn, "runoff_only_downgrade")
            conn.execute(
                sa.insert(forecasts).values(
                    id=forecast_id,
                    station_id=station_id,
                    model_id=model_id,
                    model_artifact_id=None,
                    issued_at=_ISSUED_A,
                    nwp_cycle_reference_time=None,
                    nwp_cycle_source="runoff_only",
                    representation="members",
                    status="raw",
                    version=1,
                    parameter="discharge",
                    units="m³/s",
                )
            )

        # The reversibility contract: this must NOT raise despite the runoff-only
        # row that only migration 0026 made representable.
        command.downgrade(cfg, "0025")

        with engine.connect() as conn:
            source, reference_time = conn.execute(
                sa.text(
                    "SELECT nwp_cycle_source, nwp_cycle_reference_time "
                    "FROM forecasts WHERE id = :id"
                ),
                {"id": forecast_id},
            ).one()

        # Coerced back into the two-value world; reference time backfilled from
        # issued_at (the nominal issue time).
        assert source == "primary"
        assert reference_time is not None
