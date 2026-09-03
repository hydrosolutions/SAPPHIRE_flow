"""Plan 228 per-run scope (2026-09-03) — LOCKED upgrade acceptance test for
migration 0052's partial unique indexes.

Real Alembic upgrade against a throwaway PostGIS container (mirrors
``tests/integration/db/test_migration_0029_dedup.py``). Seeds two
`computation_version=1` `skill_scores` rows that share every 0051
natural-key column except `model_artifact_id` (both `NULL`) — a shape the
live mac-mini legitimately accumulated under 0051's raw (non-`COALESCE`)
NULL compare, since PostgreSQL never treats two `NULL`s as equal.

RED against a plain (non-partial) `CREATE UNIQUE INDEX` over the tightened,
`COALESCE`-NULL-safe key: the two seeded rows collide and the upgrade raises
``UniqueViolation``. GREEN once the tightened index is made PARTIAL
(``WHERE computation_version >= 2``): the seeded `computation_version=1`
rows are grandfathered untouched, so the upgrade completes and both survive.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.db.metadata import models, stations
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from collections.abc import Iterator

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    """Throwaway PostGIS container so a real Alembic upgrade can run 0052
    against seeded legacy duplicates without disturbing the shared session
    engine (migrated to head once)."""
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_0052_test",
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


def _seed_station(conn: sa.Connection) -> uuid.UUID:
    sid = uuid.uuid4()
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"MIG-{sid.hex[:6]}",
            name="Migration Test Station",
            location="SRID=4326;POINT(8.5 47.4)",
            station_kind="river",
            network="bafu",
            timezone="Europe/Zurich",
            measured_parameters=["discharge"],
            ownership="own",
            tenant_id=DEFAULT_TENANT_ID,
        )
    )
    return sid


def _seed_model(conn: sa.Connection) -> str:
    mid = f"test_migration_model_{uuid.uuid4().hex[:8]}"
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Migration Test Model",
            artifact_scope="station",
            description="Integration test",
        )
    )
    return mid


def _insert_legacy_score(
    conn: sa.Connection,
    *,
    station_id: uuid.UUID,
    model_id: str,
    score: float,
) -> uuid.UUID:
    """Raw INSERT at the pre-0052 schema shape (no `time_step_seconds`/
    `phase_offset_seconds` columns exist yet at revision 0051) —
    `model_artifact_id` is NULL, matching a real pooled/BMA
    `computation_version=1` row on the live mac-mini."""
    row_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO skill_scores "
            "(id, station_id, model_id, model_artifact_id, parameter, "
            "skill_source, forcing_type, computation_version, computed_at, "
            "lead_time_hours, season, flow_regime, metric, score, "
            "sample_size, freshness, eval_period_start, eval_period_end, "
            "created_at) VALUES "
            "(:id, :station_id, :model_id, NULL, :parameter, :skill_source, "
            "NULL, :computation_version, :computed_at, :lead_time_hours, "
            "NULL, NULL, :metric, :score, :sample_size, :freshness, "
            ":eval_period_start, :eval_period_end, :created_at)"
        ),
        {
            "id": row_id,
            "station_id": station_id,
            "model_id": model_id,
            "parameter": "discharge",
            "skill_source": "hindcast_nwp_archive",
            "computation_version": 1,
            "computed_at": _NOW,
            "lead_time_hours": 24,
            "metric": "crps",
            "score": score,
            "sample_size": 10,
            "freshness": "current",
            "eval_period_start": _NOW,
            "eval_period_end": _NOW,
            "created_at": _NOW,
        },
    )
    return row_id


class TestMigration0052PartialIndex:
    def test_upgrade_survives_legacy_null_artifact_duplicates(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0051")

        with engine.begin() as conn:
            station_id = _seed_station(conn)
            model_id = _seed_model(conn)
            # Two rows sharing EVERY 0051 natural-key column (both
            # `model_artifact_id=NULL`) at the legacy `computation_version`
            # — exactly what 0051's raw-NULL compare legitimately allowed
            # to coexist.
            _insert_legacy_score(
                conn, station_id=station_id, model_id=model_id, score=0.5
            )
            _insert_legacy_score(
                conn, station_id=station_id, model_id=model_id, score=0.7
            )

        # Must not raise — the tightened key only applies to
        # computation_version >= 2.
        command.upgrade(cfg, "0052")

        with engine.connect() as conn:
            count = conn.execute(
                sa.text("SELECT COUNT(*) FROM skill_scores WHERE station_id = :sid"),
                {"sid": station_id},
            ).scalar_one()
        assert count == 2, (
            "both legacy computation_version=1 duplicate rows must survive "
            f"the 0052 upgrade ungrandfathered, found {count}"
        )
