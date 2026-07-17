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

Plan 115b4 §6A/§6B: emits one ``PipelineHealthRecord``
(``check_type=WEATHER_HISTORY_INGEST``) per run, measured by EFFECT — a real
DB readback (``HistoricalForcingStore.fetch_latest_valid_time``) — never the
flow's own ``rows_stored`` counter, which reports ``len(records)`` even for a
pure-duplicate re-fetch.
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
from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus
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


def _append_weather_history_health_record(
    pipeline_health_store: object | None,
    *,
    checked_at: UtcDatetime,
    status: PipelineHealthStatus,
    detail: dict[str, object],
) -> None:
    # Best-effort heartbeat, mirrors run_forecast_cycle._append_pipeline_health_record
    # / collect_bafu_forecasts._append_bafu_health_record — a health-write
    # failure must never fail the ingest run itself.
    if pipeline_health_store is None:
        return
    append = getattr(pipeline_health_store, "append_health_record", None)
    if not callable(append):
        return

    from sapphire_flow.types.pipeline import PipelineHealthRecord

    try:
        append(
            PipelineHealthRecord(
                check_type=PipelineCheckType.WEATHER_HISTORY_INGEST,
                checked_at=checked_at,
                status=status,
                subject="weather_history_ingest",
                detail=detail,
                cycle_time=None,
                created_at=checked_at,
            )
        )
    except Exception as exc:
        log.warning(
            "pipeline.health_record_write_failed",
            check_type=PipelineCheckType.WEATHER_HISTORY_INGEST.value,
            subject="weather_history_ingest",
            error=str(exc),
        )


def _horizon_present(
    forcing_store: HistoricalForcingStore,
    *,
    station_ids: list[StationId],
    sources: list[ForcingSource],
    start: UtcDatetime,
    end: UtcDatetime,
) -> bool:
    """Health-by-EFFECT (Plan 115b4 §6B): True iff the store ACTUALLY holds at
    least one row, for at least one of ``sources``, within [start, end) —
    a real DB readback via ``fetch_latest_valid_time``'s O(1)-per-source
    aggregate, never the flow's own ``rows_stored`` counter (which is
    ``len(records)`` post ``on_conflict_do_nothing`` and looks healthy even
    when nothing new landed for a store that has never held anything).
    """
    return any(
        forcing_store.fetch_latest_valid_time(station_ids, source.value, start, end)
        is not None
        for source in sources
    )


@flow(name="ingest-weather-history", log_prints=False)
def ingest_weather_history_flow(
    station_store: object = None,
    forcing_store: object = None,
    basin_store: object = None,
    adapter: object = None,
    clock: object = None,
    # Plan 082 Task 3B item 4: parametric backfill window for multi-year
    # Nepal historical back-extraction (e.g. window_days=730). None keeps
    # the Swiss rolling-ingest default (_WINDOW_DAYS = 60) unchanged.
    window_days: int | None = None,
    pipeline_health_store: object | None = None,
) -> WeatherHistoryIngestResult:
    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731
    effective_window_days = window_days if window_days is not None else _WINDOW_DAYS

    # --- Production setup ---
    if station_store is None or forcing_store is None:
        from sapphire_flow.flows._db import setup_production_stores

        database_url = os.environ["DATABASE_URL"]
        _conn, stores = setup_production_stores(database_url)
        station_store = stores["station_store"]
        forcing_store = stores["forcing_store"]
        if basin_store is None:
            basin_store = stores["basin_store"]
        if pipeline_health_store is None:
            pipeline_health_store = stores["pipeline_health_store"]

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
    start = ensure_utc(now - timedelta(days=effective_window_days))

    log.info(
        "weather_history.starting",
        nwp_source=adapter_t.NWP_SOURCE,
        start=start.isoformat(),
        end=now.isoformat(),
    )

    configs = _reanalysis_sources(station_store_t, adapter_t.NWP_SOURCE)
    if not configs:
        log.info("weather_history.no_stations")
        # Health-by-EFFECT (Plan 115b4 §6B): zero bound stations is a config
        # fault, distinct from "bound but the store's horizon is empty".
        _append_weather_history_health_record(
            pipeline_health_store,
            checked_at=now,
            status=PipelineHealthStatus.CRITICAL,
            detail={"reason": "no_stations_bound"},
        )
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

    # Every product actually targeted this run — used both for the fetch
    # calls below AND the post-store health-by-effect readback (§6B), so the
    # two never drift out of sync.
    targeted_products: list[ForcingSource] = []

    rows: list[RawHistoricalForcing] = []
    if start < rhiresd_end:
        targeted_products.append(ForcingSource.METEOSWISS_RHIRESD)
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
        targeted_products.append(ForcingSource.METEOSWISS_RPRELIMD)
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

    targeted_products.extend(_NON_PRECIP_PRODUCTS)
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

    # Health-by-EFFECT (Plan 115b4 §6B): a real DB readback, never
    # ``rows_stored`` (which reports len(records) regardless of whether
    # anything was actually persisted).
    station_ids = [cfg.station_id for cfg in configs]
    horizon_present = _horizon_present(
        forcing_store_t,
        station_ids=station_ids,
        sources=targeted_products,
        start=start,
        end=now,
    )
    _append_weather_history_health_record(
        pipeline_health_store,
        checked_at=now,
        status=(
            PipelineHealthStatus.OK
            if horizon_present
            else PipelineHealthStatus.CRITICAL
        ),
        detail=(
            {"stations_targeted": len(configs), "rows_stored": stored}
            if horizon_present
            else {
                "reason": "no_horizon_advance",
                "stations_targeted": len(configs),
                "rows_stored": stored,
            }
        ),
    )

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
