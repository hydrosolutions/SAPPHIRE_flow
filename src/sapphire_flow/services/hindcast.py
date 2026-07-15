from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import polars as pl
import psycopg
import psycopg.errors
import structlog
from sqlalchemy.exc import (
    DisconnectionError,
    InterfaceError,
    InternalError,
    OperationalError,
    PendingRollbackError,
)

from sapphire_flow.exceptions import StoreError
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType, QcStatus
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.ids import ArtifactId, HindcastForecastId, ModelId, StationId
from sapphire_flow.types.model import (
    StationInputData,
    StationModelInputs,
    stack_model_inputs,
)
from sapphire_flow.types.training import HindcastStepResult

if TYPE_CHECKING:
    import random
    from collections.abc import Callable
    from datetime import timedelta

    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )
    from sapphire_flow.protocols.stores import (
        BasinStore,
        HindcastStore,
        ObservationStore,
        StationStore,
    )
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.model import ModelArtifact, ModelInputs
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.station import (
        StationConfig,
        StationGroup,
        StationWeatherSource,
    )

log = structlog.get_logger(__name__)

_CONNECTION_FATAL_KEYWORDS = frozenset(
    {
        "adminshutdown",
        "server closed",
        "connection reset",
        "connection refused",
        "terminating connection",
        "could not connect",
    }
)


def is_connection_fatal(exc: Exception) -> bool:
    """Return True if the exception indicates the DB connection is dead."""
    if isinstance(exc, (DisconnectionError, InterfaceError, PendingRollbackError)):
        return True
    if isinstance(exc, (OperationalError, InternalError)):
        msg = str(exc).lower()
        return any(kw in msg for kw in _CONNECTION_FATAL_KEYWORDS)
    if isinstance(exc, psycopg.Error):
        _fatal_psycopg = (
            psycopg.errors.AdminShutdown,
            psycopg.errors.CrashShutdown,
            psycopg.errors.ConnectionFailure,
            psycopg.errors.ConnectionDoesNotExist,
            psycopg.errors.CannotConnectNow,
        )
        if isinstance(exc, _fatal_psycopg):
            return True
        msg = str(exc).lower()
        return any(kw in msg for kw in _CONNECTION_FATAL_KEYWORDS)
    return False


def _issue_times(
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    time_step: timedelta,
) -> list[UtcDatetime]:
    times = []
    t = period_start
    while t < period_end:
        times.append(t)
        t = ensure_utc(t + time_step)
    return times


def _raw_forcing_to_dataframe(
    raw_records: list,
    station_id: StationId,
    parameters: list[str],
) -> pl.DataFrame | None:
    rows = [
        {"timestamp": r.valid_time, r.parameter: r.value}
        for r in raw_records
        if r.station_id == station_id and r.parameter in parameters
    ]
    if not rows:
        return None
    all_timestamps = sorted({r["timestamp"] for r in rows})
    pivot: dict[object, dict] = {ts: {"timestamp": ts} for ts in all_timestamps}
    for row in rows:
        ts = row["timestamp"]
        for key, val in row.items():
            if key != "timestamp":
                pivot[ts][key] = val
    return pl.DataFrame(list(pivot.values()))


def _observations_to_dataframe(
    observations: list, parameter: str = "discharge"
) -> pl.DataFrame:
    rows = [{"timestamp": o.timestamp, parameter: o.value} for o in observations]
    return pl.DataFrame(rows)


