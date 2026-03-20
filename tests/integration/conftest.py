from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.historical_forcing import HistoricalForcingRecord
from sapphire_flow.types.ids import HistoricalForcingId, StationId


def make_historical_forcing_record(
    *,
    station_id: StationId | None = None,
    source: str = "camels-ch",
    version: str = "1.0",
    valid_time: datetime | None = None,
    parameter: str = "precipitation",
    spatial_type: SpatialRepresentation = SpatialRepresentation.BASIN_AVERAGE,
    band_id: int | None = None,
    member_id: int | None = None,
    value: float = 5.0,
) -> HistoricalForcingRecord:
    return HistoricalForcingRecord(
        id=HistoricalForcingId(uuid4()),
        station_id=station_id or StationId(uuid4()),
        source=source,
        version=version,
        valid_time=ensure_utc(valid_time or datetime(2026, 1, 15, 12, 0, tzinfo=UTC)),
        parameter=parameter,
        spatial_type=spatial_type,
        band_id=band_id,
        member_id=member_id,
        value=value,
        created_at=ensure_utc(datetime.now(UTC)),
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
