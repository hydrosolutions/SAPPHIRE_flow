"""Plan 235 T1 — LOCKED upgrade acceptance test for migration 0054's
generation identity.

Real Alembic upgrade against a throwaway PostGIS container (mirrors
``tests/integration/db/test_migration_0052_partial_index.py``). Seeds
0052-shaped rows — legacy (`computation_version < 2`) NULL-artifact
duplicates, AND `computation_version = 2` rows written before this plan
(no `generation_id` column exists yet at revision 0052) — then upgrades to
0054 and confirms:

* the upgrade does not raise (the new nullable `generation_id` columns and
  the `skill_generations` table coexist with every pre-existing row);
* every pre-existing row reads back with `generation_id IS NULL` — the
  deterministic baseline semantics T1 requires;
* an old-image write with `generation_id = NULL` still collides with an
  identical-stratum NULL row under `ON CONFLICT DO NOTHING` — the split
  partial index (`uq_skill_scores_natural_key` restricted to
  `generation_id IS NULL`) does not let NULL bypass uniqueness entirely.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from testcontainers.postgres import PostgresContainer

from sapphire_flow.db.metadata import models, skill_scores, stations
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from collections.abc import Iterator

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
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


def _seed_station(conn: sa.Connection) -> uuid.UUID:
    sid = uuid.uuid4()
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"MIG053-{sid.hex[:6]}",
            name="Migration 0054 Test Station",
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
    mid = f"test_migration053_model_{uuid.uuid4().hex[:8]}"
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Migration 0054 Test Model",
            artifact_scope="station",
            description="Integration test",
        )
    )
    return mid


def _insert_score_at_0052_shape(
    conn: sa.Connection,
    *,
    station_id: uuid.UUID,
    model_id: str,
    computation_version: int,
    score: float,
) -> uuid.UUID:
    """Raw INSERT at the pre-0054 (0053-shaped) schema: `time_step_seconds`/
    `phase_offset_seconds` exist, `generation_id` does not yet."""
    row_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO skill_scores "
            "(id, station_id, model_id, model_artifact_id, parameter, "
            "skill_source, forcing_type, computation_version, computed_at, "
            "lead_time_hours, season, flow_regime, metric, score, "
            "sample_size, freshness, eval_period_start, eval_period_end, "
            "created_at, time_step_seconds, phase_offset_seconds) VALUES "
            "(:id, :station_id, :model_id, NULL, :parameter, :skill_source, "
            "NULL, :computation_version, :computed_at, :lead_time_hours, "
            "NULL, NULL, :metric, :score, :sample_size, :freshness, "
            ":eval_period_start, :eval_period_end, :created_at, 86400, NULL)"
        ),
        {
            "id": row_id,
            "station_id": station_id,
            "model_id": model_id,
            "parameter": "discharge",
            "skill_source": "hindcast_nwp_archive",
            "computation_version": computation_version,
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


class TestMigration0054SkillGenerations:
    def test_upgrade_survives_0052_shaped_rows_at_both_versions(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0052")

        with engine.begin() as conn:
            station_id = _seed_station(conn)
            model_id = _seed_model(conn)
            # A legacy (< 2) NULL-artifact duplicate pair (0052's own
            # scenario) ...
            _insert_score_at_0052_shape(
                conn,
                station_id=station_id,
                model_id=model_id,
                computation_version=1,
                score=0.5,
            )
            # ... AND a `computation_version = 2` row written BEFORE this
            # plan (no `generation_id` column exists yet at 0052) — T1:
            # "including any v2 rows written between Plan 228 and this
            # plan."
            v2_row_id = _insert_score_at_0052_shape(
                conn,
                station_id=station_id,
                model_id=model_id,
                computation_version=2,
                score=0.9,
            )

        # Must not raise.
        command.upgrade(cfg, "0054")

        with engine.connect() as conn:
            count = conn.execute(
                sa.text("SELECT COUNT(*) FROM skill_scores WHERE station_id = :sid"),
                {"sid": station_id},
            ).scalar_one()
            v2_generation = conn.execute(
                sa.text("SELECT generation_id FROM skill_scores WHERE id = :id"),
                {"id": v2_row_id},
            ).scalar_one()
        assert count == 2, "both pre-existing rows must survive the upgrade"
        assert v2_generation is None, (
            "a pre-Plan-235 v2 row must read back with generation_id IS "
            "NULL — the deterministic baseline semantics T1 requires"
        )

    def test_null_generation_old_image_write_still_collides(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        """T1 exit gate: an old-image write with a NULL generation cannot
        bypass uniqueness. Confirms `uq_skill_scores_natural_key`'s
        `generation_id IS NULL` restriction still enforces 0052's exact
        collision behaviour among NULL-generation rows — it does not
        silently stop deduplicating just because the column exists.
        """
        from alembic import command

        engine, url = migration_engine
        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "head")

        with engine.begin() as conn:
            station_id = _seed_station(conn)
            model_id = _seed_model(conn)

        def _row(score: float) -> dict[str, object]:
            return {
                "id": uuid.uuid4(),
                "station_id": station_id,
                "model_id": model_id,
                "model_artifact_id": None,
                "parameter": "discharge",
                "skill_source": "hindcast_nwp_archive",
                "forcing_type": None,
                "computation_version": 2,
                "computed_at": _NOW,
                "lead_time_hours": 24,
                "season": None,
                "flow_regime": None,
                "flow_regime_config_id": None,
                "metric": "crps",
                "score": score,
                "sample_size": 10,
                "freshness": "current",
                "eval_period_start": _NOW,
                "eval_period_end": _NOW,
                "created_at": _NOW,
                "time_step_seconds": 86400,
                "phase_offset_seconds": None,
                "generation_id": None,
            }

        with engine.begin() as conn:
            stmt = pg_insert(skill_scores).on_conflict_do_nothing()
            conn.execute(stmt, [_row(0.5)])
            # A second, identical-stratum old-image write — same
            # computation_version, same NULL model_artifact_id, same NULL
            # generation_id.
            conn.execute(stmt, [_row(0.9)])

        with engine.connect() as conn:
            count = conn.execute(
                sa.text(
                    "SELECT COUNT(*) FROM skill_scores WHERE station_id = :sid "
                    "AND generation_id IS NULL"
                ),
                {"sid": station_id},
            ).scalar_one()
        assert count == 1, (
            "a repeated NULL-generation write for the identical stratum "
            f"must still collide under ON CONFLICT DO NOTHING, found "
            f"{count} row(s) — NULL generation must not bypass uniqueness"
        )