def _assemble_hindcast_inputs(
    station_id: StationId,
    issue_time: UtcDatetime,
    lookback_steps: int,
    time_step: timedelta,
    forecast_horizon_steps: int,
    required_features: list[str],
    all_forcing: list[RawHistoricalForcing],
    all_observations: list[Observation],
    weather_sources: list[StationWeatherSource],
    static_attributes: pl.DataFrame | None,
    parameter: str = "discharge",
) -> StationModelInputs | None:
    lookback_start = ensure_utc(issue_time - lookback_steps * time_step)
    # +1 because the fetch end is exclusive
    horizon_end = ensure_utc(issue_time + (forecast_horizon_steps + 1) * time_step)

    station_ids = {cfg.station_id for cfg in weather_sources}
    raw_forcing = [
        r
        for r in all_forcing
        if r.station_id in station_ids
        and lookback_start <= r.valid_time < horizon_end
        and r.parameter in required_features
    ]

    # NO-FUTURE-LEAKAGE: end=issue_time (unchanged from original).
    observations = [
        o
        for o in all_observations
        if o.station_id == station_id
        and o.parameter == parameter
        and o.qc_status == QcStatus.QC_PASSED
        and lookback_start <= o.timestamp < issue_time
    ]

    if not observations:
        log.warning(
            "hindcast.skip.no_observations",
            station_id=str(station_id),
            issue_time=str(issue_time),
        )
        return None

    if required_features:
        forcing_df = _raw_forcing_to_dataframe(
            raw_forcing, station_id, required_features
        )
        if forcing_df is None:
            log.warning(
                "hindcast.skip.no_forcing",
                station_id=str(station_id),
                issue_time=str(issue_time),
            )
            return None
    else:
        forcing_df = pl.DataFrame(schema={"timestamp": pl.Datetime("us", "UTC")})

    obs_df = _observations_to_dataframe(observations, parameter)

    # Split forcing into past (≤ issue_time) and future (> issue_time).
    # Reanalysis serves as teacher forcing in hindcast (v0-scope §A13).
    past_dynamic = forcing_df.filter(pl.col("timestamp") <= issue_time)
    future_dynamic = forcing_df.filter(pl.col("timestamp") > issue_time)

    station_data = StationInputData(
        past_targets=obs_df,
        past_dynamic=past_dynamic,
        future_dynamic=future_dynamic,
        static=static_attributes,
    )

    return StationModelInputs(
        station_id=station_id,
        data=station_data,
        issue_time=issue_time,
        forecast_horizon_steps=forecast_horizon_steps,
        time_step=time_step,
    )


def _load_static_attributes(
    basin_store: BasinStore,
    station_config: StationConfig,
) -> pl.DataFrame | None:
    if station_config.basin_id is None:
        return None
    basin = basin_store.fetch_basin(station_config.basin_id)
    if basin is not None and basin.attributes:
        return pl.DataFrame([basin.attributes])
    return None


# ---------------------------------------------------------------------------
# Legacy stacking helper — converts StationModelInputs back to ModelInputs
# format for stack_model_inputs(). Removed once group hindcast is refactored.
# ---------------------------------------------------------------------------


def _to_legacy_model_inputs(inputs: StationModelInputs) -> ModelInputs:
    """Temporary shim: pack StationModelInputs into legacy ModelInputs for stacking."""
    from sapphire_flow.types.model import ModelInputs

    # Reconstruct flat forcing DataFrame from past + future dynamic.
    past = inputs.data.past_dynamic
    future = inputs.data.future_dynamic
    forcing = past if future.is_empty() else pl.concat([past, future]).sort("timestamp")

    return ModelInputs(
        station_id=inputs.station_id,
        forcing=forcing,
        observations=inputs.data.past_targets,
        static_attributes=inputs.data.static,
        issue_time=inputs.issue_time,
        forecast_horizon_steps=inputs.forecast_horizon_steps,
        time_step=inputs.time_step,
        warm_up_steps=None,
    )


