from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


def create_engine_from_env() -> sa.Engine:
    url = os.environ["DATABASE_URL"]
    return sa.create_engine(url)


def run_migrations(engine: sa.Engine) -> None:
    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(alembic_cfg, "head")


def make_pg_stores(conn: sa.Connection) -> dict[str, object]:
    from sapphire_flow.store.basin_store import PgBasinStore
    from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
    from sapphire_flow.store.flow_regime_config_store import PgFlowRegimeConfigStore
    from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
    from sapphire_flow.store.observation_store import PgObservationStore
    from sapphire_flow.store.station_store import PgStationStore

    return {
        "basin_store": PgBasinStore(conn),
        "station_store": PgStationStore(conn),
        "obs_store": PgObservationStore(conn),
        "forcing_store": PgHistoricalForcingStore(conn),
        "baseline_store": PgClimBaselineStore(conn),
        "flow_regime_store": PgFlowRegimeConfigStore(conn),
    }
