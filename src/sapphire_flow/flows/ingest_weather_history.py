"""Rolling weather-history ingest flow (Plan 071).

Mirrors ``flows/ingest_observations.py``: a single ``@flow`` with injected
stores, adapter, and clock (no direct ``datetime.now()``), structlog events, and
per-step Prefect tasks.

This collapses the plan's separate catchup + daily-append flows into ONE
rolling-ingest flow deployed daily. Each run fetches the MeteoSwiss daily
reanalysis over the rolling ``[clock() - 60 days, clock()]`` window for every
station bound to the reanalysis source, and persists the returned rows to the
``historical_forcing`` store. Idempotency is delegated to the store's
content-hash ``version`` supersession — the flow performs no dedup of its own
and NEVER writes to ``weather_forecasts``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import structlog
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.adapters.meteoswiss_open_data_reanalysis import (
        MeteoSwissOpenDataReanalysisAdapter,
    )
    from sapphire_flow.protocols.stores import (
        BasinStore,
        HistoricalForcingStore,
        StationStore,
    )
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationWeatherSource

log = structlog.get_logger(__name__)

# The rolling window the MeteoSwiss open-data daily archive retains.
_WINDOW_DAYS = 60

# Production reanalysis-adapter STAC defaults (MeteoSwiss open data).
_DEFAULT_REANALYSIS_STAC_BASE_URL = "https://data.geo.admin.ch/api/stac/v1"
_DEFAULT_REANALYSIS_STAC_COLLECTION = "ch.meteoschweiz.ogd-surface-derived-grid"

# The four canonical MeteoSwiss daily products this flow requests.
_CANONICAL_PARAMETERS: list[str] = [
    "precipitation",
    "temperature",
    "temperature_min",
    "temperature_max",
]


class _ReanalysisAdapter(Protocol):
    """Structural view of the reanalysis adapter the flow needs: a source
    identity to filter station weather-sources by, and ``fetch_reanalysis``."""

    NWP_SOURCE: str

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherHistoryIngestResult:
    stations_targeted: int
    rows_fetched: int
    rows_stored: int


@dataclass(frozen=True, kw_only=True, slots=True)
class _ReanalysisStacConfig:
    stac_base_url: str
    stac_collection: str


# ---------------------------------------------------------------------------
# Production adapter factory
# ---------------------------------------------------------------------------


def _load_reanalysis_stac_config() -> _ReanalysisStacConfig:
    """Read STAC base-url/collection for the reanalysis adapter from
    ``[adapters.weather_reanalysis]``.

    Falls back to the MeteoSwiss open-data defaults when ``SAPPHIRE_CONFIG`` is
    unset or the section/keys are absent. Mirrors the lightweight TOML-overlay
    read used by the forecast-cycle adapter config loader.
    """
    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is None:
        return _ReanalysisStacConfig(
            stac_base_url=_DEFAULT_REANALYSIS_STAC_BASE_URL,
            stac_collection=_DEFAULT_REANALYSIS_STAC_COLLECTION,
        )

    from sapphire_flow.config._overlay import (
        _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
        load_merged_toml,
    )

    data = cast(
        "dict[str, Any]",
        load_merged_toml(Path(config_path), _resolve_overlay_paths()),
    )
    adapters = data.get("adapters", {})
    section = (
        adapters.get("weather_reanalysis", {}) if isinstance(adapters, dict) else {}
    )
    if not isinstance(section, dict):
        section = {}

    base_url = section.get("stac_base_url", _DEFAULT_REANALYSIS_STAC_BASE_URL)
    collection = section.get("stac_collection", _DEFAULT_REANALYSIS_STAC_COLLECTION)
    if not isinstance(base_url, str) or base_url == "":
        raise ConfigurationError(
            "[adapters.weather_reanalysis].stac_base_url must be a non-empty string"
        )
    if not isinstance(collection, str) or collection == "":
        raise ConfigurationError(
            "[adapters.weather_reanalysis].stac_collection must be a non-empty string"
        )
    return _ReanalysisStacConfig(stac_base_url=base_url, stac_collection=collection)


def _build_station_basins(
    station_store: StationStore, basin_store: BasinStore
) -> dict[StationId, Basin]:
    """Per-station basin map for basin-average extraction.

    Mirrors how ``run_forecast_cycle`` builds ``station_basins``: every station
    carrying a ``basin_id`` resolves to its ``Basin`` (skipping any that cannot
    be resolved, with a warning).
    """
    basins: dict[StationId, Basin] = {}
    for station in station_store.fetch_all_stations():
        if station.basin_id is None:
            continue
        basin = basin_store.fetch_basin(station.basin_id)
        if basin is not None:
            basins[station.id] = basin
        else:
            log.warning(
                "weather_history.basin_not_found",
                station_id=str(station.id),
                basin_id=str(station.basin_id),
            )
    return basins


def build_production_reanalysis_adapter(
    *,
    config: _ReanalysisStacConfig,
    station_store: StationStore,
    basin_store: BasinStore,
    clock: Callable[[], UtcDatetime],
) -> MeteoSwissOpenDataReanalysisAdapter:
    """Construct the production MeteoSwiss reanalysis adapter from config STAC
    fields, an ``httpx.Client``, an ``ExactExtractGridExtractor``, and the
    per-station basin map.

    The ``httpx.Client`` is created but only used at ``fetch_reanalysis`` time,
    so this factory is fully constructible without network access.
    """
    import httpx

    from sapphire_flow.adapters.meteoswiss_open_data_reanalysis import (
        MeteoSwissOpenDataReanalysisAdapter,
    )
    from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
        ExactExtractGridExtractor,
    )

    basins = _build_station_basins(station_store, basin_store)
    http_client = httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=None, pool=5.0)
    )
    return MeteoSwissOpenDataReanalysisAdapter(
        stac_base_url=config.stac_base_url,
        stac_collection=config.stac_collection,
        http_client=http_client,
        extractor=ExactExtractGridExtractor(),
        basins=basins,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# Prefect tasks
# ---------------------------------------------------------------------------


@task(
    name="fetch-weather-history",
    task_run_name="fetch-weather-history",
    cache_policy=NO_CACHE,
)
def _fetch_reanalysis_task(
    adapter: _ReanalysisAdapter,
    station_configs: list[StationWeatherSource],
    start: UtcDatetime,
    end: UtcDatetime,
    parameters: list[str],
) -> list[RawHistoricalForcing]:
    return adapter.fetch_reanalysis(station_configs, start, end, parameters)


@task(
    name="store-weather-history",
    task_run_name="store-weather-history",
    cache_policy=NO_CACHE,
)
def _store_forcing_task(
    forcing_store: HistoricalForcingStore,
    records: list[RawHistoricalForcing],
) -> int:
    forcing_store.store_forcing(records)
    return len(records)


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


def _reanalysis_sources(
    station_store: StationStore, nwp_source: str
) -> list[StationWeatherSource]:
    """Every REANALYSIS-role station weather-source bound to ``nwp_source``."""
    return [
        source
        for station in station_store.fetch_all_stations()
        for source in station_store.fetch_reanalysis_bindings(station.id)
        if source.nwp_source == nwp_source
    ]


@flow(name="ingest-weather-history", log_prints=False)
def ingest_weather_history_flow(
    station_store: object = None,
    forcing_store: object = None,
    basin_store: object = None,
    adapter: object = None,
    clock: object = None,
) -> WeatherHistoryIngestResult:
    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    # --- Production setup ---
    if station_store is None or forcing_store is None:
        from sapphire_flow.flows._db import setup_production_stores

        database_url = os.environ["DATABASE_URL"]
        _conn, stores = setup_production_stores(database_url)
        station_store = stores["station_store"]
        forcing_store = stores["forcing_store"]
        if basin_store is None:
            basin_store = stores["basin_store"]

    if adapter is None:
        # Scheduled/production path: build the MeteoSwiss reanalysis adapter
        # from config STAC fields + the station/basin stores. The adapter is
        # constructible without network — fetching only happens at task time.
        if station_store is None or basin_store is None:
            raise ConfigurationError(
                "ingest-weather-history requires a station_store and basin_store "
                "to build the production reanalysis adapter, but one was not "
                "available"
            )
        adapter = build_production_reanalysis_adapter(
            config=_load_reanalysis_stac_config(),
            station_store=cast("StationStore", station_store),
            basin_store=cast("BasinStore", basin_store),
            clock=cast("Callable[[], UtcDatetime]", clock),
        )

    station_store_t = cast("StationStore", station_store)
    forcing_store_t = cast("HistoricalForcingStore", forcing_store)
    adapter_t = cast("_ReanalysisAdapter", adapter)
    clock_t = cast("Callable[[], UtcDatetime]", clock)

    now = clock_t()
    start = ensure_utc(now - timedelta(days=_WINDOW_DAYS))

    log.info(
        "weather_history.starting",
        nwp_source=adapter_t.NWP_SOURCE,
        start=start.isoformat(),
        end=now.isoformat(),
    )

    configs = _reanalysis_sources(station_store_t, adapter_t.NWP_SOURCE)
    if not configs:
        log.info("weather_history.no_stations")
        return WeatherHistoryIngestResult(
            stations_targeted=0, rows_fetched=0, rows_stored=0
        )

    log.info("weather_history.stations_resolved", stations=len(configs))

    rows = _fetch_reanalysis_task(adapter_t, configs, start, now, _CANONICAL_PARAMETERS)
    log.info("weather_history.fetch_complete", rows=len(rows))

    stored = _store_forcing_task(forcing_store_t, rows) if rows else 0
    log.info("weather_history.store_complete", rows_stored=stored)

    result = WeatherHistoryIngestResult(
        stations_targeted=len(configs),
        rows_fetched=len(rows),
        rows_stored=stored,
    )
    log.info(
        "weather_history.complete",
        stations_targeted=result.stations_targeted,
        rows_fetched=result.rows_fetched,
        rows_stored=result.rows_stored,
    )
    return result
