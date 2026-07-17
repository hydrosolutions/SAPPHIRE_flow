from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.services.training_data import resample_to_time_step
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import QcStatus, WarmUpSource
from sapphire_flow.types.model import (
    ModelDataRequirements,
    StationInputData,
    StationModelInputs,
)

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
    from sapphire_flow.types.weather import WeatherForecastRecord

log = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalInputMetadata:
    warm_up_source: WarmUpSource
    warm_up_state_age_hours: float | None
    observation_staleness_hours: float | None
    prior_state: bytes | None
    nwp_age_hours: float


@dataclass(frozen=True, slots=True)
class _AggregatedNwpPoint:
    """A single per-member NWP value aggregated to the model's time_step."""

    valid_time: UtcDatetime
    parameter: str
    member_id: int | None
    value: float


def _member_records_to_wide(records: list[WeatherForecastRecord]) -> pl.DataFrame:
    all_times = sorted({r.valid_time for r in records})
    pivot: dict[UtcDatetime, dict[str, object]] = {
        ts: {"timestamp": ts} for ts in all_times
    }
    for r in records:
        pivot[r.valid_time][r.parameter] = r.value
    return pl.DataFrame(list(pivot.values()))


def _aggregate_nwp_records_to_time_step(
    records: list[WeatherForecastRecord],
    time_step: timedelta,
) -> list[_AggregatedNwpPoint]:
    """Aggregate hourly per-member NWP records to the model's ``time_step``.

    Keyed on ``(bare-parameter, member_id, UTC-calendar-day)`` via the shared
    ``resample_to_time_step`` machinery (mirrors ``_future_dynamic_from_forcing``):
    precipitation SUMs, temperature MEANs (from ``_V0_AGGREGATION_FALLBACK`` on the
    BARE parameter name), on UTC-midnight buckets. All members are preserved.
    """
    if not records:
        return []

    by_member: dict[int | None, list[WeatherForecastRecord]] = defaultdict(list)
    for r in records:
        by_member[r.member_id].append(r)

    aggregated: list[_AggregatedNwpPoint] = []
    for member_id, member_records in by_member.items():
        wide = _member_records_to_wide(member_records)
        daily = resample_to_time_step(wide, time_step, aggregation_methods=None)
        param_cols = [c for c in daily.columns if c != "timestamp"]
        for row in daily.iter_rows(named=True):
            ts = ensure_utc(row["timestamp"])
            for param in param_cols:
                value = row[param]
                if value is None:
                    continue
                aggregated.append(
                    _AggregatedNwpPoint(
                        valid_time=ts,
                        parameter=param,
                        member_id=member_id,
                        value=float(value),
                    )
                )
    return aggregated


def _broadcast_deterministic_features_to_members(
    records: list[_AggregatedNwpPoint],
) -> list[_AggregatedNwpPoint]:
    """Broadcast deterministic (``member_id=None``) daily points across every
    ensemble member present in the SAME batch (Plan 082 Task 2H-snow).

    recap Gateway snow forecasts are deterministic (single run, no ensemble)
    while IFS precipitation/temperature carry 51 members (``fc``=0, ``pf``
    1-50). A model declaring both as ``future_dynamic_features`` needs the
    SAME snow value repeated under every member's column so
    ``_pivot_nwp_records`` (ensemble path) produces ``snow_depth_0``,
    ``snow_depth_1``, ... alongside ``precipitation_0``, ``precipitation_1``,
    ... rather than a single unsuffixed ``snow_depth`` column that only the
    deterministic pivot branch would ever populate.

    No resampling happens here — inputs are already daily-aggregated by
    :func:`_aggregate_nwp_records_to_time_step`. If no real (non-None)
    member is present in the batch, records are returned unchanged (a purely
    deterministic model receives the single ``member_id=None`` series as-is).
    """
    member_ids = sorted({r.member_id for r in records if r.member_id is not None})
    if not member_ids:
        return records

    broadcast: list[_AggregatedNwpPoint] = []
    for r in records:
        if r.member_id is not None:
            broadcast.append(r)
            continue
        broadcast.extend(
            _AggregatedNwpPoint(
                valid_time=r.valid_time,
                parameter=r.parameter,
                member_id=member_id,
                value=r.value,
            )
            for member_id in member_ids
        )
    return broadcast


def _filter_and_cap_daily_records(
    records: list[_AggregatedNwpPoint],
    issue_time: UtcDatetime,
    forecast_horizon_steps: int,
) -> list[_AggregatedNwpPoint]:
    """Drop backdated daily buckets and cap to the forecast horizon.

    Keeps only buckets whose ``valid_time`` is strictly after ``issue_time``
    (dropping the UTC-midnight issue-day bucket that a non-midnight cycle
    backdates), then keeps the earliest ``forecast_horizon_steps`` distinct
    future valid_times. The retained valid_time set is identical across all
    members, so every ensemble member yields the same daily buckets.
    """
    future_times = sorted({r.valid_time for r in records if r.valid_time > issue_time})
    kept_times = frozenset(future_times[:forecast_horizon_steps])
    return [r for r in records if r.valid_time in kept_times]


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


