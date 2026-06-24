from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa  # noqa: TCH002 — used in function bodies via typed params

from sapphire_flow.db.engine import create_engine_from_env as create_engine_from_env

# _db.py → flows/ → sapphire_flow/ → src/ → repo_root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def run_migrations(engine: sa.Engine) -> None:
    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(alembic_cfg, "head")


def make_pg_stores(conn: sa.Connection) -> dict[str, object]:
    from sapphire_flow.config.paths import resolve_artifact_dir
    from sapphire_flow.store.alert_store import PgAlertStore
    from sapphire_flow.store.basin_store import PgBasinStore
    from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
    from sapphire_flow.store.flow_regime_config_store import PgFlowRegimeConfigStore
    from sapphire_flow.store.forecast_store import PgForecastStore
    from sapphire_flow.store.hindcast_store import PgHindcastStore
    from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
    from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
    from sapphire_flow.store.model_state_store import PgModelStateStore
    from sapphire_flow.store.model_store import PgModelStore
    from sapphire_flow.store.observation_store import PgObservationStore
    from sapphire_flow.store.parameter_store import PgParameterStore
    from sapphire_flow.store.skill_store import PgSkillStore
    from sapphire_flow.store.station_group_store import PgStationGroupStore
    from sapphire_flow.store.station_store import PgStationStore
    from sapphire_flow.store.weather_forecast_store import PgWeatherForecastStore

    artifact_dir = resolve_artifact_dir()

    return {
        "alert_store": PgAlertStore(conn),
        "basin_store": PgBasinStore(conn),
        "station_store": PgStationStore(conn),
        "obs_store": PgObservationStore(conn),
        "forcing_store": PgHistoricalForcingStore(conn),
        "baseline_store": PgClimBaselineStore(conn),
        "flow_regime_store": PgFlowRegimeConfigStore(conn),
        "model_store": PgModelStore(conn),
        "artifact_store": PgModelArtifactStore(conn, artifact_dir),
        "group_store": PgStationGroupStore(conn),
        "hindcast_store": PgHindcastStore(conn),
        "skill_store": PgSkillStore(conn),
        "weather_forecast_store": PgWeatherForecastStore(conn),
        "forecast_store": PgForecastStore(conn),
        "model_state_store": PgModelStateStore(conn),
        "parameter_store": PgParameterStore(conn),
    }


def setup_production_stores(
    database_url: str,
) -> tuple[sa.Connection, dict[str, object]]:
    engine = sa.create_engine(database_url, pool_pre_ping=True)
    # Migrations are handled by the init container (docker-compose).
    # For local dev, run: alembic upgrade head
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    stores = make_pg_stores(conn)
    return conn, stores
