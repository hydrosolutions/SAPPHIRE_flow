from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import polars as pl
import structlog

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType, QcStatus
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.ids import ArtifactId, HindcastForecastId, ModelId, StationId
from sapphire_flow.types.model import ModelInputs
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
    from sapphire_flow.types.model import ModelArtifact
    from sapphire_flow.types.station import StationGroup, StationWeatherSource

log = structlog.get_logger()


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


def _observations_to_dataframe(observations: list) -> pl.DataFrame:
    rows = [{"timestamp": o.timestamp, "value": o.value} for o in observations]
    return pl.DataFrame(rows)


def _assemble_hindcast_inputs(
    station_id: StationId,
    issue_time: UtcDatetime,
    lookback_steps: int,
    time_step: timedelta,
    forecast_horizon_steps: int,
    required_features: list[str],
    forcing_source: WeatherReanalysisSource,
    obs_store: ObservationStore,
    weather_sources: list[StationWeatherSource],
    static_attributes: pl.DataFrame | None,
    parameter: str = "discharge",
) -> ModelInputs | None:
    lookback_start = ensure_utc(issue_time - lookback_steps * time_step)

    # NO-FUTURE-LEAKAGE: end=issue_time, not issue_time + horizon
    raw_forcing = forcing_source.fetch_reanalysis(
        station_configs=weather_sources,
        start=lookback_start,
        end=issue_time,
        parameters=required_features,
    )

    # NO-FUTURE-LEAKAGE: end=issue_time
    observations = obs_store.fetch_observations(
        station_id=station_id,
        parameter=parameter,
        start=lookback_start,
        end=issue_time,
        qc_status=QcStatus.QC_PASSED,
    )

    if not observations:
        log.warning(
            "hindcast.skip.no_observations",
            station_id=str(station_id),
            issue_time=str(issue_time),
        )
        return None

    forcing_df = _raw_forcing_to_dataframe(raw_forcing, station_id, required_features)
    if forcing_df is None:
        log.warning(
            "hindcast.skip.no_forcing",
            station_id=str(station_id),
            issue_time=str(issue_time),
        )
        return None

    obs_df = _observations_to_dataframe(observations)

    return ModelInputs(
        station_id=station_id,
        forcing=forcing_df,
        observations=obs_df,
        static_attributes=static_attributes,
        issue_time=issue_time,
        forecast_horizon_steps=forecast_horizon_steps,
        time_step=time_step,
        warm_up_steps=None,
    )


def _load_static_attributes(
    basin_store: BasinStore,
    station_config: object,
) -> pl.DataFrame | None:
    if not hasattr(station_config, "basin_id") or station_config.basin_id is None:
        return None
    basin = basin_store.fetch_basin(station_config.basin_id)
    if basin is not None and basin.attributes:
        return pl.DataFrame([basin.attributes])
    return None


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
    forecast_horizon_steps: int = 120,
    lookback_steps: int = 720,
) -> list[HindcastStepResult]:
    station_config = station_store.fetch_station(station_id)
    if station_config is None:
        raise ValueError(f"Station {station_id} not found")

    weather_sources = station_store.fetch_weather_sources(station_id)
    static_df = _load_static_attributes(basin_store, station_config)
    required_features = list(model.data_requirements.past_dynamic_features)
    targets = station_config.forecast_targets
    parameter = next(iter(targets), "discharge") if targets else "discharge"

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
                forcing_source=forcing_source,
                obs_store=obs_store,
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
            log.warning(
                "hindcast.step_failed",
                station_id=str(station_id),
                issue_time=str(issue_time),
                error=str(exc),
            )
            results.append(
                HindcastStepResult(
                    issue_time=issue_time,
                    success=False,
                    error=str(exc),
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
    forecast_horizon_steps: int = 120,
    lookback_steps: int = 720,
) -> dict[StationId, list[HindcastStepResult]]:
    station_configs = {
        sid: station_store.fetch_station(sid) for sid in group.station_ids
    }
    weather_sources_map = {
        sid: station_store.fetch_weather_sources(sid) for sid in group.station_ids
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
    required_features = list(model.data_requirements.past_dynamic_features)

    per_station: dict[StationId, list[HindcastStepResult]] = {
        sid: [] for sid in group.station_ids
    }

    for issue_time in _issue_times(period_start, period_end, time_step):
        inputs_batch: dict[StationId, ModelInputs] = {}
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
                    forcing_source=forcing_source,
                    obs_store=obs_store,
                    weather_sources=weather_sources_map[sid],
                    static_attributes=static_map.get(sid),
                    parameter=parameter_map.get(sid, "discharge"),
                )
                if inputs is None:
                    skipped[sid] = "insufficient data"
                else:
                    inputs_batch[sid] = inputs
            except Exception as exc:
                log.warning(
                    "hindcast.step_failed",
                    station_id=str(sid),
                    issue_time=str(issue_time),
                    error=str(exc),
                )
                skipped[sid] = str(exc)

        for sid, reason in skipped.items():
            per_station[sid].append(
                HindcastStepResult(issue_time=issue_time, success=False, error=reason)
            )

        if not inputs_batch:
            continue

        try:
            batch_results = model.predict_batch(
                artifact=artifact,
                inputs=inputs_batch,
                rng=rng,
            )
        except Exception as exc:
            err = str(exc)
            log.warning(
                "hindcast.batch_predict_failed",
                issue_time=str(issue_time),
                error=err,
            )
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
                log.error(
                    "hindcast.store_failed",
                    station_id=str(sid),
                    issue_time=str(issue_time),
                    parameter=param_name or "<unknown>",
                    exc_info=exc,
                )
                per_station[sid].append(
                    HindcastStepResult(
                        issue_time=issue_time,
                        success=False,
                        error=str(exc),
                    )
                )

    return per_station