def build_superset_requirements(
    requirements: list[ModelDataRequirements],
) -> ModelDataRequirements:
    """Union the data requirements of all a station's assigned models.

    A station may be assigned models with heterogeneous requirements (e.g. NWP
    models declaring ``future_dynamic_features`` alongside native models that
    declare none). Assembling inputs from only the first model's requirements
    starves the others. This unions feature sets and takes the MAX of the step
    counts so a single per-station assembly covers every assigned model. Feeding
    a model more columns than it needs is harmless — each model slices/reads only
    what it declares. ``spatial_input_type`` / ``ensemble_mode`` /
    ``supported_time_steps`` are not consumed by assembly; the first model's
    values are carried through (with ``supported_time_steps`` unioned).
    """
    if not requirements:
        raise ValueError("Cannot build superset requirements from an empty list")

    return ModelDataRequirements(
        target_parameters=frozenset[str]().union(
            *(r.target_parameters for r in requirements)
        ),
        past_dynamic_features=frozenset[str]().union(
            *(r.past_dynamic_features for r in requirements)
        ),
        future_dynamic_features=frozenset[str]().union(
            *(r.future_dynamic_features for r in requirements)
        ),
        static_features=frozenset[str]().union(
            *(r.static_features for r in requirements)
        ),
        supported_time_steps=requirements[0].supported_time_steps.union(
            *(r.supported_time_steps for r in requirements[1:])
        ),
        lookback_steps=max(r.lookback_steps for r in requirements),
        forecast_horizon_steps=max(r.forecast_horizon_steps for r in requirements),
        spatial_input_type=requirements[0].spatial_input_type,
        ensemble_mode=requirements[0].ensemble_mode,
    )


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
    requirements_override: ModelDataRequirements | None = None,
) -> tuple[StationModelInputs, OperationalInputMetadata] | None:
    now = clock()
    # When a station is assigned models with heterogeneous requirements, the
    # caller passes a SUPERSET ``requirements_override`` so every model receives
    # the data it declares (e.g. NWP future forcing). ``model`` is retained only
    # for its ``data_requirements`` fallback when no override is supplied.
    reqs = (
        requirements_override
        if requirements_override is not None
        else model.data_requirements
    )
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

    # Short-lookback check: warn when some target observations exist but the
    # per-target minimum non-null count is fewer than reqs.lookback_steps.
    # Skips the wholly-absent-obs case (owned by no_observations above) and
    # early-exits for zero-target models (avoids min() of an empty sequence).
    # Column-presence guard: a declared target with zero obs has no column in
    # the resampled frame (_observations_to_wide_dataframe only builds a column
    # when at least one obs exists); indexing an absent column raises
    # ColumnNotFoundError, so count it as 0 instead.
    if latest_obs_ts is not None and reqs.target_parameters:
        per_target_counts = {
            p: (past_targets[p].drop_nulls().len() if p in past_targets.columns else 0)
            for p in reqs.target_parameters
        }
        lookback_got = min(per_target_counts.values())
        if lookback_got < reqs.lookback_steps:
            log.warning(
                "operational_inputs.short_lookback",
                station_id=str(station_id),
                issue_time=str(issue_time),
                representative_model_id=str(model_id),
                per_target_counts=per_target_counts,
                lookback_needed=reqs.lookback_steps,
                lookback_got=lookback_got,
            )

    # --- past_dynamic ---
    past_dynamic_features = list(reqs.past_dynamic_features)
    if past_dynamic_features:
        reanalysis_bindings = station_store.fetch_reanalysis_bindings(station_id)
        raw_forcing = forcing_source.fetch_reanalysis(
            station_configs=reanalysis_bindings,
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

    # Aggregate hourly per-member NWP to the model's time_step (daily) on the
    # BARE parameter name (precip SUM, temp MEAN) BEFORE pivoting to member-
    # suffixed columns, so the aggregation methods resolve correctly.
    daily_nwp_records = _aggregate_nwp_records_to_time_step(nwp_records, time_step)
    # Plan 082 Task 2H-snow: broadcast deterministic (member_id=None) daily
    # points (recap Gateway snow) across every real ensemble member (IFS)
    # present in the same batch. A no-op when no real member is present.
    daily_nwp_records = _broadcast_deterministic_features_to_members(daily_nwp_records)
    # Daily UTC-calendar-day bucketing of a non-midnight cycle backdates the
    # issue-day bucket to UTC midnight (< issue_time) and can add a partial
    # end-day bucket. Drop backdated buckets (keep valid_time > issue_time) and
    # cap to the model's forecast_horizon_steps (earliest N future buckets),
    # applied to the SAME bucket set across all members, so a daily model with
    # max_nan=0 receives exactly <= N clean future steps.
    kept_daily_records = _filter_and_cap_daily_records(
        daily_nwp_records,
        issue_time=issue_time,
        forecast_horizon_steps=forecast_horizon_steps,
    )
    future_dynamic = _pivot_nwp_records(
        kept_daily_records, reqs.future_dynamic_features
    )
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
