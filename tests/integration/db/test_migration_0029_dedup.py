from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.db.metadata import hindcast_forecasts, hindcast_values
from tests.integration.store.test_hindcast_store import (
    _seed_artifact,
    _seed_model,
    _seed_station,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """Throwaway PostGIS container so a real Alembic upgrade can run the 0029
    dedup against seeded duplicates without disturbing the shared session
    engine (migrated to head once)."""
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


def _insert_dup(
    conn: sa.Connection,
    *,
    station_id: object,
    model_id: object,
    artifact_id: object,
    step: datetime,
    run_id: object,
    created_at: datetime,
    n_values: int,
) -> object:
    """Insert one duplicate header (+ n_values value rows) sharing a natural key."""
    header_id = uuid4()
    conn.execute(
        sa.insert(hindcast_forecasts).values(
            id=header_id,
            station_id=station_id,
            model_id=model_id,
            model_artifact_id=artifact_id,
            hindcast_step=step,
            forcing_type="nwp_archive",
            representation="members",
            hindcast_run_id=run_id,
            parameter="discharge",
            units="m³/s",
            created_at=created_at,
            qc_status="raw",
            qc_flags=None,
        )
    )
    conn.execute(
        sa.insert(hindcast_values),
        [
            {
                "id": uuid4(),
                "hindcast_forecast_id": header_id,
                "hindcast_step": step,
                "valid_time": step,
                "lead_time_hours": i + 1,
                "member_id": i,
                "quantile": None,
                "value": float(i),
            }
            for i in range(n_values)
        ],
    )
    return header_id


class TestMigration0029Dedup:
    """Plan 040: migration 0029 dedups pre-existing duplicate hindcasts before
    adding the 6-col UNIQUE constraint. The survivor must be the NEWEST row
    (latest ``created_at``), matching the latest-wins full-replace upsert.

    RED against the pre-fix migration (which kept the EARLIEST row): the survivor
    would be the older header, so every assertion below flips.
    """

    def test_dedup_keeps_newest_duplicate(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        # Stop BEFORE 0029 so duplicates can be inserted (no unique constraint yet).
        command.upgrade(cfg, "0028")

        step = datetime(2026, 3, 1, tzinfo=UTC)
        run_id = uuid4()
        older = datetime(2026, 3, 2, tzinfo=UTC)
        newer = datetime(2026, 6, 1, tzinfo=UTC)

        with engine.begin() as conn:
            station_id = _seed_station(conn)
            model_id = _seed_model(conn)
            # Two active artifacts (distinct models) so the two duplicate headers
            # can carry a distinguishable mutable field.
            aid_old = _seed_artifact(conn, model_id, station_id)
            model_alt = _seed_model(conn)
            aid_new = _seed_artifact(conn, model_alt, station_id)

            _insert_dup(
                conn,
                station_id=station_id,
                model_id=model_id,
                artifact_id=aid_old,
                step=step,
                run_id=run_id,
                created_at=older,
                n_values=2,
            )
            _insert_dup(
                conn,
                station_id=station_id,
                model_id=model_id,
                artifact_id=aid_new,
                step=step,
                run_id=run_id,
                created_at=newer,
                n_values=3,
            )

        # Run the real dedup + constraint creation.
        command.upgrade(cfg, "0029")

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(
                    hindcast_forecasts.c.id,
                    hindcast_forecasts.c.created_at,
                    hindcast_forecasts.c.model_artifact_id,
                ).where(
                    hindcast_forecasts.c.station_id == station_id,
                    hindcast_forecasts.c.hindcast_run_id == run_id,
                )
            ).all()

            # Exactly one survivor.
            assert len(rows) == 1, f"expected 1 survivor after dedup, got {len(rows)}"
            survivor_id, survivor_created, survivor_artifact = rows[0]

            # The NEWEST duplicate survives (created_at + its mutable field).
            assert survivor_created.astimezone(UTC) == newer, (
                "dedup must keep the newest created_at (latest wins)"
            )
            assert survivor_artifact == aid_new, (
                "survivor must carry the newest duplicate's model_artifact_id"
            )

            # Only the survivor's value rows remain (the older dup's are gone).
            total_values = conn.execute(
                sa.select(sa.func.count()).select_from(hindcast_values)
            ).scalar_one()
            survivor_values = conn.execute(
                sa.select(sa.func.count()).where(
                    hindcast_values.c.hindcast_forecast_id == survivor_id
                )
            ).scalar_one()
            assert survivor_values == 3, "survivor must retain its 3 value rows"
            assert total_values == 3, "orphaned duplicate value rows must be deleted"
