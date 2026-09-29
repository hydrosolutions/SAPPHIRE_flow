from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING

from prefect import flow

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.nepal_onboarding import (
    chwrr_stations,
    require_history_bindings,
)
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.nepal_onboarding import (
    GATEWAY_HISTORY_SOURCE,
    HISTORY_PARAMETERS,
    HistoryCoverage,
)

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.nepal_onboarding import HistoryWindow


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
) -> tuple[HistoryCoverage, ...]:
    stations = chwrr_stations(conn, identity, now=now)
    bindings = require_history_bindings(conn, stations)
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
