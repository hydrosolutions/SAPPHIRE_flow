from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
import structlog.contextvars
from prefect import flow, task

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    NwpCycleSource,
    StationKind,
    StationStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.adapters import WeatherForecastSource
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import ForecastQcRuleSet
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastCycleResult:
    cycle_time: UtcDatetime
    stations_attempted: int
    stations_succeeded: int
    stations_failed: int
    forecasts_stored: int
    alerts_checked: bool
    duration_ms: float
    errors: tuple[str, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_production_stores(database_url: str) -> tuple[object, dict[str, object]]:
    import sqlalchemy as sa

    from sapphire_flow.flows._db import make_pg_stores, run_migrations

    engine = sa.create_engine(database_url)
    run_migrations(engine)
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    stores = make_pg_stores(conn)
    return conn, stores


def _load_forecast_qc_rules() -> ForecastQcRuleSet:
    from sapphire_flow.config.forecast_qc_rules import (
        _default_swiss_forecast_qc_rules,
        load_forecast_qc_rules,
    )

    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is not None:
        return load_forecast_qc_rules(config_path)
    return _default_swiss_forecast_qc_rules()


def _resolve_cycle_time(
    cycle_time_str: str | None,
    clock: Callable[[], UtcDatetime],
) -> UtcDatetime:
    if cycle_time_str is not None:
        return ensure_utc(datetime.fromisoformat(cycle_time_str).replace(tzinfo=UTC))
    return clock()


# ---------------------------------------------------------------------------
# Phase A task — fetch NWP and store weather records
# ---------------------------------------------------------------------------


@task(name="fetch-nwp-forcing", persist_result=False, log_prints=False)
def _fetch_nwp_task(
    adapter: WeatherForecastSource,
    station_configs: list[StationConfig],
    cycle_time: UtcDatetime,
    weather_forecast_store: object,
    clock: Callable[[], UtcDatetime],
) -> UtcDatetime | None:
    from sapphire_flow.preprocessing.converters import (
        basin_avg_to_records,
        point_forecast_to_records,
    )
    from sapphire_flow.types.weather import (
        BasinAverageForecast,
        GriddedForecast,
        PointForecast,
    )

    t0 = time.perf_counter()
    try:
        result = adapter.fetch_forecasts(
            [s for sc in station_configs for s in _station_weather_sources(sc)],
            cycle_time,
        )
    except Exception as exc:
        log.error("nwp.fetch_failed", error=str(exc))
        return None

    if isinstance(result, GriddedForecast):
        raise NotImplementedError("v0b grid path not yet wired")

    if not isinstance(result, dict):
        log.error("nwp.unexpected_return_type", type=type(result).__name__)
        return None

    all_records = []
    for station_id, forecast in result.items():
        if isinstance(forecast, PointForecast):
            all_records.extend(
                point_forecast_to_records(station_id, forecast, clock, uuid4)
            )
        elif isinstance(forecast, BasinAverageForecast):
            all_records.extend(basin_avg_to_records(station_id, forecast, clock, uuid4))
        else:
            log.warning(
                "nwp.unknown_forecast_type",
                station_id=str(station_id),
                type=type(forecast).__name__,
            )

    if all_records:
        weather_forecast_store.store_weather_forecasts(all_records)  # type: ignore[union-attr]

    duration_ms = round((time.perf_counter() - t0) * 1000, 1)
    log.info(
        "nwp.fetch_completed",
        records_stored=len(all_records),
        stations=len(result),
        duration_ms=duration_ms,
    )
    return cycle_time


def _station_weather_sources(station: StationConfig) -> list:
    return []


# ---------------------------------------------------------------------------
# Step 1.6 task — fetch latest observation timestamps
# ---------------------------------------------------------------------------


@task(name="fetch-observation-timestamps", log_prints=False)
def _fetch_obs_timestamps_task(
    obs_store: object,
    stations: list[StationConfig],
) -> dict[StationId, UtcDatetime | None]:
    result: dict[StationId, UtcDatetime | None] = {}
    for station in stations:
        for param in station.measured_parameters or frozenset():
            ts = obs_store.fetch_latest_timestamp(station.id, param)  # type: ignore[union-attr]
            if ts is not None and (
                station.id not in result or result[station.id] is None
            ):
                result[station.id] = ts
                break
        if station.id not in result:
            result[station.id] = None
    return result


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


@flow(name="forecast-cycle", log_prints=False)
def run_forecast_cycle_flow(
    station_store: object = None,
    obs_store: object = None,
    weather_forecast_store: object = None,
    forecast_store: object = None,
    model_state_store: object = None,
    artifact_store: object = None,
    alert_store: object = None,
    baseline_store: object = None,
    basin_store: object = None,
    forcing_store: object = None,
    adapter: object = None,
    models: object | None = None,
    config: object | None = None,
    qc_rules: object | None = None,
    clock: object | None = None,
    rng: object | None = None,
    cycle_time: str | None = None,
) -> ForecastCycleResult:
    flow_t0 = time.perf_counter()

    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731
    if rng is None:
        rng = random.Random()

    # --- Production setup ---
    _conn: object = None
    if station_store is None:
        database_url = os.environ["DATABASE_URL"]
        _conn, stores = _setup_production_stores(database_url)
        station_store = stores["station_store"]
        obs_store = stores["obs_store"]
        weather_forecast_store = stores["weather_forecast_store"]
        forecast_store = stores["forecast_store"]
        model_state_store = stores["model_state_store"]
        artifact_store = stores["artifact_store"]
        alert_store = stores["alert_store"]
        baseline_store = stores["baseline_store"]
        basin_store = stores["basin_store"]
        forcing_store = stores["forcing_store"]

    if adapter is None:
        raise ValueError("adapter must be provided (no default NWP adapter in v0)")

    if config is None:
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.deployment import load_config

            config = load_config(config_path)
        else:
            from sapphire_flow.config.deployment import DeploymentConfig

            config = DeploymentConfig(max_retention_days=600)

    if qc_rules is None:
        qc_rules = _load_forecast_qc_rules()

    if models is None:
        from sapphire_flow.services.model_registry import discover_models

        models = discover_models()

    assert station_store is not None
    assert obs_store is not None
    assert weather_forecast_store is not None
    assert forecast_store is not None
    assert model_state_store is not None
    assert artifact_store is not None
    assert baseline_store is not None
    assert basin_store is not None
    assert forcing_store is not None

    resolved_cycle_time: UtcDatetime = _resolve_cycle_time(cycle_time, clock)

    # --- Batch pre-fetch: operational stations ---
    all_stations = station_store.fetch_all_stations(kind=StationKind.RIVER)  # type: ignore[union-attr]
    operational = [
        s for s in all_stations if s.station_status == StationStatus.OPERATIONAL
    ]

    if not operational:
        log.info("forecast_cycle.no_operational_stations")
        return ForecastCycleResult(
            cycle_time=resolved_cycle_time,
            stations_attempted=0,
            stations_succeeded=0,
            stations_failed=0,
            forecasts_stored=0,
            alerts_checked=False,
            duration_ms=round((time.perf_counter() - flow_t0) * 1000, 1),
            errors=(),
        )

    log.info("forecast_cycle.starting", stations=len(operational))

    # Batch pre-fetch per-station data
    model_assignments: dict[StationId, list] = {
        s.id: station_store.fetch_model_assignments(s.id)  # type: ignore[union-attr]
        for s in operational
    }
    all_thresholds: dict[StationId, list] = {
        s.id: station_store.fetch_thresholds(s.id)  # type: ignore[union-attr]
        for s in operational
    }
    all_baselines: dict[StationId, list] = {}
    for s in operational:
        params = list(s.measured_parameters or frozenset())
        combined: list = []
        for param in params:
            combined.extend(baseline_store.fetch_baselines(s.id, param))  # type: ignore[union-attr]
        all_baselines[s.id] = combined

    # Build priority index for alert checker
    all_priorities: dict[StationId, dict[ModelId, int]] = {
        s.id: {a.model_id: a.priority for a in model_assignments[s.id]}
        for s in operational
    }

    # Instantiate reanalysis source for past_dynamic
    from sapphire_flow.adapters.store_backed_reanalysis import (
        StoreBackedReanalysisSource,
    )

    forcing_source = StoreBackedReanalysisSource(forcing_store)

    # Instantiate forecast QC checker
    from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker

    qc_checker = ForecastOutputQualityChecker()

    # --- Phase A: fetch NWP forcing (submit as task) ---
    nwp_future = _fetch_nwp_task.submit(
        adapter=adapter,
        station_configs=operational,
        cycle_time=resolved_cycle_time,
        weather_forecast_store=weather_forecast_store,
        clock=clock,
    )

    # --- Step 1.6: observation timestamps (parallel with Phase A) ---
    obs_ts_future = _fetch_obs_timestamps_task.submit(
        obs_store=obs_store,
        stations=operational,
    )

    # Collect Phase A result
    nwp_cycle = nwp_future.result()
    if nwp_cycle is None:
        log.error("forecast_cycle.nwp_fetch_failed_aborting")
        return ForecastCycleResult(
            cycle_time=resolved_cycle_time,
            stations_attempted=0,
            stations_succeeded=0,
            stations_failed=0,
            forecasts_stored=0,
            alerts_checked=False,
            duration_ms=round((time.perf_counter() - flow_t0) * 1000, 1),
            errors=("NWP fetch failed",),
        )

    # Collect Step 1.6 result (we don't block on this — just use it for logging)
    _obs_timestamps: dict[StationId, UtcDatetime | None] = obs_ts_future.result()

    # Determine nwp_cycle_source (v0: always PRIMARY)
    nwp_cycle_source = NwpCycleSource.PRIMARY

    # --- Phase B: per-station forecast loop ---
    from datetime import timedelta

    from sapphire_flow.services.operational_inputs import (
        assemble_station_operational_inputs,
    )
    from sapphire_flow.services.run_station_forecast import run_station_forecast

    stations_succeeded = 0
    stations_failed = 0
    forecasts_stored = 0
    errors: list[str] = []

    # Accumulate for Phase C
    all_ensembles: dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]] = {}

    for station in operational:
        sid = station.id
        structlog.contextvars.bind_contextvars(station_id=str(sid))
        station_t0 = time.perf_counter()

        assignments = model_assignments[sid]
        if not assignments:
            log.debug("forecast_cycle.no_assignments")
            structlog.contextvars.unbind_contextvars("station_id")
            continue

        # Use time_step from first active assignment (priority-sorted)
        sorted_assignments = sorted(assignments, key=lambda a: a.priority)
        time_step: timedelta = sorted_assignments[0].time_step
        forecast_horizon_steps: int = getattr(config, "forecast_horizon_steps", 120)

        # Determine nwp_source for this station
        weather_sources = station_store.fetch_weather_sources(sid)  # type: ignore[union-attr]
        nwp_source: str = (
            weather_sources[0].nwp_source if weather_sources else "icon-ch2-eps"
        )

        try:
            inputs_result = assemble_station_operational_inputs(
                station_id=sid,
                model=models.get(sorted_assignments[0].model_id),  # type: ignore[arg-type]
                model_id=sorted_assignments[0].model_id,
                issue_time=resolved_cycle_time,
                cycle_time=resolved_cycle_time,
                nwp_source=nwp_source,
                forcing_source=forcing_source,
                weather_forecast_store=weather_forecast_store,  # type: ignore[arg-type]
                obs_store=obs_store,  # type: ignore[arg-type]
                station_store=station_store,  # type: ignore[arg-type]
                basin_store=basin_store,  # type: ignore[arg-type]
                model_state_store=model_state_store,  # type: ignore[arg-type]
                clock=clock,
                forecast_horizon_steps=forecast_horizon_steps,
                time_step=time_step,
            )
        except Exception as exc:
            log.warning("forecast_cycle.input_assembly_failed", error=str(exc))
            errors.append(f"Input assembly failed for {sid}: {exc}")
            stations_failed += 1
            structlog.contextvars.unbind_contextvars("station_id")
            continue

        if inputs_result is None:
            log.info("forecast_cycle.station_skipped_no_nwp")
            structlog.contextvars.unbind_contextvars("station_id")
            continue

        inputs, input_metadata = inputs_result

        try:
            fc_result = run_station_forecast(
                station_id=sid,
                inputs=inputs,
                input_metadata=input_metadata,
                assignments=sorted_assignments,
                models=models,
                artifact_store=artifact_store,  # type: ignore[arg-type]
                qc_checker=qc_checker,
                qc_rules=qc_rules,
                qc_overrides=[],
                baselines=all_baselines[sid],
                nwp_cycle_reference_time=resolved_cycle_time,
                nwp_cycle_source=nwp_cycle_source,
                config=config,
                clock=clock,
                id_gen=uuid4,
                rng=rng,
            )
        except Exception as exc:
            log.warning("forecast_cycle.station_forecast_failed", error=str(exc))
            errors.append(f"Forecast failed for {sid}: {exc}")
            stations_failed += 1
            structlog.contextvars.unbind_contextvars("station_id")
            continue

        if fc_result is None:
            log.warning("forecast_cycle.all_models_failed")
            stations_failed += 1
            structlog.contextvars.unbind_contextvars("station_id")
            continue

        # Store forecasts
        for fc in fc_result.forecasts:
            try:
                forecast_store.store_forecast(fc)  # type: ignore[union-attr]
                forecasts_stored += 1
            except Exception as exc:
                log.warning("forecast_cycle.store_forecast_failed", error=str(exc))
                errors.append(f"Store failed for {sid}: {exc}")

        # Persist warm-up state
        if fc_result.new_state is not None:
            try:
                model_state_store.store_state(  # type: ignore[union-attr]
                    sid,
                    fc_result.model_id,
                    resolved_cycle_time,
                    fc_result.new_state,
                )
            except Exception as exc:
                log.warning("forecast_cycle.store_state_failed", error=str(exc))

        # Accumulate ensembles for Phase C
        all_ensembles[sid] = {fc_result.model_id: dict(fc_result.ensembles)}

        stations_succeeded += 1
        duration_ms = round((time.perf_counter() - station_t0) * 1000, 1)
        log.info("forecast.station_completed", duration_ms=duration_ms)
        structlog.contextvars.unbind_contextvars("station_id")

    # --- Phase C: alert checking ---
    alerts_checked = False
    if config.enable_forecast_alerts and all_ensembles:
        from sapphire_flow.services.alert_checker import check_station_alerts

        alert_t0 = time.perf_counter()
        try:
            check_station_alerts(
                all_ensembles=all_ensembles,
                all_thresholds=all_thresholds,
                danger_levels=config.get_danger_level_definitions(),
                all_priorities=all_priorities,
                config=config,
                alert_store=alert_store,  # type: ignore[arg-type]
                clock=clock,
            )
            alerts_checked = True
        except Exception as exc:
            log.error("forecast_cycle.alert_check_failed", error=str(exc))
            errors.append(f"Alert check failed: {exc}")
        alert_duration_ms = round((time.perf_counter() - alert_t0) * 1000, 1)
        log.info("alerts.check_completed", duration_ms=alert_duration_ms)

    total_ms = round((time.perf_counter() - flow_t0) * 1000, 1)

    result = ForecastCycleResult(
        cycle_time=resolved_cycle_time,
        stations_attempted=len(operational),
        stations_succeeded=stations_succeeded,
        stations_failed=stations_failed,
        forecasts_stored=forecasts_stored,
        alerts_checked=alerts_checked,
        duration_ms=total_ms,
        errors=tuple(errors),
    )

    log.info(
        "forecast_cycle.complete",
        cycle_time=str(resolved_cycle_time),
        stations_attempted=result.stations_attempted,
        stations_succeeded=result.stations_succeeded,
        stations_failed=result.stations_failed,
        forecasts_stored=result.forecasts_stored,
        alerts_checked=result.alerts_checked,
        duration_ms=result.duration_ms,
    )

    return result
