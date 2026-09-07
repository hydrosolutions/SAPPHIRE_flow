"""Plan 253 T1a — LOCKED upgrade/downgrade acceptance test for migration
0054's ``forecasts.input_quality`` / ``input_quality_flags`` columns.

Real Alembic upgrade against a throwaway PostGIS container (mirrors
``tests/integration/db/test_migration_0052_partial_index.py``). A row
inserted at the pre-0054 schema shape must survive the upgrade with `NULL`
in both new columns — not the `InputQualityLevel.FULL` default a server
default would silently substitute (the exact failure this migration's
docstring, and this plan's exit gate 2, forbid). Downgrade must remove both
columns and leave the row otherwise intact.

REVISION CHAIN (post-rebase). This migration was authored as `0053` while
`origin/main` independently landed its own `0053_forecasts_time_step.py`
(Plan 241 T4, `forecasts.time_step_seconds`); both chained onto `0052`. On
merge ours was rebased to revision `0054`, down_revision `0053`. So the
pre-input-quality schema shape is revision **0053**, not `0052`, and the
downgrade target is **0053** — downgrading to `0052` would tear out main's
independent migration and prove nothing about ours.
"""

from __future__ import annotations

import os
import random
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.store.station_store import PgStationStore
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator

_NOW = datetime(2026, 1, 1, tzinfo=UTC)

# The revision immediately BELOW this migration: main's Plan 241 T4
# `forecasts.time_step_seconds`. Seeding the legacy row here (not at 0052)
# is what makes the row genuinely pre-input-quality without also unwinding
# an unrelated migration.
_PRE_REVISION = "0053"
_REVISION = "0054"


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """Throwaway PostGIS container so a real Alembic upgrade/downgrade can
    run 0054 against a seeded legacy row without disturbing the shared
    session engine (migrated to head once)."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_0054_test",
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


def _forecasts_columns(conn: sa.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'forecasts'"
            )
        )
    }


def _seed_station(conn: sa.Connection) -> uuid.UUID:
    station = make_station_config(rng=random.Random(54))
    PgStationStore(conn).store_station(station)
    return station.id


def _seed_model(conn: sa.Connection, model_id: str = "test_model_0054") -> str:
    conn.execute(
        sa.text(
            "INSERT INTO models (id, display_name, artifact_scope, description) "
            "VALUES (:id, 'Migration Test Model', 'station', 'Integration test')"
        ),
        {"id": model_id},
    )
    return model_id


def _insert_legacy_forecast(
    conn: sa.Connection, *, station_id: uuid.UUID, model_id: str
) -> uuid.UUID:
    """Raw INSERT at the pre-0054 schema shape — neither `input_quality` nor
    `input_quality_flags` exists yet at revision 0053."""
    forecast_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO forecasts "
            "(id, station_id, model_id, issued_at, representation, parameter, "
            "units) VALUES "
            "(:id, :station_id, :model_id, :issued_at, 'members', 'discharge', "
            "'m3/s')"
        ),
        {
            "id": forecast_id,
            "station_id": station_id,
            "model_id": model_id,
            "issued_at": _NOW,
        },
    )
    return forecast_id


class TestMigration0054InputQuality:
    def test_upgrade_leaves_legacy_row_null_not_full(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, _PRE_REVISION)

        with engine.begin() as conn:
            # The row is genuinely legacy only if the columns do not exist yet.
            pre_columns = _forecasts_columns(conn)
            assert "input_quality" not in pre_columns
            assert "input_quality_flags" not in pre_columns

            station_id = _seed_station(conn)
            model_id = _seed_model(conn)
            forecast_id = _insert_legacy_forecast(
                conn, station_id=station_id, model_id=model_id
            )

        # Must not raise — nullable, no server default.
        command.upgrade(cfg, _REVISION)

        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT input_quality, input_quality_flags FROM forecasts "
                    "WHERE id = :id"
                ),
                {"id": forecast_id},
            ).one()

        assert row.input_quality is None, (
            "a pre-migration row must read input_quality as NULL (unknown), "
            f"got {row.input_quality!r} — a server default would silently "
            "substitute InputQualityLevel.FULL"
        )
        assert row.input_quality_flags is None, (
            "a pre-migration row must read input_quality_flags as NULL "
            f"(unknown), got {row.input_quality_flags!r}"
        )

    def test_columns_are_nullable_with_no_server_default(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT column_name, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'forecasts' "
                    "AND column_name IN ('input_quality', 'input_quality_flags')"
                )
            ).all()

        by_name = {r.column_name: r for r in rows}
        assert set(by_name) == {"input_quality", "input_quality_flags"}
        for name, r in by_name.items():
            assert r.is_nullable == "YES", f"{name} must be nullable"
            assert r.column_default is None, (
                f"{name} must have no server default, found {r.column_default!r}"
            )

    def test_downgrade_to_0053_removes_both_columns_and_keeps_time_step(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        with engine.begin() as conn:
            station_id = _seed_station(conn)
            model_id = _seed_model(conn)
            forecast_id = _insert_legacy_forecast(
                conn, station_id=station_id, model_id=model_id
            )

        # 0054 -> 0053 ONLY. Downgrading to 0052 would also tear out main's
        # independent Plan 241 T4 migration, which this revision merely
        # chains onto and must leave untouched.
        command.downgrade(cfg, _PRE_REVISION)

        with engine.connect() as conn:
            columns = _forecasts_columns(conn)
            (surviving_id,) = conn.execute(
                sa.text("SELECT id FROM forecasts WHERE id = :id"),
                {"id": forecast_id},
            ).one()

        assert "input_quality" not in columns
        assert "input_quality_flags" not in columns
        assert "time_step_seconds" in columns, (
            "downgrading 0054 must leave main's independent 0053 migration "
            "(forecasts.time_step_seconds) in place — this revision only "
            "chains onto it"
        )
        assert surviving_id == forecast_id

        # Round-trip: re-upgrade must succeed.
        command.upgrade(cfg, "head")
