from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from alembic.config import Config
from sqlalchemy.exc import DBAPIError

from alembic import command
from tests.integration.db.test_migration_0096_station_ingest_paused import (
    migration_engine as migration_engine,
)
from tests.integration.store.test_hindcast_store import (
    _declare_hindcast,
    _make_hindcast,
    _seed_artifact,
    _seed_model,
    _seed_station,
    savepoint_factory,
)

if TYPE_CHECKING:
    import sqlalchemy as sa


def test_downgrade_preserves_legacy_and_refuses_retained_period_evidence(
    migration_engine: tuple[sa.Engine, str],
) -> None:
    from sapphire_flow.store.hindcast_store import PgHindcastStore

    engine, url = migration_engine
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0097")
    with engine.begin() as conn:
        station = _seed_station(conn)
        model = _seed_model(conn)
        artifact = _seed_artifact(conn, model, station)
        legacy = _make_hindcast(station, model, artifact)
        PgHindcastStore(
            conn, transaction_factory=savepoint_factory(conn)
        ).store_hindcast(legacy)
    command.downgrade(cfg, "0096")
    command.upgrade(cfg, "0097")
    with engine.begin() as conn:
        store = PgHindcastStore(conn, transaction_factory=savepoint_factory(conn))
        assert (
            store.fetch_hindcasts(
                station,
                model,
                legacy.hindcast_step,
                legacy.hindcast_step.replace(year=2027),
            )[0].ensemble.period_definition
            is None
        )
        from dataclasses import replace
        from uuid import uuid4

        declared = _declare_hindcast(
            replace(legacy, id=uuid4(), hindcast_run_id=uuid4())
        )
        store.store_hindcast(declared)
    with pytest.raises(
        DBAPIError, match="Cannot discard retained hindcast period evidence"
    ):
        command.downgrade(cfg, "0096")
    with engine.connect() as conn:
        retained = PgHindcastStore(conn).fetch_hindcasts(
            station,
            model,
            legacy.hindcast_step,
            legacy.hindcast_step.replace(year=2027),
            hindcast_run_id=declared.hindcast_run_id,
        )
        assert (
            retained[0].ensemble.period_definition
            == declared.ensemble.period_definition
        )
