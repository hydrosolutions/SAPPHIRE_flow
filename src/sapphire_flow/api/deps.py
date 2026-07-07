from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from typing import Any

import sqlalchemy as sa
from fastapi import Depends, FastAPI, Request

from sapphire_flow.db.engine import create_engine_from_env


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.engine = create_engine_from_env()
    yield
    app.state.engine.dispose()


def get_connection(request: Request) -> Generator[sa.Connection, None, None]:
    engine: sa.Engine = request.app.state.engine
    with engine.connect() as conn:
        yield conn


def get_connection_rw(request: Request) -> Generator[sa.Connection, None, None]:
    engine: sa.Engine = request.app.state.engine
    with engine.begin() as conn:
        yield conn


def get_stores(
    conn: sa.Connection = Depends(get_connection),
) -> dict[str, Any]:
    from sapphire_flow.config.paths import resolve_artifact_dir
    from sapphire_flow.store.alert_store import PgAlertStore
    from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
    from sapphire_flow.store.flow_regime_config_store import PgFlowRegimeConfigStore
    from sapphire_flow.store.forecast_store import PgForecastStore
    from sapphire_flow.store.hindcast_store import PgHindcastStore
    from sapphire_flow.store.historical_forcing_store import (
        PgHistoricalForcingStore,
    )
    from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
    from sapphire_flow.store.model_store import PgModelStore
    from sapphire_flow.store.observation_store import PgObservationStore
    from sapphire_flow.store.pipeline_health_store import PgPipelineHealthStore
    from sapphire_flow.store.skill_store import PgSkillStore
    from sapphire_flow.store.station_group_store import PgStationGroupStore
    from sapphire_flow.store.station_store import PgStationStore

    artifact_dir = resolve_artifact_dir()

    return {
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
        "forecast_store": PgForecastStore(conn),
        "alert_store": PgAlertStore(conn),
        "pipeline_health_store": PgPipelineHealthStore(conn),
    }