def run_station_hindcast(
    model: StationForecastModel,
    artifact: ModelArtifact,
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    time_step: timedelta,
    forcing_source: WeatherReanalysisSource,
    obs_store: ObservationStore,
    hindcast_store: HindcastStore,
    station_store: StationStore,
    basin_store: BasinStore,
    clock: Callable[[], UtcDatetime],
    rng: random.Random,
    hindcast_run_id: UUID,
    forecast_horizon_steps: int | None = None,
    lookback_steps: int = 720,
) -> list[HindcastStepResult]:
    station_config = station_store.fetch_station(station_id)
    if station_config is None:
        raise ValueError(f"Station {station_id} not found")

    if forecast_horizon_steps is None:
        forecast_horizon_steps = model.data_requirements.forecast_horizon_steps
    log.debug(
        "hindcast.horizon_resolved",
        forecast_horizon_steps=forecast_horizon_steps,
        model_id=str(model_id),
        station_id=str(station_id),
    )

    weather_sources = station_store.fetch_reanalysis_bindings(station_id)
    static_df = _load_static_attributes(basin_store, station_config)
    # Fetch BOTH past- and future-known forcing from reanalysis: future-dynamic
    # forcing (e.g. NWP precip/temp) is teacher-forced in hindcast and must be
    # present for models whose forcing is future-known only (past set empty).
    required_features = sorted(
        model.data_requirements.past_dynamic_features
        | model.data_requirements.future_dynamic_features
    )
    targets = station_config.forecast_targets
    parameter = next(iter(targets), "discharge") if targets else "discharge"

    # Pre-fetch all data for the full period (H.2 + H.3 from architecture).
    full_start = ensure_utc(period_start - lookback_steps * time_step)
    full_end = ensure_utc(period_end + (forecast_horizon_steps + 1) * time_step)

    t0 = time.perf_counter()
    if required_features:
        all_forcing = forcing_source.fetch_reanalysis(
            station_configs=weather_sources,
            start=full_start,
            end=full_end,
            parameters=required_features,
        )
    else:
        all_forcing = []
    all_observations = obs_store.fetch_observations(
        station_id=station_id,
        parameter=parameter,
        start=full_start,
        end=period_end,
        qc_status=QcStatus.QC_PASSED,
    )
    log.info(
        "hindcast.prefetch_completed",
        station_id=str(station_id),
        forcing_records=len(all_forcing),
        observation_records=len(all_observations),
        duration_ms=round((time.perf_counter() - t0) * 1000, 1),
    )

    results: list[HindcastStepResult] = []
    for issue_time in _issue_times(period_start, period_end, time_step):
        try:
            inputs = _assemble_hindcast_inputs(
                station_id=station_id,
                issue_time=issue_time,
                lookback_steps=lookback_steps,
                time_step=time_step,
                forecast_horizon_steps=forecast_horizon_steps,
                required_features=required_features,
                all_forcing=all_forcing,
                all_observations=all_observations,
                weather_sources=weather_sources,
                static_attributes=static_df,
                parameter=parameter,
            )
            if inputs is None:
                results.append(
                    HindcastStepResult(
                        issue_time=issue_time,
                        success=False,
                        error="insufficient data",
                    )
                )
                continue

            ensembles, _ = model.predict(
                artifact=artifact,
                inputs=inputs,
                rng=rng,
                prior_state=None,
            )
            for param_name, ensemble in ensembles.items():
                if ensemble.parameter != param_name:
                    raise ValueError(
                        f"Dict key '{param_name}' != ensemble.parameter "
                        f"'{ensemble.parameter}'"
                    )
                hindcast = HindcastForecast(
                    id=HindcastForecastId(uuid4()),
                    station_id=station_id,
                    model_id=model_id,
                    model_artifact_id=artifact_id,
                    hindcast_step=issue_time,
                    forcing_type=ForcingType.REANALYSIS,
                    representation=EnsembleRepresentation.MEMBERS,
                    hindcast_run_id=hindcast_run_id,
                    ensemble=ensemble,
                    created_at=clock(),
                )
                hindcast_store.store_hindcast(hindcast)
            results.append(HindcastStepResult(issue_time=issue_time, success=True))

        except Exception as exc:
            if is_connection_fatal(exc):
                log.error(
                    "hindcast.connection_failed",
                    station_id=str(station_id),
                    issue_time=str(issue_time),
                    error_type=type(exc).__qualname__,
                    successful_steps=sum(1 for r in results if r.success),
                    remaining_steps=len(
                        _issue_times(
                            ensure_utc(issue_time + time_step), period_end, time_step
                        )
                    ),
                    exc_info=True,
                )
                raise StoreError(f"Connection-fatal: {type(exc).__qualname__}") from exc
            # Guard against DSN leakage
            if isinstance(
                exc, (OperationalError, InterfaceError, InternalError, psycopg.Error)
            ):
                error_fields: dict[str, str] = {"error_type": type(exc).__qualname__}
            else:
                error_fields = {"error": str(exc)}
            log.warning(
                "hindcast.step_failed",
                station_id=str(station_id),
                issue_time=str(issue_time),
                **error_fields,
            )
            results.append(
                HindcastStepResult(
                    issue_time=issue_time,
                    success=False,
                    error=type(exc).__qualname__
                    if isinstance(
                        exc,
                        (
                            OperationalError,
                            InterfaceError,
                            InternalError,
                            psycopg.Error,
                        ),
                    )
                    else str(exc),
                )
            )

    return results


