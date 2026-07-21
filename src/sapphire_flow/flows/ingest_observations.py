from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from prefect import flow, task
from prefect.cache_policies import NO_CACHE
from prefect.runtime import flow_run, task_run

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.qc import Stage1QualityChecker
from sapphire_flow.services.qc_datum import (
    add_observation_datum_details,
    obs_qc_rule_version,
    obs_skipped_rules,
    shift_observations_for_water_level_datum,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import GaugingStatus, QcStatus, StationKind

if TYPE_CHECKING:
    from sapphire_flow.adapters.hydro_scraper import HydroScraperAdapter
    from sapphire_flow.store.calculated_station_formula_store import PgFormulaStore
    from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
    from sapphire_flow.store.observation_store import PgObservationStore
    from sapphire_flow.store.station_store import PgStationStore
    from sapphire_flow.types.calculated_station import ComponentWeight
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcRuleSet
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import Observation, RawObservation
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
    # Plan 015 step 2.5 — calculated-station derivation (0 when no calculated stations).
    observations_derived: int = 0
    observations_missing: int = 0


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
    from typing import Any, cast

    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is None:
        return "https://lindas.admin.ch/query"
    from sapphire_flow.config._overlay import (
        _resolve_overlay_paths,  # pyright: ignore[reportPrivateUsage]
        load_merged_toml,
    )

    # Cast to dict[str, Any] — post-parse code treats TOML values loosely
    # (same behaviour as the prior tomllib.loads return type).
    data = cast(
        "dict[str, Any]",
        load_merged_toml(Path(config_path), _resolve_overlay_paths()),
    )
    return (
        data.get("adapters", {})
        .get("river_stations", {})
        .get("endpoint", "https://lindas.admin.ch/query")
    )


def _aggregate_qc_status(flags: list[object]) -> QcStatus:
    if not flags:
        return QcStatus.QC_PASSED
    if any(f.status == QcStatus.QC_FAILED for f in flags):  # type: ignore[union-attr]
        return QcStatus.QC_FAILED
    return QcStatus.QC_SUSPECT


# ---------------------------------------------------------------------------
# Prefect tasks
# ---------------------------------------------------------------------------


def _resolve_fetch_observations_run_name() -> str:
    params = task_run.parameters or {}
    since = params.get("since") or {}
    if since:
        earliest = min(since.values())
        return f"fetch-observations-{earliest:%Y-%m-%dT%H}"
    scheduled = task_run.scheduled_start_time
    if scheduled is not None:
        return f"fetch-observations-{scheduled:%Y-%m-%dT%H}"
    return "fetch-observations"


@task(
    name="fetch-observations",
    task_run_name=_resolve_fetch_observations_run_name,
    cache_policy=NO_CACHE,
)
def _fetch_observations_task(
    adapter: HydroScraperAdapter,
    station_configs: list[StationConfig],
    since: dict[StationId, UtcDatetime],
) -> list[RawObservation]:
    return adapter.fetch_observations(station_configs, since)


@task(
    name="store-raw-observations",
    task_run_name="store-raw-observations",
    cache_policy=NO_CACHE,
)
def _store_raw_task(
    obs_store: PgObservationStore,
    observations: list[RawObservation],
) -> int:
    ids = obs_store.store_raw_observations(observations)
    return len(ids)


@task(
    name="run-qc-and-update",
    task_run_name="run-qc-{station_id}-{parameter}",
    cache_policy=NO_CACHE,
)
def _run_qc_task(
    obs_store: PgObservationStore,
    baseline_store: PgClimBaselineStore,
    station_id: StationId,
    parameter: str,
    qc_rules: QcRuleSet,
    now: UtcDatetime,
    datum: float | None = None,
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

    qc_observations = shift_observations_for_water_level_datum(
        all_obs,
        parameter=parameter,
        datum=datum,
    )
    checker = Stage1QualityChecker()
    flags = checker.check(
        observations=qc_observations,
        rule_set=qc_rules,
        overrides=[],
        baselines=baselines,
        skipped_rule_ids=obs_skipped_rules(parameter, datum),
    )
    flags = add_observation_datum_details(
        flags,
        raw_observations=all_obs,
        shifted_observations=qc_observations,
        parameter=parameter,
        datum=datum,
    )

    counts: dict[str, int] = {"passed": 0, "failed": 0, "suspect": 0}
    version = obs_qc_rule_version(parameter, datum)
    raw_by_id = {obs.id: obs for obs in all_obs}
    for obs_id, obs_flags in flags.items():
        if obs_id not in raw_ids:
            continue
        status = _aggregate_qc_status(obs_flags)
        obs_store.update_qc(obs_id, status, obs_flags, qc_rule_version=version)
        for flag in obs_flags:
            if flag.status == QcStatus.QC_FAILED:
                obs = raw_by_id[obs_id]
                log.debug(
                    "qc.rejected",
                    station_id=str(station_id),
                    parameter=parameter,
                    rule_id=flag.rule_id,
                    value=obs.value,
                    threshold=flag.detail,
                )
        if status == QcStatus.QC_PASSED:
            counts["passed"] += 1
        elif status == QcStatus.QC_FAILED:
            counts["failed"] += 1
        else:
            counts["suspect"] += 1

    return counts


def _component_eligible(cfg: StationConfig | None) -> bool:
    # Read-time defensive check (Plan 015 §Tiered Derivation): the formula trigger is a
    # write-time guarantee only. A component that is no longer gauged+operational is
    # treated exactly like a missing observation.
    return (
        cfg is not None
        and cfg.gauging_status == GaugingStatus.GAUGED
        and cfg.station_status.value == "operational"
    )


def _skip_reason(
    weights: list[ComponentWeight],
    best_by_component: dict[StationId, dict[UtcDatetime, Observation]],
    eligible: dict[StationId, bool],
    timestamp: UtcDatetime,
) -> str:
    for weight in weights:
        cid = weight.component_station_id
        if not eligible[cid]:
            return f"component {cid} not gauged+operational"
        obs = best_by_component[cid].get(timestamp)
        if obs is None:
            return f"component {cid} missing observation"
        if obs.qc_status not in (QcStatus.QC_PASSED, QcStatus.QC_SUSPECT):
            return f"component {cid} qc_status={obs.qc_status.value}"
    return "unknown"


@task(
    name="derive-calculated-stations",
    task_run_name="derive-calculated-stations",
    cache_policy=NO_CACHE,
)
def _derive_calculated_task(
    obs_store: PgObservationStore,
    formula_store: PgFormulaStore,
    station_store: PgStationStore,
    calculated: list[StationConfig],
    raw_obs: list[RawObservation],
    now: UtcDatetime,
) -> dict[str, int]:
    from uuid import uuid4

    from sapphire_flow.services.component_derivation import (
        DERIVATION_RULE_VERSION,
        derive_point,
        select_by_precedence,
    )
    from sapphire_flow.types.enums import ObservationSource
    from sapphire_flow.types.ids import ObservationId
    from sapphire_flow.types.observation import Observation

    counts = {"derived": 0, "missing": 0}
    formulas = formula_store.fetch_formulas_for_stations([s.id for s in calculated])
    if not formulas:
        return counts

    # Re-read current component status at derivation time (§Tiered Derivation): Flow 2
    # runs in AUTOCOMMIT, so the step-2.0 snapshot may be stale if a component was
    # suspended/decommissioned between selection and derivation.
    station_by_id: dict[StationId, StationConfig] = {
        s.id: s for s in station_store.fetch_all_stations()
    }

    to_store: list[Observation] = []
    for (calc_id, parameter), weights in formulas.items():
        component_ids = [w.component_station_id for w in weights]
        component_id_set = set(component_ids)
        eligible = {
            cid: _component_eligible(station_by_id.get(cid)) for cid in component_ids
        }
        # Derivation window = the timestamps some component reported this run for the
        # formula's parameter. Empty ⇒ nothing to derive (never a placeholder for a
        # timestamp no component reported).
        candidate_ts = sorted(
            {
                o.timestamp
                for o in raw_obs
                if o.station_id in component_id_set and o.parameter == parameter
            }
        )
        if not candidate_ts:
            continue

        window_start = candidate_ts[0]
        window_end = ensure_utc(candidate_ts[-1] + timedelta(seconds=1))
        best_by_component: dict[StationId, dict[UtcDatetime, Observation]] = {}
        for cid in component_ids:
            if not eligible[cid]:
                best_by_component[cid] = {}
                continue
            grouped: dict[UtcDatetime, list[Observation]] = {}
            for row in obs_store.fetch_observations(
                station_id=cid,
                parameter=parameter,
                start=window_start,
                end=window_end,
            ):
                grouped.setdefault(row.timestamp, []).append(row)
            best_by_component[cid] = {
                ts: best
                for ts, group in grouped.items()
                if (best := select_by_precedence(group)) is not None
            }

        for ts in candidate_ts:
            resolved = [
                (w, best_by_component[w.component_station_id].get(ts)) for w in weights
            ]
            point = derive_point(resolved)
            to_store.append(
                Observation(
                    id=ObservationId(uuid4()),
                    station_id=calc_id,
                    timestamp=ts,
                    parameter=parameter,
                    value=point.value,
                    source=ObservationSource.COMPONENT_DERIVED,
                    rating_curve_id=None,
                    rating_curve_correction_version=None,
                    qc_status=point.qc_status,
                    qc_flags=point.qc_flags,
                    qc_rule_version=(
                        DERIVATION_RULE_VERSION if point.value is not None else None
                    ),
                    created_at=now,
                )
            )
            if point.value is None:
                counts["missing"] += 1
                log.info(
                    "observation.derivation_skipped",
                    calculated_station_id=str(calc_id),
                    parameter=parameter,
                    timestamp=ts.isoformat(),
                    reason=_skip_reason(weights, best_by_component, eligible, ts),
                )
            else:
                counts["derived"] += 1

    if to_store:
        obs_store.store_observations(to_store)
    log.info(
        "ingest.derivation_complete",
        derived=counts["derived"],
        missing=counts["missing"],
    )
    return counts


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


def _resolve_ingest_observations_run_name() -> str:
    scheduled = flow_run.scheduled_start_time
    if scheduled is not None:
        return f"ingest-obs-{scheduled:%Y-%m-%dT%H}"
    return "ingest-obs"


@flow(
    name="ingest-observations",
    log_prints=False,
    flow_run_name=_resolve_ingest_observations_run_name,
)
def ingest_observations_flow(
    station_store: object = None,
    obs_store: object = None,
    baseline_store: object = None,
    alert_store: object = None,
    adapter: object = None,
    qc_rules: object = None,
    deployment_config: object = None,
    clock: object = None,
    formula_store: object = None,
    context_window_hours: float = 2.0,
    default_lookback_hours: float = 1.0,
) -> IngestResult:
    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    # --- Production setup ---
    _conn: object = None
    if station_store is None:
        from sapphire_flow.flows._db import setup_production_stores

        database_url = os.environ["DATABASE_URL"]
        _conn, stores = setup_production_stores(database_url)
        station_store = stores["station_store"]  # type: ignore[assignment]
        obs_store = stores["obs_store"]  # type: ignore[assignment]
        baseline_store = stores["baseline_store"]  # type: ignore[assignment]
        alert_store = stores["alert_store"]  # type: ignore[assignment]
        formula_store = stores["formula_store"]  # type: ignore[assignment]

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

    if station_store is None:
        raise ConfigurationError("station_store is required but was not provided")
    if obs_store is None:
        raise ConfigurationError("obs_store is required but was not provided")
    if baseline_store is None:
        raise ConfigurationError("baseline_store is required but was not provided")

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
    datums: dict[tuple[StationId, str], float | None] = {
        (station.id, "water_level"): station.water_level_datum_masl
        for station in eligible
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
                datum=datums.get((station_id, parameter)),
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

    # --- Step 2.5: Calculated-station derivation (Plan 015) ---
    # Sequential post-QC step (NOT a task.map fan-out): the QC loop finishing is the
    # barrier. Calculated stations were excluded from `eligible` by the GAUGED guard, so
    # this reads their components' just-QC'd observations. No-op when there are none.
    derived = {"derived": 0, "missing": 0}
    calculated = [
        s
        for s in all_stations
        if s.gauging_status == GaugingStatus.CALCULATED
        and s.station_status.value == "operational"
    ]
    if calculated and formula_store is not None:
        derived = _derive_calculated_task(
            obs_store,  # type: ignore[arg-type]
            formula_store,  # type: ignore[arg-type]
            station_store,  # type: ignore[arg-type]
            calculated,
            raw_obs,
            now,
        )
    elif calculated and formula_store is None:
        log.warning("ingest.derivation_skipped_no_store", calculated=len(calculated))

    # --- Steps 2.8–2.10: Observation alerts (v0: disabled by default) ---
    if deployment_config is not None and deployment_config.enable_observation_alerts:
        from sapphire_flow.services.observation_alert_checker import (
            check_observation_alerts,
        )

        if alert_store is None:
            raise ConfigurationError("alert_store is required but was not provided")
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
        observations_derived=derived["derived"],
        observations_missing=derived["missing"],
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
        observations_derived=result.observations_derived,
        observations_missing=result.observations_missing,
    )

    return result
