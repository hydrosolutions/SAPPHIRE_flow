from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog
from prefect import flow

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.nepal_onboarding import (
    chwrr_stations,
    history_binding,
    operational_weather_stations,
    require_history_bindings,
)
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.nepal_onboarding import (
    GATEWAY_HISTORY_SOURCE,
    HISTORY_PARAMETERS,
    HistoryCoverage,
    HistoryWindow,
)

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.nepal_onboarding import (
        OperationalWeatherScope,
    )


log = structlog.get_logger(__name__)


@flow(
    name="ingest-recap-era5-reanalysis",
    log_prints=False,
    persist_result=False,
    validate_parameters=False,
)
def ingest_recap_era5_reanalysis_flow(
    conn: sa.Connection,
    identity: DeploymentIdentityConfig,
    adapter: WeatherReanalysisSource,
    window: HistoryWindow,
    *,
    now: UtcDatetime,
    scope: OperationalWeatherScope | None = None,
) -> tuple[HistoryCoverage, ...]:
    return _ingest_history(conn, identity, adapter, window, now=now, scope=scope)


def _ingest_history(
    conn: sa.Connection,
    identity: DeploymentIdentityConfig,
    adapter: WeatherReanalysisSource,
    window: HistoryWindow,
    *,
    now: UtcDatetime,
    scope: OperationalWeatherScope | None = None,
) -> tuple[HistoryCoverage, ...]:
    if scope is None:
        stations = chwrr_stations(conn, identity, now=now)
        bindings = require_history_bindings(conn, stations)
    else:
        stations = operational_weather_stations(conn, identity, scope, now=now)
        bindings = [history_binding(station.id) for station in stations]
    station_ids = {s.id for s in stations}
    store = PgHistoricalForcingStore(conn)
    start = window.start
    while start < window.end:
        end = ensure_utc(min(start + timedelta(days=31), window.end))
        rows = adapter.fetch_reanalysis(
            bindings, start, end, sorted(HISTORY_PARAMETERS)
        )
        if any(
            r.station_id not in station_ids
            or r.source != GATEWAY_HISTORY_SOURCE
            or r.parameter not in HISTORY_PARAMETERS
            or not start <= r.valid_time < end
            or r.spatial_type is not SpatialRepresentation.BASIN_AVERAGE
            or r.band_id is not None
            or r.member_id is not None
            or not math.isfinite(r.value)
            for r in rows
        ):
            raise ConfigurationError(
                "Gateway returned rows outside the requested history contract"
            )
        store.store_forcing(rows)
        start = end
    coverage = []
    for station in stations:
        for parameter in sorted(HISTORY_PARAMETERS):
            records = store.fetch_forcing(
                station.id,
                GATEWAY_HISTORY_SOURCE,
                window.start,
                window.end,
                [parameter],
            )
            stamps = [r.valid_time for r in records if math.isfinite(r.value)]
            coverage.append(
                HistoryCoverage(
                    station_id=station.id,
                    code=station.code,
                    parameter=parameter,
                    rows=len(stamps),
                    start=min(stamps) if stamps else None,
                    end=max(stamps) if stamps else None,
                )
            )
    return tuple(coverage)


def parse_history_window(start: str, end: str) -> HistoryWindow:
    return HistoryWindow(
        start=ensure_utc(datetime.fromisoformat(start)),
        end=ensure_utc(datetime.fromisoformat(end)),
    )


@flow(
    name="ingest-operational-era5-history",
    log_prints=False,
    persist_result=False,
    retries=0,
)
def ingest_operational_era5_history_flow(
    start: str,
    end: str,
    weather_scope: str,
) -> tuple[HistoryCoverage, ...]:
    try:
        return _run_operational_history(start, end, weather_scope)
    except Exception:
        # SDK errors may carry prepared URLs containing the API key.
        # Raise outside the handler so Prefect receives no chained exception.
        pass
    raise ConfigurationError("Operational ERA5 history failed")


def _run_operational_history(
    start: str,
    end: str,
    weather_scope: str,
) -> tuple[HistoryCoverage, ...]:
    from recap_client import RecapClient

    from sapphire_flow.adapters.recap_gateway import (
        RecapClientLike,
        RecapGatewayReanalysisAdapter,
        StoreBackedGatewayPolygonResolver,
    )
    from sapphire_flow.config.deployment_identity import load_deployment_identity_config
    from sapphire_flow.config.nepal_weather import load_weather_scope
    from sapphire_flow.config.recap_gateway import (
        build_recap_client_config,
        load_recap_api_key,
        load_recap_gateway_config,
    )
    from sapphire_flow.db.engine import create_engine_from_env
    from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore

    window = parse_history_window(start, end)
    config_path = Path(os.environ.get("SAPPHIRE_CONFIG", "config.toml"))
    identity = load_deployment_identity_config(config_path)
    scope = load_weather_scope(config_path, weather_scope).scope
    config = load_recap_gateway_config(config_path)
    engine = create_engine_from_env()
    try:
        with engine.connect() as conn:
            coverage: tuple[HistoryCoverage, ...] = ()
            for batch in window.batches():
                with conn.begin():
                    now = ensure_utc(datetime.now(UTC))
                    operational_weather_stations(conn, identity, scope, now=now)
                    client = RecapClient(
                        build_recap_client_config(
                            api_key=load_recap_api_key(), config=config
                        )
                    )
                    try:
                        adapter = RecapGatewayReanalysisAdapter(
                            client=cast("RecapClientLike", client),
                            resolver=StoreBackedGatewayPolygonResolver(
                                RecapGatewayPolygonStore(conn)
                            ),
                        )
                        batch_coverage = _ingest_history(
                            conn, identity, adapter, batch, now=now, scope=scope
                        )
                    finally:
                        # The pinned SDK has no public close/context-manager API.
                        client.ecmwf._http._session.close()  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
                coverage += batch_coverage
                for item in batch_coverage:
                    log.info(
                        "nepal_onboarding.history",
                        station=item.code,
                        parameter=item.parameter,
                        rows=item.rows,
                        start=item.start,
                        end=item.end,
                    )
            return coverage
    finally:
        engine.dispose()
