"""Dedicated recap-Gateway snow-reanalysis ingest flow (Plan 146 D1/D2/D5/D7).

Mirrors ``flows/ingest_weather_history.py``'s shape (injected stores/adapter/
clock, a fixed-rolling-window ``@flow``, health-by-EFFECT via a real DB
readback) but owns its own dedicated primitives because the two feeds differ
in three ways this module encodes directly rather than sharing code with the
sibling:

* the adapter is ``RecapGatewayReanalysisAdapter.fetch_snow_reanalysis`` (a
  NEW non-Protocol method, Plan 146 D5/2a) — NOT ``fetch_products``;
* the flow is MODEL-AGNOSTIC (Plan 146 D5, LOCKED 2026-07-24): it fetches the
  full snow-variable ceiling for every in-scope HRU every run, with per-HRU
  *subscription* discovered at runtime via
  ``RecapSnowUnavailableError.code`` — no ``ModelStore``/``StationGroupStore``
  injection, no requirement resolution;
* health-by-EFFECT uses the FINER ``HistoricalForcingStore.fetch_covered_days``
  primitive (per ``(station_id, parameter)``), not
  ``fetch_latest_valid_time`` — all three snow variables share ONE ``source``
  literal, so a single collapsed ``MAX(valid_time)`` would mask a
  silently-stalled key behind healthy ones (Plan 146 D5 finding #1).

No watermark (Plan 146 D2): each run re-fetches a fixed rolling window
``[clock() - window_days, clock())``; idempotency is free via the store's
``on_conflict_do_nothing()`` upsert.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

import structlog
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from sapphire_flow.adapters.recap_gateway import SNOW_CANONICAL_PARAMETERS
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    PipelineCheckType,
    PipelineHealthStatus,
    SpatialRepresentation,
)
from sapphire_flow.types.forcing_sources import ForcingSource
from sapphire_flow.types.ids import StationId

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date

    from sapphire_flow.adapters.recap_gateway import GatewayPolygonBindingStoreLike
    from sapphire_flow.protocols.stores import HistoricalForcingStore, StationStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.weather import SnowReanalysisFetchResult

log = structlog.get_logger(__name__)

# Snow ceiling default (D1): fetched IN FULL every run, not model-scoped.
DEFAULT_VARIABLES: tuple[str, ...] = ("swe", "snow_depth", "snowmelt")

# Safely exceeds JSNOW's ~7-day reanalysis lag (D2).
DEFAULT_WINDOW_DAYS = 21

_RECAP_REANALYSIS_NWP_SOURCE = "era5_land"  # RecapGatewayReanalysisAdapter.NWP_SOURCE
_CHECK_TYPE = PipelineCheckType.RECAP_SNOW_REANALYSIS_INGEST
_SUBJECT = "recap_snow_reanalysis_ingest"


class _SnowReanalysisAdapter(Protocol):
    """Structural view of the adapter capability this flow needs."""

    def fetch_snow_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        variables: list[str] | None = None,
    ) -> SnowReanalysisFetchResult: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class RecapSnowReanalysisIngestResult:
    stations_targeted: int
    rows_fetched: int
    rows_stored: int
    status: PipelineHealthStatus


# ---------------------------------------------------------------------------
# Boundary validation (Plan 146 D5 point 2 item B / D2 boundary validation)
# ---------------------------------------------------------------------------


def _validate_variables(variables: tuple[str, ...]) -> None:
    if not variables:
        raise ConfigurationError(
            "ingest-recap-reanalysis 'variables' ceiling must be non-empty"
        )
    if len(set(variables)) != len(variables):
        raise ConfigurationError(
            f"ingest-recap-reanalysis 'variables' ceiling has duplicate entries: "
            f"{variables!r}"
        )
    unknown = [v for v in variables if v not in SNOW_CANONICAL_PARAMETERS]
    if unknown:
        raise ConfigurationError(
            f"ingest-recap-reanalysis 'variables' ceiling has unknown entries "
            f"{unknown!r}; must be a subset of {sorted(SNOW_CANONICAL_PARAMETERS)!r}"
        )


def _validate_window_days(window_days: int) -> None:
    if window_days <= 0:
        raise ConfigurationError(
            f"ingest-recap-reanalysis 'window_days' must be > 0, got {window_days}"
        )


def _parse_station_ids(station_ids: list[str] | None) -> list[StationId] | None:
    if station_ids is None:
        return None
    parsed: list[StationId] = []
    for raw in station_ids:
        try:
            parsed.append(StationId(UUID(raw)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ConfigurationError(
                f"ingest-recap-reanalysis 'station_ids' entry {raw!r} is not a "
                "valid station id"
            ) from exc
    return parsed


# ---------------------------------------------------------------------------
# Station resolution (mirrors ingest_weather_history's `_reanalysis_sources`)
# ---------------------------------------------------------------------------


def _reanalysis_sources(
    station_store: StationStore,
    nwp_source: str,
    *,
    station_ids: list[StationId] | None,
) -> list[StationWeatherSource]:
    allowed = set(station_ids) if station_ids is not None else None
    return [
        source
        for station in station_store.fetch_all_stations()
        if allowed is None or station.id in allowed
        for source in station_store.fetch_reanalysis_bindings(station.id)
        if source.nwp_source == nwp_source
    ]


# ---------------------------------------------------------------------------
# Health-by-EFFECT (Plan 146 D5 finding #1): fetch_covered_days readback
# ---------------------------------------------------------------------------


def _snapshot_covered_days(
    forcing_store: HistoricalForcingStore,
    *,
    station_ids: list[StationId],
    variables: tuple[str, ...],
    start: UtcDatetime,
    end: UtcDatetime,
) -> dict[str, dict[StationId, set[date]]]:
    return {
        variable: forcing_store.fetch_covered_days(
            station_ids,
            ForcingSource.RECAP_SNOW_REANALYSIS.value,
            variable,
            SpatialRepresentation.BASIN_AVERAGE,
            start,
            end,
        )
        for variable in variables
    }


@dataclass(frozen=True, kw_only=True, slots=True)
class _Classification:
    status: PipelineHealthStatus
    detail: dict[str, object]


def _classify_run(
    result: SnowReanalysisFetchResult,
    *,
    attempted_variables: tuple[str, ...],
    pre_resolution_station_ids: list[StationId],
    before: dict[str, dict[StationId, set[date]]],
    after: dict[str, dict[StationId, set[date]]],
) -> _Classification:
    # Reconciliation invariant (Plan 146 D5): every pre-resolution in-scope
    # station must land in EITHER `resolved` OR `skipped` — never neither.
    unexplained = [
        sid
        for sid in pre_resolution_station_ids
        if sid not in result.resolved and sid not in result.skipped
    ]

    stalled: list[str] = []
    subscription_not_found: list[str] = []
    for hru_name, variable_set in result.attempted.items():
        stations_for_hru = [
            sid for sid, hru in result.resolved.items() if hru == hru_name
        ]
        gaps = result.unavailable.get(hru_name, {})
        for variable in variable_set:
            code = gaps.get(variable)
            if code == "subscription_not_found":
                subscription_not_found.append(f"{hru_name}/{variable}")
                continue
            # Plan 146 fold #1: per-(station, parameter) granularity — a
            # station that did not advance must stall on its own, never be
            # masked by a sibling station (same HRU) that did advance.
            after_variable = after.get(variable, {})
            before_variable = before.get(variable, {})
            for sid in stations_for_hru:
                station_advanced = after_variable.get(sid, set()) > before_variable.get(
                    sid, set()
                )
                if not station_advanced:
                    stalled.append(f"{hru_name}/{variable} (station {sid})")

    if subscription_not_found:
        log.info(
            "recap_snow_reanalysis.subscription_not_found",
            keys=subscription_not_found,
        )

    dropped = [str(sid) for sid in result.skipped] + [str(sid) for sid in unexplained]

    # Plan 146 fold #2: a stalled key must never hide a dropped/unresolved
    # in-scope station (and vice versa) — surface BOTH when both fire,
    # rather than the first condition checked winning the return.
    reasons: list[str] = []
    detail: dict[str, object] = {}
    if stalled:
        reasons.append("no_horizon_advance")
        detail["stalled_keys"] = stalled
        detail["variables"] = list(attempted_variables)
    if dropped:
        reasons.append("station_resolution_dropped")
        detail["dropped_stations"] = dropped
        detail["skipped"] = {str(k): v for k, v in result.skipped.items()}

    if reasons:
        detail["reason"] = reasons[0] if len(reasons) == 1 else reasons
        return _Classification(status=PipelineHealthStatus.WARNING, detail=detail)
    return _Classification(
        status=PipelineHealthStatus.OK,
        detail={"variables": list(attempted_variables)},
    )


def _append_health_record(
    pipeline_health_store: object | None,
    *,
    checked_at: UtcDatetime,
    status: PipelineHealthStatus,
    detail: dict[str, object],
) -> None:
    # Best-effort heartbeat (Plan 146 D5a) — mirrors
    # `_append_weather_history_health_record`: a write failure or a missing
    # store must never fail the ingest run itself.
    if pipeline_health_store is None:
        return
    append = getattr(pipeline_health_store, "append_health_record", None)
    if not callable(append):
        return

    from sapphire_flow.types.pipeline import PipelineHealthRecord

    try:
        append(
            PipelineHealthRecord(
                check_type=_CHECK_TYPE,
                checked_at=checked_at,
                status=status,
                subject=_SUBJECT,
                detail=detail,
                cycle_time=None,
                created_at=checked_at,
            )
        )
    except Exception as exc:
        log.warning(
            "pipeline.health_record_write_failed",
            check_type=_CHECK_TYPE.value,
            subject=_SUBJECT,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Production adapter construction (Plan 146 D5a — ORDERED, Recap-side LAST)
# ---------------------------------------------------------------------------


def _build_production_snow_adapter(
    gateway_polygon_store: GatewayPolygonBindingStoreLike | None,
) -> _SnowReanalysisAdapter:
    """Build the Recap-side adapter. ONLY called once the in-scope station set
    is known to be non-empty (D5a step 4) — never touches Recap config/key
    before that gate."""
    from sapphire_flow.adapters.recap_gateway import (
        RecapClientLike,
        RecapGatewayReanalysisAdapter,
        StoreBackedGatewayPolygonResolver,
    )
    from sapphire_flow.config.recap_gateway import (
        build_recap_client_config,
        load_recap_api_key,
        load_recap_gateway_config,
    )

    if gateway_polygon_store is None:
        raise ConfigurationError(
            "ingest-recap-reanalysis requires a gateway_polygon_store (§5a "
            "table reader) but none was available"
        )
    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is None:
        raise ConfigurationError(
            "ingest-recap-reanalysis requires SAPPHIRE_CONFIG with an "
            "[adapters.recap_gateway] section but SAPPHIRE_CONFIG is unset"
        )

    from recap_client import RecapClient

    recap_gateway_config = load_recap_gateway_config(Path(config_path))
    client_config = build_recap_client_config(
        api_key=load_recap_api_key(), config=recap_gateway_config
    )
    recap_client = RecapClient(client_config)
    return RecapGatewayReanalysisAdapter(
        client=cast("RecapClientLike", recap_client),
        resolver=StoreBackedGatewayPolygonResolver(gateway_polygon_store),
    )


# ---------------------------------------------------------------------------
# Prefect tasks
# ---------------------------------------------------------------------------


@task(
    name="fetch-snow-reanalysis",
    task_run_name="fetch-snow-reanalysis",
    cache_policy=NO_CACHE,
)
def _fetch_snow_reanalysis_task(
    adapter: _SnowReanalysisAdapter,
    station_configs: list[StationWeatherSource],
    start: UtcDatetime,
    end: UtcDatetime,
    variables: list[str],
) -> SnowReanalysisFetchResult:
    return adapter.fetch_snow_reanalysis(station_configs, start, end, variables)


@task(
    name="store-snow-reanalysis",
    task_run_name="store-snow-reanalysis",
    cache_policy=NO_CACHE,
)
def _store_snow_reanalysis_task(
    forcing_store: HistoricalForcingStore,
    records: list[object],
) -> int:
    forcing_store.store_forcing(records)  # type: ignore[arg-type]
    return len(records)


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


@flow(name="ingest-recap-reanalysis", log_prints=False)
def ingest_recap_reanalysis_flow(
    station_store: object = None,
    forcing_store: object = None,
    gateway_polygon_store: object = None,
    pipeline_health_store: object | None = None,
    adapter: object = None,
    clock: object = None,
    variables: tuple[str, ...] = DEFAULT_VARIABLES,
    window_days: int = DEFAULT_WINDOW_DAYS,
    station_ids: list[str] | None = None,
) -> RecapSnowReanalysisIngestResult:
    _validate_variables(variables)
    _validate_window_days(window_days)
    parsed_station_ids = _parse_station_ids(station_ids)

    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731
    clock_t = cast("Callable[[], UtcDatetime]", clock)
    now = clock_t()
    start = ensure_utc(now - timedelta(days=window_days))

    # --- Production setup (D5a step 1: cheap, Recap-independent) ---
    if station_store is None or forcing_store is None:
        from sapphire_flow.flows._db import setup_production_stores

        database_url = os.environ["DATABASE_URL"]
        _conn, stores = setup_production_stores(database_url)
        station_store = stores["station_store"]
        forcing_store = stores["forcing_store"]
        if gateway_polygon_store is None:
            gateway_polygon_store = stores["gateway_polygon_store"]
        if pipeline_health_store is None:
            pipeline_health_store = stores["pipeline_health_store"]

    station_store_t = cast("StationStore", station_store)
    forcing_store_t = cast("HistoricalForcingStore", forcing_store)

    # D5a step 2: resolve in-scope stations. Still no Recap dependency.
    in_scope = _reanalysis_sources(
        station_store_t,
        _RECAP_REANALYSIS_NWP_SOURCE,
        station_ids=parsed_station_ids,
    )

    if not in_scope:
        # D5a step 3: benign no-op — no Recap config/key/adapter touched.
        log.info("recap_snow_reanalysis.no_stations")
        _append_health_record(
            pipeline_health_store,
            checked_at=now,
            status=PipelineHealthStatus.OK,
            detail={"reason": "no_stations_bound"},
        )
        return RecapSnowReanalysisIngestResult(
            stations_targeted=0,
            rows_fetched=0,
            rows_stored=0,
            status=PipelineHealthStatus.OK,
        )

    log.info(
        "recap_snow_reanalysis.starting",
        stations=len(in_scope),
        start=start.isoformat(),
        end=now.isoformat(),
        variables=list(variables),
    )

    station_ids_all = [cfg.station_id for cfg in in_scope]
    before = _snapshot_covered_days(
        forcing_store_t,
        station_ids=station_ids_all,
        variables=variables,
        start=start,
        end=now,
    )

    # D5a step 4: build the Recap side ONLY now that in_scope is non-empty.
    if adapter is None:
        adapter = _build_production_snow_adapter(
            cast("GatewayPolygonBindingStoreLike | None", gateway_polygon_store)
        )
    adapter_t = cast("_SnowReanalysisAdapter", adapter)

    result = _fetch_snow_reanalysis_task(
        adapter_t, in_scope, start, now, list(variables)
    )
    log.info("recap_snow_reanalysis.fetch_complete", rows=len(result.rows))

    stored = (
        _store_snow_reanalysis_task(forcing_store_t, cast("list[object]", result.rows))
        if result.rows
        else 0
    )
    log.info("recap_snow_reanalysis.store_complete", rows_stored=stored)

    after = _snapshot_covered_days(
        forcing_store_t,
        station_ids=station_ids_all,
        variables=variables,
        start=start,
        end=now,
    )

    classification = _classify_run(
        result,
        attempted_variables=variables,
        pre_resolution_station_ids=station_ids_all,
        before=before,
        after=after,
    )
    _append_health_record(
        pipeline_health_store,
        checked_at=now,
        status=classification.status,
        detail=classification.detail,
    )

    log.info(
        "recap_snow_reanalysis.complete",
        stations_targeted=len(in_scope),
        rows_fetched=len(result.rows),
        rows_stored=stored,
        status=classification.status.value,
    )
    return RecapSnowReanalysisIngestResult(
        stations_targeted=len(in_scope),
        rows_fetched=len(result.rows),
        rows_stored=stored,
        status=classification.status,
    )
