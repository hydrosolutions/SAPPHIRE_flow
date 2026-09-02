from __future__ import annotations

from datetime import UTC, datetime, timedelta  # noqa: TCH003
from typing import TYPE_CHECKING, cast

import polars as pl
import structlog

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.caravan_statics import (
    available_declared_static_keys,
    declared_static_naming,
    project_declared_static_attributes,
)
from sapphire_flow.types.basin import non_null_static_keys
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AggregationMethod, QcStatus, StaticNaming

if TYPE_CHECKING:
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )
    from sapphire_flow.protocols.stores import (
        BasinStore,
        ObservationStore,
        StationStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.model import (
        GroupTrainingData,
        ModelDataRequirements,
        StationTrainingData,
    )
    from sapphire_flow.types.station import StationGroup

log = structlog.get_logger()

_V0_AGGREGATION_FALLBACK: dict[str, AggregationMethod] = {
    "discharge": AggregationMethod.MEAN,
    "water_level": AggregationMethod.MEAN,
    "precipitation": AggregationMethod.SUM,
    "temperature": AggregationMethod.MEAN,
    "relative_humidity": AggregationMethod.MEAN,
    "wind_speed": AggregationMethod.MEAN,
    "wind_direction": AggregationMethod.MEAN,
    "global_radiation": AggregationMethod.MEAN,
    "reference_et": AggregationMethod.SUM,
    "snow_water_equivalent": AggregationMethod.MEAN,
    # Plan 145 D2: canonical Recap snow variables (recap_gateway.RECAP_VARIABLES).
    # swe/snow_depth are states (MEAN); snowmelt is a flux (SUM). No `rof` key —
    # `_accumulate_snow` stamps `parameter=variable.canonical` ("snowmelt"), never
    # the source name "rof".
    "swe": AggregationMethod.MEAN,
    "snow_depth": AggregationMethod.MEAN,
    "snowmelt": AggregationMethod.SUM,
}


def aggregation_method_for(parameter: str) -> AggregationMethod:
    """The v0 aggregation method ``parameter`` is TRAINED against (Plan 228
    D2): a caller comparing a forecast to observations (e.g. skill scoring)
    must aggregate with this SAME method, never a different one, or the fix
    substitutes one quantity mismatch for another."""
    return _V0_AGGREGATION_FALLBACK.get(parameter, AggregationMethod.MEAN)


def resolved_aggregation_methods(
    data_requirements: ModelDataRequirements,
) -> dict[str, AggregationMethod]:
    """Per-column aggregation methods for :func:`resample_to_time_step`,
    with a model's DECLARED per-variable aggregation
    (``PastKnownVariable.aggregation`` / ``FutureKnownVariable.aggregation``,
    captured into ``ModelDataRequirements.declared_aggregations`` by the FI
    adapter) taking precedence over the v0 name-keyed fallback table.

    Plan 228 review fixer round (blocker): every caller that previously
    passed ``aggregation_methods=None`` to ``resample_to_time_step`` ignored
    a model's declaration entirely and fell back to matching purely on
    parameter NAME — a model legally declaring, say, ``discharge`` with
    ``AggregationMethod.MAX`` would silently receive/train on the MEAN
    fallback instead. Declared values win; any parameter the model did not
    declare an aggregation for keeps the v0 fallback unchanged.
    """
    return {**_V0_AGGREGATION_FALLBACK, **dict(data_requirements.declared_aggregations)}


_CADENCE_TOLERANCE_FRACTION = 0.01


def _detect_cadence_us(df: pl.DataFrame) -> float | None:
    """Median gap (microseconds) between sorted, distinct ``timestamp`` rows.

    Returns ``None`` when the frame is too small (or lacks a usable spread)
    to have a detectable cadence at all — callers treat that as "nothing to
    check", never as a match.
    """
    if df.is_empty() or df.height < 2:
        return None
    timestamps = df["timestamp"].sort()
    diffs = timestamps.diff().drop_nulls()
    if diffs.is_empty():
        return None
    median_diff_us = diffs.cast(pl.Int64).median()
    if median_diff_us is None:
        return None
    # polars .median() is typed PythonLiteral; the cast column is Int64.
    return cast("float", median_diff_us)


def _all_timestamps_calendar_aligned(df: pl.DataFrame, target_us: float) -> bool:
    """True iff every ``timestamp`` row already sits on a whole multiple of
    ``target_us`` since the Unix epoch (Plan 228 D4's calendar grid).

    Plan 228 review fixer round (major): this is checked BEFORE (and
    independently of) any cadence/height shortcut in
    :func:`resample_to_time_step` — a frame at the right cadence but the
    wrong PHASE (e.g. daily rows all stamped 06:00) must still fall through
    to bucketing, not be waved through as "already correct".
    """
    if df.is_empty():
        return True
    if target_us <= 0:
        return True
    remainder = df["timestamp"].dt.epoch("us") % int(target_us)
    return bool((remainder == 0).all())


def validate_time_step_cadence(
    df: pl.DataFrame,
    time_step: timedelta,
    *,
    context: str,
) -> None:
    """Fail loudly when ``df``'s delivered cadence does not match ``time_step``.

    Plan 228 D1 (option C): a model's ``PastKnownVariable.lookback`` is a
    count of steps of the declared ``time_step`` (ForecastInterface
    ``input/variable.py``/``input/requirement.py``) — the model is entitled
    to assume it, never obliged to check it. P1 was exactly this silently
    violated: hindcast handed 7 raw ~10-minute rows against a declared
    ``timedelta(days=1)``. Both assemblers resample ``past_targets`` to
    ``time_step`` themselves; this is the backstop that makes a future
    resampling omission (a new caller, a refactor) fail loudly instead of
    silently, at the one point both paths already call through.

    Deliberately scoped to ``past_targets`` callers only — ``past_dynamic``
    legitimately carries a finer, unresampled cadence than the model's
    ``time_step`` in the operational path (e.g. hourly reanalysis feeding a
    daily model), so a blanket check would misfire there.

    Checks **every** adjacent gap, not the median (Plan 228 review fixer
    round): a median-of-N check passes an isolated missing bucket (e.g.
    resampled days 1,2,3,5,6 — gaps 1d,1d,2d,1d — median is still 1d), which
    silently lets a model read non-consecutive buckets as if they were
    consecutive steps (`.tail(n)`/`values[-n:]` are position-based, so a gap
    shifts every earlier value's apparent recency by one step). Every gap
    must match ``time_step`` within tolerance; a single bad gap raises.
    """
    if df.is_empty() or df.height < 2:
        return
    timestamps = df["timestamp"].sort()
    diffs_us = timestamps.diff().drop_nulls().cast(pl.Int64)
    if diffs_us.is_empty():
        return
    target_us = time_step.total_seconds() * 1_000_000
    if target_us <= 0:
        return
    tolerance_us = target_us * _CADENCE_TOLERANCE_FRACTION
    bad_gaps = diffs_us.filter((diffs_us - target_us).abs() > tolerance_us)
    if bad_gaps.len() > 0:
        worst_us = bad_gaps.abs().max()
        actual = timedelta(microseconds=int(cast("float", worst_us)))
        raise ConfigurationError(
            f"{context}: delivered cadence includes a gap of {actual} that "
            f"does not match the declared time_step {time_step} "
            f"({bad_gaps.len()} of {diffs_us.len()} gaps out of tolerance)"
        )


def floor_to_time_step(instant: UtcDatetime, time_step: timedelta) -> UtcDatetime:
    """The UTC-calendar bucket boundary at or before ``instant`` (Plan 228
    D4): buckets are whole multiples of ``time_step`` since the Unix epoch
    (UTC midnight, for a daily step) — never phase-aligned to ``instant``
    itself. A previous fixer round did the opposite (an ``anchor`` parameter
    that phase-aligned buckets to a forecast's own timestamp); D4 retracts
    that: it made hindcast consume rolling windows while training consumes
    UTC calendar days, and it aligned to ``valid_time`` — precisely the value
    Plan 226 exists to correct.
    """
    step_seconds = time_step.total_seconds()
    if step_seconds <= 0:
        return instant
    floored_seconds = (instant.timestamp() // step_seconds) * step_seconds
    return ensure_utc(datetime.fromtimestamp(floored_seconds, tz=UTC))


def aligned_lookback_bounds(
    instant: UtcDatetime,
    lookback_steps: int,
    time_step: timedelta,
) -> tuple[UtcDatetime, UtcDatetime]:
    """The ``[start, end)`` fetch bounds covering exactly ``lookback_steps``
    COMPLETE UTC-calendar buckets of ``time_step``, ending at the last bucket
    boundary at or before ``instant`` (Plan 228 D4).

    Filtering after the fact is not sufficient: a naive
    ``[instant - lookback_steps * time_step, instant)`` window has a partial
    bucket at BOTH ends whenever ``instant`` is not itself a bucket boundary
    (e.g. a 06:00 UTC cycle for a daily model) — dropping only the trailing
    one leaves a corrupt leading day, and dropping both starves a 7-day model
    to 6 days. Aligning ``end`` to the boundary at or before ``instant``
    (dropping the partial trailing bucket) and deriving ``start`` from THAT
    aligned ``end`` (extending earlier than the naive start whenever
    ``instant`` itself isn't aligned) is what keeps every one of the
    ``lookback_steps`` buckets whole.
    """
    end = floor_to_time_step(instant, time_step)
    start = ensure_utc(end - lookback_steps * time_step)
    return start, end


def resample_to_time_step(
    df: pl.DataFrame,
    time_step: timedelta,
    aggregation_methods: dict[str, AggregationMethod] | None = None,
) -> pl.DataFrame:
    """Resample a wide-format observations DataFrame to the target time_step.

    Expects columns: ``timestamp`` (datetime) + one column per parameter.
    Returns as-is ONLY when the data both matches ``time_step`` cadence AND
    is already stamped on the UTC-calendar grid.

    Buckets are UTC-calendar-aligned (whole multiples of ``time_step`` since
    the Unix epoch) — Plan 228 D4: every path aggregates onto the SAME grid,
    never phase-aligned to a caller's own timestamp. Callers that need the
    result to line up with a specific instant (an ``issue_time``, a
    forecast's ``valid_time``) must fetch pre-aligned bounds themselves (see
    :func:`aligned_lookback_bounds`), not ask this function to shift the
    grid to meet them halfway.

    Plan 228 review fixer round (major): the fast path used to trigger on
    cadence match ALONE, so already-daily data stamped off the calendar grid
    (e.g. every row at 06:00, from a non-midnight operational cycle) was
    returned byte-for-byte unchanged — never rebucketed onto the calendar
    grid, exactly the phase-aligned behavior D4 forbids. Calendar alignment
    is now checked FIRST and independently of cadence/height, so a
    misaligned frame (including a single misaligned row) always falls
    through to the ``group_by_dynamic`` bucketing below, which re-labels it
    onto the calendar grid.

    Plan 228 review fixer round (major): sorted by ``timestamp`` on EVERY
    return path, including the fast paths above — a caller (e.g. hindcast's
    ``.tail(declared_lookback_steps)``) trims the CHRONOLOGICAL tail, and an
    unordered store read (no ``ORDER BY``) must not silently defeat that.
    """
    if df.is_empty():
        return df
    df = df.sort("timestamp")

    methods = (
        aggregation_methods
        if aggregation_methods is not None
        else _V0_AGGREGATION_FALLBACK
    )

    target_us = time_step.total_seconds() * 1_000_000
    if _all_timestamps_calendar_aligned(df, target_us):
        if df.height < 2:
            return df
        # Detect current cadence via median gap between sorted timestamps.
        median_diff_us = _detect_cadence_us(df)
        if median_diff_us is None:
            return df
        if abs(median_diff_us - target_us) < target_us * _CADENCE_TOLERANCE_FRACTION:
            return df

    # Build per-column aggregation expressions for wide-format DataFrame.
    parameter_cols = [c for c in df.columns if c != "timestamp"]
    agg_exprs: list[pl.Expr] = []
    for col in parameter_cols:
        method = methods.get(col)
        if method is None:
            log.warning(
                "resample_to_time_step.unknown_parameter",
                parameter=col,
                fallback="mean",
            )
            method = AggregationMethod.MEAN
        if method == AggregationMethod.SUM:
            agg_exprs.append(pl.col(col).sum())
        elif method == AggregationMethod.MAX:
            agg_exprs.append(pl.col(col).max())
        else:
            agg_exprs.append(pl.col(col).mean())

    working = df.sort("timestamp")
    resampled = (
        working.group_by_dynamic("timestamp", every=_timedelta_to_polars(time_step))
        .agg(agg_exprs)
        .sort("timestamp")
    )
    return resampled


def _timedelta_to_polars(td: timedelta) -> str:
    """Convert a timedelta to a Polars duration string (e.g. '1h', '1d')."""
    total_seconds = int(td.total_seconds())
    if total_seconds % 86400 == 0:
        return f"{total_seconds // 86400}d"
    if total_seconds % 3600 == 0:
        return f"{total_seconds // 3600}h"
    if total_seconds % 60 == 0:
        return f"{total_seconds // 60}m"
    return f"{total_seconds}s"


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
    pivot: dict[str, dict] = {ts: {"timestamp": ts} for ts in all_timestamps}
    for row in rows:
        ts = row["timestamp"]
        for key, val in row.items():
            if key != "timestamp":
                pivot[ts][key] = val
    return pl.DataFrame(list(pivot.values()))


def _observations_to_dataframe(observations: list, parameter: str) -> pl.DataFrame:
    rows = [{"timestamp": o.timestamp, parameter: o.value} for o in observations]
    return pl.DataFrame(rows)


def assemble_station_training_data(
    station_id: StationId,
    model: StationForecastModel | GroupForecastModel,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    time_step: timedelta,
    forcing_source: WeatherReanalysisSource,
    obs_store: ObservationStore,
    basin_store: BasinStore,
    station_store: StationStore,
) -> StationTrainingData | None:
    from sapphire_flow.types.model import StationTrainingData

    station = station_store.fetch_station(station_id)
    if station is None:
        log.warning("training_data.station_not_found", station_id=str(station_id))
        return None

    targets = station.forecast_targets
    parameter = next(iter(targets), "discharge") if targets else "discharge"
    observations = obs_store.fetch_observations(
        station_id=station_id,
        parameter=parameter,
        start=period_start,
        end=period_end,
        qc_status=QcStatus.QC_PASSED,
    )
    if not observations:
        log.warning(
            "training_data.no_observations",
            station_id=str(station_id),
            period_start=str(period_start),
            period_end=str(period_end),
        )
        return None

    past_features = model.data_requirements.past_dynamic_features
    future_features = model.data_requirements.future_dynamic_features
    required_features = sorted(past_features | future_features)
    if required_features:
        weather_sources = station_store.fetch_reanalysis_bindings(station_id)
        if not weather_sources:
            log.warning("training_data.no_weather_sources", station_id=str(station_id))
            return None

        raw_forcing = forcing_source.fetch_reanalysis(
            station_configs=weather_sources,
            start=period_start,
            end=period_end,
            parameters=required_features,
        )

        forcing_df: pl.DataFrame | None = _raw_forcing_to_dataframe(
            raw_forcing, station_id, required_features
        )
        if forcing_df is None:
            log.warning("training_data.no_forcing", station_id=str(station_id))
            return None
    else:
        forcing_df = pl.DataFrame(schema={"timestamp": pl.Datetime("us", "UTC")})

    forcing_columns = set(forcing_df.columns) - {"timestamp"}
    missing_features = (past_features | future_features) - forcing_columns
    if missing_features:
        log.warning(
            "training_data.missing_features",
            station_id=str(station_id),
            missing=sorted(missing_features),
        )
        return None

    static_attributes: pl.DataFrame | None = None
    if station.basin_id is not None:
        basin = basin_store.fetch_basin(station.basin_id)
        if basin is None or not basin.attributes:
            # Plan 120 Task 2D (D-UP prerequisite): a dangling basin_id or a
            # basin row with no/empty attributes must fail loud UPSTREAM when
            # static features are required — falling through to
            # static_attributes=None here would let a required-static model
            # silently train without them, and would let a required-static
            # artifact reach the lineage helper with no basin to reference.
            if model.data_requirements.static_features:
                log.warning(
                    "training_data.missing_static_attributes",
                    station_id=str(station_id),
                    missing=sorted(model.data_requirements.static_features),
                )
                return None
        else:
            declared_names = model.data_requirements.static_features
            static_naming = declared_static_naming(model)
            # Plan 155 T2 (G8) + D16: a Caravan-DECLARING model's own names
            # (e.g. PT's "area") never appear bare in `basin.attributes` --
            # they live `caravan:`-namespaced (D15) -- so the available set
            # must resolve through the alias/direct rule instead of (never
            # in addition to) the raw bare key set, ahead of training's own
            # missing-static gate. A NATIVE model (the default) keeps
            # today's raw-key-set/frame behaviour byte-for-byte.
            if declared_names:
                available = (
                    available_declared_static_keys(
                        basin.attributes, declared_names, station_code=station.code
                    )
                    if static_naming is StaticNaming.CARAVAN
                    else non_null_static_keys(basin.attributes)
                )
                missing_attrs = declared_names - available
                if missing_attrs:
                    log.warning(
                        "training_data.missing_static_attributes",
                        station_id=str(station_id),
                        missing=sorted(missing_attrs),
                    )
                    return None
            static_attributes = pl.DataFrame(
                [
                    project_declared_static_attributes(
                        basin.attributes, declared_names, station_code=station.code
                    )
                    if static_naming is StaticNaming.CARAVAN
                    else basin.attributes
                ]
            )
    elif model.data_requirements.static_features:
        log.warning(
            "training_data.missing_static_attributes",
            station_id=str(station_id),
            missing=sorted(model.data_requirements.static_features),
        )
        return None

    aggregation_methods = resolved_aggregation_methods(model.data_requirements)
    past_targets_df = _observations_to_dataframe(observations, parameter)
    past_targets_df = resample_to_time_step(
        past_targets_df, time_step, aggregation_methods=aggregation_methods
    )

    # Past-known forcing features are delivered as history (past_dynamic); the
    # future-known forcing (e.g. NWP precip/temp) is delivered into future_dynamic,
    # timestamp-aligned to past_targets. The discharge target stays in past_targets.
    past_dynamic_df = _select_feature_columns(forcing_df, past_features)
    future_dynamic_df = _future_dynamic_from_forcing(
        forcing_df=forcing_df,
        future_features=future_features,
        past_targets=past_targets_df,
        time_step=time_step,
        aggregation_methods=aggregation_methods,
    )

    return StationTrainingData(
        past_targets=past_targets_df,
        past_dynamic=past_dynamic_df,
        future_dynamic=future_dynamic_df,
        static=static_attributes,
        time_step=time_step,
        val_start=None,
    )


def _select_feature_columns(
    forcing_df: pl.DataFrame, features: frozenset[str]
) -> pl.DataFrame:
    columns = ["timestamp", *sorted(features)]
    return forcing_df.select([c for c in columns if c in forcing_df.columns])


def _future_dynamic_from_forcing(
    *,
    forcing_df: pl.DataFrame,
    future_features: frozenset[str],
    past_targets: pl.DataFrame,
    time_step: timedelta,
    aggregation_methods: dict[str, AggregationMethod] | None = None,
) -> pl.DataFrame:
    if not future_features:
        return forcing_df.select("timestamp").clear()

    future_cols = sorted(future_features)
    future_forcing = resample_to_time_step(
        forcing_df.select(["timestamp", *future_cols]),
        time_step,
        aggregation_methods=aggregation_methods,
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))

    return (
        past_targets.select(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
        .join(future_forcing, on="timestamp", how="left")
        .sort("timestamp")
    )


def assemble_group_training_data(
    group: StationGroup,
    model: GroupForecastModel,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    time_step: timedelta,
    forcing_source: WeatherReanalysisSource,
    obs_store: ObservationStore,
    basin_store: BasinStore,
    station_store: StationStore,
) -> GroupTrainingData | None:
    from sapphire_flow.types.model import GroupTrainingData

    past_targets_parts: list[pl.DataFrame] = []
    past_dynamic_parts: list[pl.DataFrame] = []
    future_dynamic_parts: list[pl.DataFrame] = []
    static_parts: list[pl.DataFrame] = []
    valid_station_ids: list[StationId] = []

    for station_id in group.station_ids:
        data = assemble_station_training_data(
            station_id=station_id,
            model=model,
            period_start=period_start,
            period_end=period_end,
            time_step=time_step,
            forcing_source=forcing_source,
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )
        if data is None:
            continue

        sid_col = pl.lit(str(station_id)).alias("station_id")
        past_targets_parts.append(data.past_targets.with_columns(sid_col))
        past_dynamic_parts.append(data.past_dynamic.with_columns(sid_col))
        future_dynamic_parts.append(data.future_dynamic.with_columns(sid_col))
        if data.static is not None:
            static_parts.append(data.static.with_columns(sid_col))
        valid_station_ids.append(station_id)

    if not valid_station_ids:
        log.warning(
            "training_data.group_no_data",
            group_id=str(group.id),
        )
        return None

    def _reorder(df: pl.DataFrame) -> pl.DataFrame:
        cols = ["station_id"] + [c for c in df.columns if c != "station_id"]
        return df.select(cols)

    return GroupTrainingData(
        group_id=group.id,
        station_ids=tuple(valid_station_ids),
        past_targets=_reorder(pl.concat(past_targets_parts)),
        past_dynamic=_reorder(pl.concat(past_dynamic_parts)),
        future_dynamic=_reorder(pl.concat(future_dynamic_parts)),
        static=_reorder(pl.concat(static_parts)) if static_parts else None,
        time_step=time_step,
        val_start=None,
    )
