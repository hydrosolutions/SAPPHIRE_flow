from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.caravan_statics import resolve_shared_static_frame
from sapphire_flow.services.training_data import resample_to_time_step
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleMode,
    ForcingRoute,
    QcStatus,
    WarmUpSource,
)
from sapphire_flow.types.model import (
    ModelDataRequirements,
    StationInputData,
    StationModelInputs,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
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
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import ModelId, StationId
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.weather import WeatherForecastRecord

log = structlog.get_logger(__name__)


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalInputMetadata:
    warm_up_source: WarmUpSource
    warm_up_state_age_hours: float | None
    observation_staleness_hours: float | None
    nwp_age_hours: float


@dataclass(frozen=True, kw_only=True, slots=True)
class WarmUpState:
    """The three warm-up fields, as loaded from the ``ModelStateStore`` for a
    single ``(station_id, model_id)``. Returned by :func:`load_warm_up_state`.
    """

    prior_state: bytes | None
    warm_up_source: WarmUpSource
    warm_up_state_age_hours: float | None


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRunContext:
    """Assignment-keyed run unit (Plan 148 D1). Carries the shared inputs +
    shared non-state scalars (``observation_staleness_hours``,
    ``nwp_age_hours``) plus THIS assignment's own per-assignment warm-up
    state — never an ``OperationalInputMetadata`` object (which would expose
    the representative-scoped warm-up under the same field names).
    """

    station_id: StationId
    model_id: ModelId
    inputs: StationModelInputs
    observation_staleness_hours: float | None
    nwp_age_hours: float | None
    prior_state: bytes | None
    warm_up_source: WarmUpSource
    warm_up_state_age_hours: float | None


def load_warm_up_state(
    model_state_store: ModelStateStore,
    station_id: StationId,
    model_id: ModelId,
    clock: Callable[[], UtcDatetime],
) -> WarmUpState:
    """Read this ``(station_id, model_id)``'s latest state and classify it.

    ``clock`` is consulted ONLY when a state is present (to classify its age)
    — an empty store returns ``COLD_START`` without calling ``clock()`` at
    all (Plan 148 D2/D4).
    """
    state_result = model_state_store.fetch_latest_state(station_id, model_id)
    if state_result is None:
        return WarmUpState(
            prior_state=None,
            warm_up_source=WarmUpSource.COLD_START,
            warm_up_state_age_hours=None,
        )
    state_time, state_bytes = state_result
    age_hours = (clock() - state_time).total_seconds() / 3600.0
    warm_up_source = WarmUpSource.FRESH if age_hours < 24.0 else WarmUpSource.SNAPSHOT
    return WarmUpState(
        prior_state=state_bytes,
        warm_up_source=warm_up_source,
        warm_up_state_age_hours=age_hours,
    )


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
    feature_horizons: dict[str, int] | None = None,
) -> list[_AggregatedNwpPoint]:
    """Drop backdated daily buckets and cap to the forecast horizon.

    Keeps only buckets whose ``valid_time`` is at or after ``issue_time``
    (``>=``, not ``>``): a non-midnight cycle backdates the UTC-midnight
    issue-day bucket to strictly BEFORE ``issue_time`` (it mixes already-
    elapsed hours with future ones) and that bucket is correctly dropped. A
    midnight-exact ``issue_time`` (a daily cycle issued at UTC 00:00) instead
    labels the issue-day bucket AT ``issue_time`` itself — the whole day is
    still ahead of "now" with zero elapsed-hour contamination, so ``>``
    would wrongly drop a full day of genuinely future NWP precip and open a
    day-wide gap against the past array's last day (Plan 129's "no gap"
    seam-continuity claim). Then keeps the earliest ``forecast_horizon_steps``
    distinct future valid_times.

    ``feature_horizons`` (Plan 151 T6, review fold-in — major), when given,
    caps EACH parameter to its own earliest ``feature_horizons[parameter]``
    valid_times independently (falling back to ``forecast_horizon_steps``
    for a parameter absent from the mapping) — so the retained valid_time
    set can now DIFFER per feature. ``None`` (the default, and every legacy
    caller) keeps one shared valid_time set across every parameter and
    member, exactly as before.
    """
    if feature_horizons is None:
        future_times = sorted(
            {r.valid_time for r in records if r.valid_time >= issue_time}
        )
        kept_times = frozenset(future_times[:forecast_horizon_steps])
        return [r for r in records if r.valid_time in kept_times]

    by_parameter: dict[str, list[_AggregatedNwpPoint]] = defaultdict(list)
    for r in records:
        by_parameter[r.parameter].append(r)

    kept: list[_AggregatedNwpPoint] = []
    for parameter, param_records in by_parameter.items():
        horizon = feature_horizons.get(parameter, forecast_horizon_steps)
        future_times = sorted(
            {r.valid_time for r in param_records if r.valid_time >= issue_time}
        )
        kept_times = frozenset(future_times[:horizon])
        kept.extend(r for r in param_records if r.valid_time in kept_times)
    return kept


