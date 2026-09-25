"""Plan 328 T1 — the MIGRATED schema, not the model.

``db/metadata.py`` carries the same partial-index predicate as the deployed
index, so diffing the two would prove nothing: both were written by the same
hand and both were wrong for months. These assertions run against a database
that has actually been migrated, and compare it to ``ForecastStatus``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from sapphire_flow.types.enums import ForecastStatus

if TYPE_CHECKING:
    from collections.abc import Iterator

from alembic import command

_STATUS_CHECK_DEF = sa.text(
    """
    SELECT pg_get_constraintdef(oid)
    FROM pg_constraint
    WHERE conrelid = 'forecasts'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%status%'
      AND pg_get_constraintdef(oid) NOT LIKE '%qc_status%'
    """
)

_INDEX_DEF = sa.text(
    "SELECT indexdef FROM pg_indexes "
    "WHERE indexname = 'uq_forecasts_station_model_issued_param'"
)


@pytest.fixture
def migration_engine() -> Iterator[tuple[sa.Engine, str]]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_superseded_migration_test",
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


def _config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migrated_check_admits_every_declared_status(
    migration_engine: tuple[sa.Engine, str],
) -> None:
    engine, url = migration_engine
    config = _config(url)

    command.upgrade(config, "0057")
    with engine.connect() as connection:
        before = connection.execute(_STATUS_CHECK_DEF).scalar_one()
    assert "superseded" not in before

    command.upgrade(config, "0058")
    with engine.connect() as connection:
        after = connection.execute(_STATUS_CHECK_DEF).scalar_one()
        index_def = connection.execute(_INDEX_DEF).scalar_one()

    missing = [s.value for s in ForecastStatus if f"'{s.value}'" not in after]
    assert not missing, f"migrated CHECK rejects declared statuses: {missing}"

    # ⛔ The predicate is REACHABLE now, not replaced: the deployed index text
    # must still read exactly as it did before this revision.
    assert "status <> 'superseded'" in index_def.replace("::text", "")

    # The downgrade narrows it again (there is nothing to rewrite: no row has
    # been superseded on a database that only just gained the status).
    command.downgrade(config, "0057")
    with engine.connect() as connection:
        narrowed = connection.execute(_STATUS_CHECK_DEF).scalar_one()
    assert "superseded" not in narrowed


def test_migrated_predicate_names_only_declared_statuses(
    migration_engine: tuple[sa.Engine, str],
) -> None:
    """The failure Plan 328 exists to end: the deployed partial index excluded
    a value the domain could not produce, so it silently behaved as a FULL
    unique index."""
    engine, url = migration_engine
    command.upgrade(_config(url), "0058")

    with engine.connect() as connection:
        index_def = connection.execute(_INDEX_DEF).scalar_one()

    predicate = index_def.split(" WHERE ", 1)[1]
    quoted = {
        chunk.split("'")[0]
        for chunk in predicate.split("'")[1::2]
        if chunk  # every literal named in the predicate
    }
    declared = {status.value for status in ForecastStatus}
    assert quoted <= declared, (
        f"deployed index predicate names {sorted(quoted - declared)}, which "
        f"ForecastStatus cannot produce"
    )
