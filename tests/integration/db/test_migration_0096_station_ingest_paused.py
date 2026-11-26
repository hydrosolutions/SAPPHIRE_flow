"""#501: migration 0096 adds stations.ingest_paused against real Postgres."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_0096_test",
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


def _cfg(url: str) -> object:
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _columns(conn: sa.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'stations'"
            )
        )
    ]


def test_upgrade_defaults_existing_rows_to_unpaused_and_downgrade_drops_it(
    migration_engine: tuple[sa.Engine, str],
) -> None:
    engine, url = migration_engine
    from alembic import command
    from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

    cfg = _cfg(url)
    command.upgrade(cfg, "0095")
    sid = uuid.uuid4()
    with engine.begin() as conn:
        assert "ingest_paused" not in _columns(conn)
        conn.execute(
            sa.text(
                "INSERT INTO stations (id, code, name, location, station_kind, "
                "network, timezone, measured_parameters, ownership, tenant_id) "
                "VALUES (:id, 'MIG', 'Mig', 'SRID=4326;POINT(8.5 47.4)', 'river', "
                "'bafu', 'Europe/Zurich', ARRAY['discharge'], 'own', :t)"
            ),
            {"id": sid, "t": DEFAULT_TENANT_ID},
        )

    command.upgrade(cfg, "0096")
    with engine.connect() as conn:
        paused = conn.execute(
            sa.text("SELECT ingest_paused FROM stations WHERE id = :i"), {"i": sid}
        ).scalar_one()
        assert paused is False
        col = conn.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'stations' AND column_name = 'ingest_paused'"
            )
        ).one()
        assert col.is_nullable == "NO"

    command.downgrade(cfg, "0095")
    with engine.connect() as conn:
        assert "ingest_paused" not in _columns(conn)
        remaining = conn.execute(
            sa.text("SELECT count(*) FROM stations WHERE id = :i"), {"i": sid}
        ).scalar_one()
        assert remaining == 1