def run_group_hindcast(
    model: GroupForecastModel,
    artifact: ModelArtifact,
    group: StationGroup,
    model_id: ModelId,
    artifact_id: ArtifactId,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    time_step: timedelta,
    forcing_source: WeatherReanalysisSource,
    obs_store: ObservationStore,
    hindcast_store: HindcastStore,
    station_store: StationStore,
    basin_store: BasinStore,
    clock: Callable[[], UtcDatetime],
    rng: random.Random,
    hindcast_run_id: UUID,
    forecast_horizon_steps: int | None = None,
    lookback_steps: int = 720,
) -> dict[StationId, list[HindcastStepResult]]:
    station_configs = {
        sid: station_store.fetch_station(sid) for sid in group.station_ids
    }
    weather_sources_map = {
        sid: station_store.fetch_reanalysis_bindings(sid) for sid in group.station_ids
    }
    static_map: dict[StationId, pl.DataFrame | None] = {
        sid: _load_static_attributes(basin_store, cfg)
        for sid, cfg in station_configs.items()
        if cfg is not None
    }
    parameter_map: dict[StationId, str] = {
        sid: (
            next(iter(cfg.forecast_targets), "discharge")
            if cfg.forecast_targets
            else "discharge"
        )
        for sid, cfg in station_configs.items()
        if cfg is not None
    }
    # Fetch BOTH past- and future-known forcing from reanalysis (see run_station
    # _hindcast): future-dynamic forcing is teacher-forced in hindcast.
    required_features = sorted(
        model.data_requirements.past_dynamic_features
        | model.data_requirements.future_dynamic_features
    )

    if forecast_horizon_steps is None:
        forecast_horizon_steps = model.data_requirements.forecast_horizon_steps
    log.debug(
        "hindcast.horizon_resolved",
        forecast_horizon_steps=forecast_horizon_steps,
        model_id=str(model_id),
        group_id=str(group.id),
    )

    full_start = ensure_utc(period_start - lookback_steps * time_step)
    full_end = ensure_utc(period_end + (forecast_horizon_steps + 1) * time_step)

    fetchable_sids = [
        sid for sid in group.station_ids if station_configs[sid] is not None
    ]

    # Single batch call for forcing across all stations.
    all_weather_sources = [
        ws for sid in fetchable_sids for ws in weather_sources_map[sid]
    ]
    if required_features:
        all_forcing_flat = forcing_source.fetch_reanalysis(
            station_configs=all_weather_sources,
            start=full_start,
            end=full_end,
            parameters=required_features,
        )
    else:
        all_forcing_flat = []
    # Index by station_id for per-step lookup.
    all_forcing_map: dict[StationId, list[RawHistoricalForcing]] = {
        sid: [] for sid in fetchable_sids
    }
    for r in all_forcing_flat:
        if r.station_id in all_forcing_map:
            all_forcing_map[r.station_id].append(r)

    # Per-station calls for observations (different parameters per station).
    all_obs_map: dict[StationId, list[Observation]] = {}
    for sid in fetchable_sids:
        all_obs_map[sid] = obs_store.fetch_observations(
            station_id=sid,
            parameter=parameter_map.get(sid, "discharge"),
            start=full_start,
            end=period_end,
            qc_status=QcStatus.QC_PASSED,
        )

    per_station: dict[StationId, list[HindcastStepResult]] = {
        sid: [] for sid in group.station_ids
    }

    for issue_time in _issue_times(period_start, period_end, time_step):
        inputs_batch: dict[StationId, StationModelInputs] = {}
        skipped: dict[StationId, str] = {}

        for sid in group.station_ids:
            cfg = station_configs[sid]
            if cfg is None:
                skipped[sid] = f"station {sid} not found"
                continue
            try:
                inputs = _assemble_hindcast_inputs(
                    station_id=sid,
                    issue_time=issue_time,
                    lookback_steps=lookback_steps,
                    time_step=time_step,
                    forecast_horizon_steps=forecast_horizon_steps,
                    required_features=required_features,
                    all_forcing=all_forcing_map.get(sid, []),
                    all_observations=all_obs_map.get(sid, []),
                    weather_sources=weather_sources_map[sid],
                    static_attributes=static_map.get(sid),
                    parameter=parameter_map.get(sid, "discharge"),
                )
                if inputs is None:
                    skipped[sid] = "insufficient data"
                else:
                    inputs_batch[sid] = inputs
            except Exception as exc:
                if is_connection_fatal(exc):
                    log.error(
                        "hindcast.connection_failed",
                        station_id=str(sid),
                        issue_time=str(issue_time),
                        error_type=type(exc).__qualname__,
                        exc_info=True,
                    )
                    raise StoreError(
                        f"Connection-fatal: {type(exc).__qualname__}"
                    ) from exc
                if isinstance(
                    exc,
                    (OperationalError, InterfaceError, InternalError, psycopg.Error),
                ):
                    error_fields_: dict[str, str] = {
                        "error_type": type(exc).__qualname__
                    }
                    skipped[sid] = type(exc).__qualname__
                else:
                    error_fields_ = {"error": str(exc)}
                    skipped[sid] = str(exc)
                log.warning(
                    "hindcast.step_failed",
                    station_id=str(sid),
                    issue_time=str(issue_time),
                    **error_fields_,
                )

        for sid, reason in skipped.items():
            per_station[sid].append(
                HindcastStepResult(issue_time=issue_time, success=False, error=reason)
            )

        if not inputs_batch:
            continue

        # Convert StationModelInputs → legacy ModelInputs for GroupModelInputs stacking.
        # TODO: refactor stack_model_inputs to accept StationModelInputs directly.
        legacy_batch = {
            sid: _to_legacy_model_inputs(inp) for sid, inp in inputs_batch.items()
        }

        t0 = time.perf_counter()
        group_inputs = stack_model_inputs(
            group_id=group.id,
            inputs=legacy_batch,
            issue_time=issue_time,
        )
        t1 = time.perf_counter()
        log.info(
            "group_inputs.stacking_completed",
            group_id=str(group.id),
            station_count=len(inputs_batch),
            issue_time=str(issue_time),
            duration_ms=round((t1 - t0) * 1000, 1),
        )

        try:
            batch_results = model.predict_batch(
                artifact=artifact,
                inputs=group_inputs,
                rng=rng,
            )
        except Exception as exc:
            if is_connection_fatal(exc):
                log.error(
                    "hindcast.connection_failed",
                    issue_time=str(issue_time),
                    error_type=type(exc).__qualname__,
                    exc_info=True,
                )
                raise StoreError(f"Connection-fatal: {type(exc).__qualname__}") from exc
            if isinstance(
                exc, (OperationalError, InterfaceError, InternalError, psycopg.Error)
            ):
                log.warning(
                    "hindcast.batch_predict_failed",
                    issue_time=str(issue_time),
                    error_type=type(exc).__qualname__,
                )
                err = type(exc).__qualname__
            else:
                log.warning(
                    "hindcast.batch_predict_failed",
                    issue_time=str(issue_time),
                    error=str(exc),
                )
                err = str(exc)
            for sid in inputs_batch:
                per_station[sid].append(
                    HindcastStepResult(issue_time=issue_time, success=False, error=err)
                )
            continue

        for sid, (ensembles, _) in batch_results.items():
            param_name: str | None = None
            try:
                for param_name, ensemble in ensembles.items():
                    if ensemble.parameter != param_name:
                        raise ValueError(
                            f"Dict key '{param_name}' != ensemble.parameter "
                            f"'{ensemble.parameter}'"
                        )
                    hindcast = HindcastForecast(
                        id=HindcastForecastId(uuid4()),
                        station_id=sid,
                        model_id=model_id,
                        model_artifact_id=artifact_id,
                        hindcast_step=issue_time,
                        forcing_type=ForcingType.REANALYSIS,
                        representation=EnsembleRepresentation.MEMBERS,
                        hindcast_run_id=hindcast_run_id,
                        ensemble=ensemble,
                        created_at=clock(),
                    )
                    hindcast_store.store_hindcast(hindcast)
                per_station[sid].append(
                    HindcastStepResult(issue_time=issue_time, success=True)
                )
            except Exception as exc:
                if is_connection_fatal(exc):
                    log.error(
                        "hindcast.connection_failed",
                        station_id=str(sid),
                        issue_time=str(issue_time),
                        parameter=param_name or "<unknown>",
                        error_type=type(exc).__qualname__,
                        exc_info=True,
                    )
                    raise StoreError(
                        f"Connection-fatal: {type(exc).__qualname__}"
                    ) from exc
                if isinstance(
                    exc,
                    (OperationalError, InterfaceError, InternalError, psycopg.Error),
                ):
                    log.error(
                        "hindcast.store_failed",
                        station_id=str(sid),
                        issue_time=str(issue_time),
                        parameter=param_name or "<unknown>",
                        error_type=type(exc).__qualname__,
                        exc_info=True,
                    )
                    per_station[sid].append(
                        HindcastStepResult(
                            issue_time=issue_time,
                            success=False,
                            error=type(exc).__qualname__,
                        )
                    )
                else:
                    log.error(
                        "hindcast.store_failed",
                        station_id=str(sid),
                        issue_time=str(issue_time),
                        parameter=param_name or "<unknown>",
                        exc_info=True,
                    )
                    per_station[sid].append(
                        HindcastStepResult(
                            issue_time=issue_time,
                            success=False,
                            error=str(exc),
                        )
                    )

    return per_station
