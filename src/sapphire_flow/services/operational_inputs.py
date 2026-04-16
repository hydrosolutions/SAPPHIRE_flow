from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.services.training_data import resample_to_time_step
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import QcStatus, WarmUpSource
from sapphire_flow.types.model import StationInputData, StationModelInputs

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )
    from sapphire_flow.protocols.stores import (
        BasinStore,
        ModelStateStore,
        ObservationStore,
        StationStore,
        WeatherForecastStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationId

log = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalInputMetadata:
    warm_up_source: WarmUpSource
    warm_up_state_age_hours: float | None
    observation_staleness_hours: float | None
    prior_state: bytes | None
    nwp_age_hours: float


def _pivot_nwp_records(
    records: list,
    future_dynamic_features: frozenset[str],
) -> pl.DataFrame:
    if not records:
        return pl.DataFrame()

    feature_cols = list(future_dynamic_features)
    all_times = sorted({r.valid_time for r in records})
    members = sorted({r.member_id for r in records if r.member_id is not None})

    if members:
        # Ensemble: columns are param_member (e.g. precipitation_0, precipitation_1)
        pivot: dict[object, dict] = {ts: {"timestamp": ts} for ts in all_times}
        for r in records:
            if r.parameter not in feature_cols:
                continue
            ts = r.valid_time
            col = (
                f"{r.parameter}_{r.member_id}"
                if r.member_id is not None
                else r.parameter
            )
            pivot[ts][col] = r.value
        return pl.DataFrame(list(pivot.values()))
    else:
        # Deterministic: columns are param names
        pivot2: dict[object, dict] = {ts: {"timestamp": ts} for ts in all_times}
        for r in records:
            if r.parameter not in feature_cols:
                continue
            pivot2[r.valid_time][r.parameter] = r.value
        return pl.DataFrame(list(pivot2.values()))


def _observations_to_wide_dataframe(
    observations: list, parameters: list[str]
) -> pl.DataFrame:
    if not observations:
        return pl.DataFrame()
    all_timestamps = sorted({o.timestamp for o in observations})
    pivot: dict[object, dict] = {ts: {"timestamp": ts} for ts in all_timestamps}
    for o in observations:
        if o.parameter in parameters:
            pivot[o.timestamp][o.parameter] = o.value
    return pl.DataFrame(list(pivot.values()))


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


def assemble_station_operational_inputs(
    station_id: StationId,
    model: StationForecastModel | GroupForecastModel,
    model_id: ModelId,
    issue_time: UtcDatetime,
    cycle_time: UtcDatetime,
    nwp_source: str,
    forcing_source: WeatherReanalysisSource,
    weather_forecast_store: WeatherForecastStore,
    obs_store: ObservationStore,
    station_store: StationStore,
    basin_store: BasinStore,
    model_state_store: ModelStateStore,
    clock: Callable[[], UtcDatetime],
    forecast_horizon_steps: int,
    time_step: timedelta,
) -> tuple[StationModelInputs, OperationalInputMetadata] | None:
    now = clock()
    reqs = model.data_requirements
    lookback_start = ensure_utc(issue_time - reqs.lookback_steps * time_step)

    # --- past_targets ---
    target_parameters = list(reqs.target_parameters)
    all_observations: list = []
    for parameter in target_parameters:
        obs = obs_store.fetch_observations(
            station_id=station_id,
            parameter=parameter,
            start=lookback_start,
            end=issue_time,
            qc_status=QcStatus.QC_PASSED,
        )
        all_observations.extend(obs)

    past_targets = _observations_to_wide_dataframe(all_observations, target_parameters)
    past_targets = resample_to_time_step(
        past_targets, time_step, aggregation_methods=None
    )

    latest_obs_ts = max((o.timestamp for o in all_observations), default=None)
    observation_staleness_hours: float | None = None
    if latest_obs_ts is not None:
        observation_staleness_hours = (now - latest_obs_ts).total_seconds() / 3600.0
    else:
        log.warning(
            "operational_inputs.no_observations",
            station_id=str(station_id),
            issue_time=str(issue_time),
        )

    # --- past_dynamic ---
    past_dynamic_features = list(reqs.past_dynamic_features)
    if past_dynamic_features:
        weather_sources = station_store.fetch_weather_sources(station_id)
        raw_forcing = forcing_source.fetch_reanalysis(
            station_configs=weather_sources,
            start=lookback_start,
            end=issue_time,
            parameters=past_dynamic_features,
        )
        past_dynamic = _raw_forcing_to_dataframe(
            raw_forcing, station_id, past_dynamic_features
        )
        if past_dynamic is None:
            log.warning(
                "operational_inputs.no_past_dynamic",
                station_id=str(station_id),
                issue_time=str(issue_time),
            )
            past_dynamic = pl.DataFrame()
    else:
        past_dynamic = pl.DataFrame()

    # --- future_dynamic (NWP) ---
    nwp_records = weather_forecast_store.fetch_weather_forecasts(
        station_id=station_id,
        nwp_source=nwp_source,
        cycle_time=cycle_time,
        parameters=list(reqs.future_dynamic_features)
        if reqs.future_dynamic_features
        else None,
    )
    if not nwp_records and reqs.future_dynamic_features:
        log.warning(
            "operational_inputs.no_nwp",
            station_id=str(station_id),
            issue_time=str(issue_time),
            nwp_source=nwp_source,
            cycle_time=str(cycle_time),
        )
        return None

    future_dynamic = _pivot_nwp_records(nwp_records, reqs.future_dynamic_features)
    nwp_age_hours = (now - cycle_time).total_seconds() / 3600.0
    if nwp_age_hours < 0:
        log.warning(
            "operational_inputs.nwp_cycle_in_future", nwp_age_hours=nwp_age_hours
        )
        nwp_age_hours = 0.0

    # --- static ---
    static_df: pl.DataFrame | None = None
    station_config = station_store.fetch_station(station_id)
    if station_config is not None and station_config.basin_id is not None:
        basin = basin_store.fetch_basin(station_config.basin_id)
        if basin is not None and basin.attributes:
            static_df = pl.DataFrame([basin.attributes])

    # --- warm-up state ---
    state_result = model_state_store.fetch_latest_state(station_id, model_id)
    prior_state: bytes | None = None
    warm_up_state_age_hours: float | None = None

    if state_result is not None:
        state_time, state_bytes = state_result
        age_hours = (now - state_time).total_seconds() / 3600.0
        warm_up_state_age_hours = age_hours
        prior_state = state_bytes
        warm_up_source = (
            WarmUpSource.FRESH if age_hours < 24.0 else WarmUpSource.SNAPSHOT
        )
    else:
        warm_up_source = WarmUpSource.COLD_START

    inputs = StationModelInputs(
        station_id=station_id,
        data=StationInputData(
            past_targets=past_targets,
            past_dynamic=past_dynamic,
            future_dynamic=future_dynamic,
            static=static_df,
        ),
        issue_time=issue_time,
        forecast_horizon_steps=forecast_horizon_steps,
        time_step=time_step,
    )
    metadata = OperationalInputMetadata(
        warm_up_source=warm_up_source,
        warm_up_state_age_hours=warm_up_state_age_hours,
        observation_staleness_hours=observation_staleness_hours,
        prior_state=prior_state,
        nwp_age_hours=nwp_age_hours,
    )

    return inputs, metadata