def build_future_dynamic_frame(
    records: list[WeatherForecastRecord],
    *,
    time_step: timedelta,
    issue_time: UtcDatetime,
    forecast_horizon_steps: int,
    future_dynamic_features: frozenset[str],
    ensemble_mode: EnsembleMode,
    feature_horizons: dict[str, int] | None = None,
) -> pl.DataFrame:
    """Public: the shared hourly-to-model-time_step reduction (aggregate,
    broadcast deterministic features across members, drop backdated buckets,
    cap to the horizon, pivot to a wide frame). Extracted so Plan 151 T6's
    per-assignment assembler can build a ``future_dynamic`` frame from a
    forcing track's OWN records with byte-identical behaviour to the legacy
    superset assembler below, which now calls this too (pure refactor, no
    behaviour change).

    ``feature_horizons`` (Plan 151 T6, review fold-in — major) is the
    PER-FEATURE cap: when given, each parameter is capped to its OWN
    ``feature_horizons[parameter]`` (falling back to the scalar
    ``forecast_horizon_steps`` for a parameter absent from the mapping)
    INSTEAD of one scalar cap applied uniformly to every feature. Without
    this, a 2-day precip / 10-day temp assignment fed 10 raw days of both
    would retain 10 values for precip too — silently over-delivering data
    the assignment never declared (D9). ``None`` (the default) preserves the
    legacy scalar-cap behaviour byte-for-byte — the superset assembler below
    never passes it.
    """
    daily_nwp_records = _aggregate_nwp_records_to_time_step(records, time_step)
    daily_nwp_records = _broadcast_deterministic_features_to_members(daily_nwp_records)
    kept_daily_records = _filter_and_cap_daily_records(
        daily_nwp_records,
        issue_time=issue_time,
        forecast_horizon_steps=forecast_horizon_steps,
        feature_horizons=feature_horizons,
    )
    return _pivot_nwp_records(
        kept_daily_records, future_dynamic_features, ensemble_mode
    )


def reduced_daily_step_times(
    records: list[WeatherForecastRecord],
    *,
    time_step: timedelta,
    issue_time: UtcDatetime,
) -> dict[tuple[str, int | None], frozenset[UtcDatetime]]:
    """Public: the clean daily bucket VALID_TIMES per ``(parameter,
    member_id)`` AFTER the same aggregation + backdated-bucket drop that
    :func:`build_future_dynamic_frame` (and legacy assembly) apply — Plan
    151 T5's completeness gate (D8) needs the exact SET, not just a count
    (review fold-in, blocker): two members can each individually reach a
    horizon's step COUNT while covering entirely different calendar days
    (member 0 = days 1-2, member 1 = days 3-4), which a count-only compare
    would wrongly accept even though ``_filter_and_cap_daily_records``'s
    global earliest-N-times cap would then silently retain one member and
    drop the other. Deliberately does NOT apply the horizon CAP
    (`_filter_and_cap_daily_records`'s ``forecast_horizon_steps``
    truncation) — a completeness check needs the true available set, not
    one already truncated to some other consumer's horizon.
    """
    daily_nwp_records = _aggregate_nwp_records_to_time_step(records, time_step)
    kept_times = frozenset(
        r.valid_time for r in daily_nwp_records if r.valid_time >= issue_time
    )
    times: dict[tuple[str, int | None], set[UtcDatetime]] = defaultdict(set)
    for r in daily_nwp_records:
        if r.valid_time in kept_times:
            times[(r.parameter, r.member_id)].add(r.valid_time)
    return {key: frozenset(vals) for key, vals in times.items()}


def reduced_daily_step_counts(
    records: list[WeatherForecastRecord],
    *,
    time_step: timedelta,
    issue_time: UtcDatetime,
) -> dict[tuple[str, int | None], int]:
    """Public: count clean daily buckets per ``(parameter, member_id)`` —
    see :func:`reduced_daily_step_times` for the underlying set this counts
    and why a count alone is not sufficient for the completeness gate."""
    return {
        key: len(vals)
        for key, vals in reduced_daily_step_times(
            records, time_step=time_step, issue_time=issue_time
        ).items()
    }


