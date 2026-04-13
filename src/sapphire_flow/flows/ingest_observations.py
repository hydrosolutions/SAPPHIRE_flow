from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from prefect import flow, task

from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import GaugingStatus, QcStatus, StationKind

if TYPE_CHECKING:
    from sapphire_flow.adapters.hydro_scraper import HydroScraperAdapter
    from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
    from sapphire_flow.store.observation_store import PgObservationStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcRuleSet
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import RawObservation
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class IngestResult:
    stations_polled: int
    observations_fetched: int
    observations_stored: int
    observations_skipped: int
    qc_passed: int
    qc_failed: int
    qc_suspect: int
    stations_failed: int
    errors: tuple[str, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_qc_rules() -> QcRuleSet:
    from sapphire_flow.config.qc_rules import load_qc_rules

    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is not None:
        return load_qc_rules(config_path)
    from sapphire_flow.config.qc_rules import _default_swiss_qc_rules

    return _default_swiss_qc_rules()


def _load_adapter_endpoint() -> str:
    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is None:
        return "https://lindas.admin.ch/query"
    from sapphire_flow.config.qc_rules import _resolve_env_vars

    raw_text = Path(config_path).read_text()
    data = tomllib.loads(_resolve_env_vars(raw_text))
    return (
        data.get("adapters", {})
        .get("river_stations", {})
        .get("endpoint", "https://lindas.admin.ch/query")
    )


def _setup_production_stores(
    database_url: str,
) -> tuple[object, dict[str, object]]:
    import sqlalchemy as sa

    from sapphire_flow.flows._db import make_pg_stores, run_migrations

    engine = sa.create_engine(database_url)
    run_migrations(engine)
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    stores = make_pg_stores(conn)
    return conn, stores


def _aggregate_qc_status(flags: list[object]) -> QcStatus:
    if not flags:
        return QcStatus.QC_PASSED
    if any(f.status == QcStatus.QC_FAILED for f in flags):  # type: ignore[union-attr]
        return QcStatus.QC_FAILED
    return QcStatus.QC_SUSPECT


# ---------------------------------------------------------------------------
# Prefect tasks
# ---------------------------------------------------------------------------


@task(name="fetch-observations")
def _fetch_observations_task(
    adapter: HydroScraperAdapter,
    station_configs: list[StationConfig],
    since: dict[StationId, UtcDatetime],
) -> list[RawObservation]:
    return adapter.fetch_observations(station_configs, since)


@task(name="store-raw-observations")
def _store_raw_task(
    obs_store: PgObservationStore,
    observations: list[RawObservation],
) -> int:
    ids = obs_store.store_raw_observations(observations)
    return len(ids)


@task(name="run-qc-and-update")
def _run_qc_task(
    obs_store: PgObservationStore,
    baseline_store: PgClimBaselineStore,
    station_id: StationId,
    parameter: str,
    qc_rules: QcRuleSet,
    now: UtcDatetime,
    context_window_hours: float = 2.0,
) -> dict[str, int]:
    window_start = ensure_utc(now - timedelta(hours=context_window_hours))
    window_end = ensure_utc(now + timedelta(hours=1))

    all_obs = obs_store.fetch_observations(
        station_id=station_id,
        parameter=parameter,
        start=window_start,
        end=window_end,
    )
    if not all_obs:
        return {"passed": 0, "failed": 0, "suspect": 0}

    raw_obs = [o for o in all_obs if o.qc_status == QcStatus.RAW]
    if not raw_obs:
        return {"passed": 0, "failed": 0, "suspect": 0}

    raw_ids = {o.id for o in raw_obs}

    baselines = baseline_store.fetch_baselines(station_id, parameter)

    checker = Stage1QualityChecker()
    flags = checker.check(
        observations=all_obs,
        rule_set=qc_rules,
        overrides=[],
        baselines=baselines,
    )

    counts: dict[str, int] = {"passed": 0, "failed": 0, "suspect": 0}
    for obs_id, obs_flags in flags.items():
        if obs_id not in raw_ids:
            continue
        status = _aggregate_qc_status(obs_flags)
        obs_store.update_qc(obs_id, status, obs_flags, qc_rule_version="1.0")
        if status == QcStatus.QC_PASSED:
            counts["passed"] += 1
        elif status == QcStatus.QC_FAILED:
            counts["failed"] += 1
        else:
            counts["suspect"] += 1

    return counts


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


@flow(name="ingest-observations", log_prints=False)
def ingest_observations_flow(
    station_store: object = None,
    obs_store: object = None,
    baseline_store: object = None,
    alert_store: object = None,
    adapter: object = None,
    qc_rules: object = None,
    deployment_config: object = None,
    clock: object = None,
    context_window_hours: float = 2.0,
    default_lookback_hours: float = 1.0,
) -> IngestResult:
    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    # --- Production setup ---
    _conn: object = None
    if station_store is None:
        database_url = os.environ["DATABASE_URL"]
        _conn, stores = _setup_production_stores(database_url)
        station_store = stores["station_store"]  # type: ignore[assignment]
        obs_store = stores["obs_store"]  # type: ignore[assignment]
        baseline_store = stores["baseline_store"]  # type: ignore[assignment]
        alert_store = stores["alert_store"]  # type: ignore[assignment]

    if adapter is None:
        import httpx

        from sapphire_flow.adapters.hydro_scraper import HydroScraperAdapter

        endpoint = _load_adapter_endpoint()
        adapter = HydroScraperAdapter(
            endpoint=endpoint,
            http_client=httpx.Client(timeout=30.0),
        )

    if qc_rules is None:
        qc_rules = _load_qc_rules()

    if deployment_config is None:
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.deployment import load_config

            deployment_config = load_config(config_path)

    assert station_store is not None
    assert obs_store is not None
    assert baseline_store is not None

    now: UtcDatetime = clock()  # type: ignore[assignment]

    # --- Step 2.0: Fetch eligible stations (RIVER + LAKE) ---
    river_stations = station_store.fetch_all_stations(kind=StationKind.RIVER)
    lake_stations = station_store.fetch_all_stations(kind=StationKind.LAKE)
    all_stations = [*river_stations, *lake_stations]
    eligible = [
        s
        for s in all_stations
        if s.gauging_status == GaugingStatus.GAUGED
        and s.station_status.value == "operational"
    ]

    if not eligible:
        log.info("ingest.no_stations")
        return IngestResult(
            stations_polled=0,
            observations_fetched=0,
            observations_stored=0,
            observations_skipped=0,
            qc_passed=0,
            qc_failed=0,
            qc_suspect=0,
            stations_failed=0,
            errors=(),
        )

    log.info("ingest.starting", stations=len(eligible))

    # --- Build since dict ---
    default_since = ensure_utc(now - timedelta(hours=default_lookback_hours))
    since: dict[StationId, UtcDatetime] = {}
    for station in eligible:
        param = (
            "water_level" if station.station_kind == StationKind.LAKE else "discharge"
        )
        latest = obs_store.fetch_latest_timestamp(station.id, param)
        since[station.id] = latest if latest is not None else default_since

    # --- Step 2.1: Fetch observations ---
    raw_obs = _fetch_observations_task(adapter, eligible, since)
    log.info("ingest.fetch_complete", observations=len(raw_obs))

    if not raw_obs:
        log.info("ingest.no_new_data")
        return IngestResult(
            stations_polled=len(eligible),
            observations_fetched=0,
            observations_stored=0,
            observations_skipped=0,
            qc_passed=0,
            qc_failed=0,
            qc_suspect=0,
            stations_failed=0,
            errors=(),
        )

    # --- Step 2.2: Store raw observations ---
    stored_count = _store_raw_task(obs_store, raw_obs)
    skipped_count = len(raw_obs) - stored_count
    log.info("ingest.store_complete", stored=stored_count, skipped=skipped_count)

    # --- Steps 2.3–2.4: QC per (station, parameter) ---
    station_params: set[tuple[StationId, str]] = {
        (o.station_id, o.parameter) for o in raw_obs
    }

    totals = {"passed": 0, "failed": 0, "suspect": 0}
    errors: list[str] = []

    for station_id, parameter in station_params:
        try:
            counts = _run_qc_task(
                obs_store,
                baseline_store,
                station_id,
                parameter,
                qc_rules=qc_rules,
                now=now,
                context_window_hours=context_window_hours,
            )
            totals["passed"] += counts["passed"]
            totals["failed"] += counts["failed"]
            totals["suspect"] += counts["suspect"]
        except Exception as exc:
            log.warning(
                "ingest.qc_failed",
                station_id=str(station_id),
                parameter=parameter,
                error=str(exc),
            )
            errors.append(f"QC failed for {station_id}/{parameter}: {exc}")

    log.info(
        "ingest.qc_complete",
        passed=totals["passed"],
        failed=totals["failed"],
        suspect=totals["suspect"],
    )

    # --- Steps 2.8–2.10: Observation alerts (v0: disabled by default) ---
    if deployment_config is not None and deployment_config.enable_observation_alerts:
        from sapphire_flow.services.observation_alert_checker import (
            check_observation_alerts,
        )

        assert alert_store is not None
        check_observation_alerts(
            station_params=station_params,
            obs_store=obs_store,
            station_store=station_store,
            alert_store=alert_store,
            now=now,
        )
    else:
        log.debug("ingest.observation_alerts_disabled")

    # --- Result ---
    result = IngestResult(
        stations_polled=len(eligible),
        observations_fetched=len(raw_obs),
        observations_stored=stored_count,
        observations_skipped=skipped_count,
        qc_passed=totals["passed"],
        qc_failed=totals["failed"],
        qc_suspect=totals["suspect"],
        stations_failed=len(errors),
        errors=tuple(errors),
    )

    log.info(
        "ingest.complete",
        stations_polled=result.stations_polled,
        observations_fetched=result.observations_fetched,
        observations_stored=result.observations_stored,
        observations_skipped=result.observations_skipped,
        qc_passed=result.qc_passed,
        qc_failed=result.qc_failed,
        qc_suspect=result.qc_suspect,
        stations_failed=result.stations_failed,
    )

    return result
