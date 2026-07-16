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
from sapphire_flow.types.forcing_sources import ForcingSource

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

# The five canonical MeteoSwiss daily parameters this flow requests (Plan
# 115b1 §1A adds relative_sunshine_duration). Precipitation is split across
# TWO products (RhiresD/RprelimD, §0a) and is therefore requested separately
# via the product-scoped calls below — it is NOT in this list.
_NON_PRECIP_PARAMETERS: list[str] = [
    "temperature",
    "temperature_min",
    "temperature_max",
    "relative_sunshine_duration",
]
_NON_PRECIP_PRODUCTS: list[ForcingSource] = [
    ForcingSource.METEOSWISS_TABSD,
    ForcingSource.METEOSWISS_TMIND,
    ForcingSource.METEOSWISS_TMAXD,
    ForcingSource.METEOSWISS_SRELD,
]


class _ReanalysisAdapter(Protocol):
    """Structural view of the reanalysis adapter the flow needs: a source
    identity to filter station weather-sources by, the writer-side product-
    scoped fetch (Plan 115b1 §1F), and R discovery (§1D). The flow uses
    ``fetch_products`` exclusively — never the parameter-keyed
    ``fetch_reanalysis``, which fails closed on "precipitation" once RhiresD
    is registered (two products, ambiguous)."""

    NWP_SOURCE: str

    def fetch_products(
        self,
        products: list[ForcingSource],
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]: ...

    def discover_rhiresd_boundary(self) -> UtcDatetime | None: ...


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

    The ``httpx.Client`` is created but only used at fetch time (``fetch_products``
    / ``discover_rhiresd_boundary``), so this factory is fully constructible
    without network access.
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
    name="fetch-weather-history-products",
    task_run_name="fetch-weather-history-products",
    cache_policy=NO_CACHE,
)
def _fetch_products_task(
    adapter: _ReanalysisAdapter,
    products: list[ForcingSource],
    station_configs: list[StationWeatherSource],
    start: UtcDatetime,
    end: UtcDatetime,
    parameters: list[str],
) -> list[RawHistoricalForcing]:
    return adapter.fetch_products(products, station_configs, start, end, parameters)


@task(
    name="discover-rhiresd-boundary",
    task_run_name="discover-rhiresd-boundary",
    cache_policy=NO_CACHE,
)
def _discover_rhiresd_boundary_task(
    adapter: _ReanalysisAdapter,
) -> UtcDatetime | None:
    return adapter.discover_rhiresd_boundary()


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

    # Precipitation is split across two products by the discovered boundary R
    # (Plan 115b1 §0a/§1D/§1G): RhiresD (definitive) covers
    # [start, min(R+1d, now)), RprelimD (preliminary live tail) covers
    # [max(start, R+1d), now). Both spans are disjoint by construction. R is
    # None when NO RhiresD has ever been published (nothing definitive yet) —
    # then the entire window is preliminary.
    r = _discover_rhiresd_boundary_task(adapter_t)
    rhiresd_end = (
        min(ensure_utc(r + timedelta(days=1)), now) if r is not None else start
    )

    rows: list[RawHistoricalForcing] = []
    if start < rhiresd_end:
        rows.extend(
            _fetch_products_task(
                adapter_t,
                [ForcingSource.METEOSWISS_RHIRESD],
                configs,
                start,
                rhiresd_end,
                ["precipitation"],
            )
        )

    rprelimd_start = max(start, rhiresd_end)
    if rprelimd_start < now:
        rows.extend(
            _fetch_products_task(
                adapter_t,
                [ForcingSource.METEOSWISS_RPRELIMD],
                configs,
                rprelimd_start,
                now,
                ["precipitation"],
            )
        )

    rows.extend(
        _fetch_products_task(
            adapter_t,
            _NON_PRECIP_PRODUCTS,
            configs,
            start,
            now,
            _NON_PRECIP_PARAMETERS,
        )
    )
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