def _pivot_nwp_records(
    records: list[_AggregatedNwpPoint],
    future_dynamic_features: frozenset[str],
    ensemble_mode: EnsembleMode,
) -> pl.DataFrame:
    """Pivot aggregated NWP points to a wide per-timestamp frame.

    Column naming is keyed on the MODEL's ``ensemble_mode`` (the requirement),
    never on which members happen to be present (Plan 127 Fix 2): a ``SINGLE``
    model must get BARE columns even on a complete-ensemble cycle, so it
    selects the CONTROL rows (``member_id`` in ``{None, 0}`` -- snow is
    ``None``, IFS ``fc``/control is ``0``) and drops any ``pf`` members
    (``member_id >= 1``) before pivoting. ``ENSEMBLE`` is unchanged: columns
    are member-suffixed (``precipitation_0``, ...) whenever a real member is
    present, falling back to bare columns for a wholly deterministic batch.
    """
    if not records:
        return pl.DataFrame()

    feature_cols = list(future_dynamic_features)

    if ensemble_mode is EnsembleMode.SINGLE:
        records = [r for r in records if r.member_id in (None, 0)]

    all_times = sorted({r.valid_time for r in records})
    members = sorted({r.member_id for r in records if r.member_id is not None})

    if ensemble_mode is EnsembleMode.ENSEMBLE and members:
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
        # SINGLE (control-only, bare columns) or a deterministic batch (no
        # real member present): columns are bare param names.
        pivot2: dict[object, dict] = {ts: {"timestamp": ts} for ts in all_times}
        for r in records:
            if r.parameter not in feature_cols:
                continue
            pivot2[r.valid_time][r.parameter] = r.value
        return pl.DataFrame(list(pivot2.values()))


def observations_to_wide_dataframe(
    observations: list[Observation], parameters: list[str]
) -> pl.DataFrame:
    if not observations:
        return pl.DataFrame()
    all_timestamps = sorted({o.timestamp for o in observations})
    pivot: dict[object, dict] = {ts: {"timestamp": ts} for ts in all_timestamps}
    for o in observations:
        if o.parameter in parameters:
            pivot[o.timestamp][o.parameter] = o.value
    return pl.DataFrame(list(pivot.values()))


