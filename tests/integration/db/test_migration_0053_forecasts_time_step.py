"""Plan 241 T4 — acceptance test for migration 0053 against real Postgres.

⛔ These are DATABASE claims — upgrade, downgrade, the constraint's NAME, and a
pre-migration row surviving with NULL. A unit run cannot establish any of them.
Mirrors ``test_migration_0052_partial_index.py``.

0053 is NULLABLE-FIRST by design (``docs/standards/cicd.md`` §Rollback: additive
only, new columns nullable, so the previous image tag can run against the new
schema). These tests pin that shape: a nullable column, NO server default, NO
backfill, and a NULL-tolerant NAMED check constraint.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

if TYPE_CHECKING:
    from collections.abc import Iterator

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_CONSTRAINT = "ck_forecasts_time_step_seconds_positive"


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_migration_0053_test",
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


def _seed_legacy_forecast(conn: sa.Connection) -> uuid.UUID:
    """A forecast row as it exists BEFORE 0053 — no cadence column at all."""
    from sapphire_flow.db.metadata import models, stations
    from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

    sid, mid, fid = uuid.uuid4(), "legacy_model", uuid.uuid4()
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
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Migration Test Model",
            artifact_scope="station",
            description="Integration test",
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO forecasts (id, station_id, model_id, issued_at, "
            "representation, status, version, parameter, units, created_at, "
            "updated_at, nwp_cycle_source) VALUES (:id, :sid, :mid, :ts, "
            "'members', 'raw', 1, 'discharge', 'm³/s', :ts, :ts, 'primary')"
        ),
        {"id": fid, "sid": sid, "mid": mid, "ts": _NOW},
    )
    return fid


class TestMigration0053:
    def test_upgrade_adds_a_nullable_column_and_leaves_existing_rows_null(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, url = migration_engine
        from alembic import command

        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0052")

        with engine.begin() as conn:
            fid = _seed_legacy_forecast(conn)

        command.upgrade(cfg, "0053")

        with engine.connect() as conn:
            # The pre-migration row survives, with NO invented cadence.
            stored = conn.execute(
                sa.text("SELECT time_step_seconds FROM forecasts WHERE id = :i"),
                {"i": fid},
            ).scalar_one()
            assert stored is None, (
                "0053 must not backfill — an invented cadence is the very defect "
                "Plan 241 removes"
            )

            col = conn.execute(
                sa.text(
                    "SELECT is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'forecasts' "
                    "AND column_name = 'time_step_seconds'"
                )
            ).one()
            assert col.is_nullable == "YES"
            assert col.column_default is None, (
                "nullable-first means NO server default; a default would silently "
                "stamp a cadence on every legacy row"
            )

    def test_check_constraint_is_named_and_null_tolerant(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, url = migration_engine
        from alembic import command

        command.upgrade(_alembic_cfg(url), "0053")

        with engine.connect() as conn:
            names = [
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT conname FROM pg_constraint c "
                        "JOIN pg_class t ON t.oid = c.conrelid "
                        "WHERE t.relname = 'forecasts' AND c.contype = 'c'"
                    )
                )
            ]
            assert _CONSTRAINT in names, (
                f"migration must create the NAMED constraint; found {names}"
            )

    def test_metadata_and_migration_agree_on_the_constraint_name(self) -> None:
        """The drift class revision 0051 exists to repair.

        Asserted on what SQLAlchemy EMITS, never on source text — an unnamed
        column-level CheckConstraint compiles to an anonymous CHECK that
        Postgres auto-names, which would make 0053's own downgrade fail against
        a create_all schema.
        """
        from sapphire_flow.db.metadata import metadata

        emitted = {
            c.name
            for c in metadata.tables["forecasts"].constraints
            if type(c).__name__ == "CheckConstraint" and c.name
        }
        assert _CONSTRAINT in emitted, (
            f"metadata.py must emit {_CONSTRAINT!r}; emitted {emitted}"
        )

    def test_constraint_rejects_a_non_positive_cadence_but_allows_null(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, url = migration_engine
        from alembic import command

        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0052")
        with engine.begin() as conn:
            fid = _seed_legacy_forecast(conn)
        command.upgrade(cfg, "0053")

        with engine.begin() as conn:  # NULL is allowed (legacy rows)
            conn.execute(
                sa.text("UPDATE forecasts SET time_step_seconds = NULL WHERE id = :i"),
                {"i": fid},
            )
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE forecasts SET time_step_seconds = 0 WHERE id = :i"),
                {"i": fid},
            )

    def test_downgrade_runs_clean(
        self, migration_engine: tuple[sa.Engine, str]
    ) -> None:
        engine, url = migration_engine
        from alembic import command

        cfg = _alembic_cfg(url)
        command.upgrade(cfg, "0053")
        command.downgrade(cfg, "0052")

        with engine.connect() as conn:
            cols = [
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'forecasts'"
                    )
                )
            ]
            assert "time_step_seconds" not in cols