def raw_forcing_to_dataframe(
    raw_records: list[RawHistoricalForcing],
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
    what it declares. ``spatial_input_type`` / ``supported_time_steps`` are not
    consumed by assembly; the first model's values are carried through (with
    ``supported_time_steps`` unioned). ``ensemble_mode`` IS consumed (Plan 127 —
    NWP forcing-column shape) and is derived from the NWP-consuming models, with a
    fail-fast guard on a mixed SINGLE/ENSEMBLE station (see below).
    """
    if not requirements:
        raise ValueError("Cannot build superset requirements from an empty list")

    # Plan 127: ``ensemble_mode`` IS now consumed by assembly (the NWP forcing-
    # column shape keys on it). Derive it from the models that actually consume
    # NWP (``future_dynamic_features`` non-empty), not blindly from the first
    # model. A station mixing SINGLE and ENSEMBLE NWP-consuming models would need
    # both bare AND member-suffixed columns from one assembly — not supported
    # here; that is deferred to Plan 126. Fail fast rather than silently serve one
    # model the wrong column shape.
    nwp_modes = {r.ensemble_mode for r in requirements if r.future_dynamic_features}
    if len(nwp_modes) > 1:
        raise ConfigurationError(
            "Station assigns both SINGLE and ENSEMBLE NWP-consuming models; a "
            "single input assembly cannot serve both bare and member-suffixed "
            "forcing columns (deferred to Plan 126). Assign homogeneous "
            "ensemble_mode NWP models per station."
        )
    superset_ensemble_mode = next(iter(nwp_modes), requirements[0].ensemble_mode)

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
        ensemble_mode=superset_ensemble_mode,
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
    static_naming_models: Sequence[object] | None = None,
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

    past_targets = observations_to_wide_dataframe(all_observations, target_parameters)
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
    # the resampled frame (observations_to_wide_dataframe only builds a column
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
        past_dynamic = raw_forcing_to_dataframe(
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
        # Plan 145 D3.2d: log-and-continue rather than abort-the-whole-assembly.
        # `_pivot_nwp_records([], ...)` below yields an empty `future_dynamic`
        # frame, and the per-model `assess_future_coverage` gate (run per-model,
        # AFTER assembly, on the model's OWN `future_dynamic_features` — never
        # this station-superset `reqs`) is what suppresses an NWP-fed model on
        # that empty frame ("required feature '<x>' absent"), advancing the
        # fallback loop to a non-NWP model. Returning `None` here used to skip
        # the WHOLE station — including a non-NWP fallback assigned alongside
        # the NWP-fed model — whenever the superset future read was empty
        # (snow-absent or IFS-absent alike). No superset pruning and no
        # per-variable availability is threaded here; this is the general,
        # simpler fix (see the plan's Problem §3.4 / D3.2d).
        log.warning(
            "operational_inputs.no_nwp",
            station_id=str(station_id),
            issue_time=str(issue_time),
            nwp_source=nwp_source,
            cycle_time=str(cycle_time),
        )

    # Aggregate hourly per-member NWP to the model's time_step (daily), drop
    # backdated buckets, cap to the horizon, and pivot to a wide frame — see
    # `build_future_dynamic_frame` for the full rationale (Plan 151 T6
    # extraction; behaviour unchanged, now shared with the per-track
    # assembler).
    future_dynamic = build_future_dynamic_frame(
        nwp_records,
        time_step=time_step,
        issue_time=issue_time,
        forecast_horizon_steps=forecast_horizon_steps,
        future_dynamic_features=reqs.future_dynamic_features,
        ensemble_mode=reqs.ensemble_mode,
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
            # Plan 155 T2 (G8) + D16, fixer round (major finding): the
            # frame is shared across EVERY model assigned to this station
            # (``reqs.static_features`` is a cross-model UNION when a
            # ``requirements_override`` superset is supplied -- see
            # ``flows/run_forecast_cycle.py::build_superset_requirements``),
            # so resolution must be scoped PER assigned model, not gated on
            # ``model`` alone (a single representative, e.g. the
            # highest-priority assignment) -- see
            # ``services/caravan_statics.py::resolve_shared_static_frame``
            # for why: a bare-name collision between a CARAVAN-declaring and
            # a NATIVE co-assignment must raise, not silently share one
            # model's resolution with the other. Defaults to ``[model]``
            # when the caller has no broader assignment set (e.g. the GROUP
            # path, which only ever assembles for one model).
            # Round-3 review (MAJOR): the invoked ``model`` is ALWAYS part
            # of the resolution set, never merely the fallback when the
            # caller passes nothing. A ``static_naming_models`` that omitted
            # it -- ``[]``, or a list built from a different assignment set
            # -- made ``resolve_shared_static_frame`` see no CARAVAN
            # declaration and hand this model the raw bare attributes, i.e.
            # CAMELS-CH's ``area``, which rescales every discharge. Uniting
            # rather than replacing makes that leak structurally impossible
            # instead of relying on every caller to pass a correct list; a
            # genuine regime disagreement still raises via the
            # differing-regimes guard inside the resolver.
            static_df = pl.DataFrame(
                [
                    resolve_shared_static_frame(
                        basin.attributes,
                        [model, *(static_naming_models or ())],
                        station_code=station_config.code,
                    )
                ]
            )

    # --- warm-up state ---
    # ``clock=lambda: now`` reuses the SAME instant already computed above
    # (for observation staleness / nwp age) so this extraction is exactly
    # behaviour-preserving: no second real ``clock()`` call is introduced.
    # The state BYTES (``warm_up.prior_state``) are not carried out of the
    # assembler — the station-cycle path reads its own per-assignment state
    # via ``ModelRunContext`` (Plan 148 D2). Only source/age survive on
    # ``OperationalInputMetadata`` as shared provenance for the GROUP path.
    warm_up = load_warm_up_state(model_state_store, station_id, model_id, lambda: now)
    warm_up_state_age_hours = warm_up.warm_up_state_age_hours
    warm_up_source = warm_up.warm_up_source

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
        # Plan 151 D10: stated explicitly (it is also the default) — this is
        # the LEGACY superset assembler. `build_superset_requirements` sizes
        # ONE frame to the MAX horizon across the station's co-assigned
        # models, so a shorter-horizon model is over-delivered BY DESIGN and
        # the FI boundary must NOT truncate it to that model's own
        # `future_steps` (`models/nwp_regression.py`: over-delivery "is
        # tolerated and forecast in full").
        forcing_route=ForcingRoute.LEGACY_SUPERSET,
    )
    metadata = OperationalInputMetadata(
        warm_up_source=warm_up_source,
        warm_up_state_age_hours=warm_up_state_age_hours,
        observation_staleness_hours=observation_staleness_hours,
        nwp_age_hours=nwp_age_hours,
    )

    return inputs, metadata
