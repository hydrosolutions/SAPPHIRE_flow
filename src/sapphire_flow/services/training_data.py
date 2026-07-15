from __future__ import annotations

from datetime import timedelta  # noqa: TCH003
from typing import TYPE_CHECKING, cast

import polars as pl
import structlog

from sapphire_flow.types.enums import AggregationMethod, QcStatus

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
    from sapphire_flow.types.model import GroupTrainingData, StationTrainingData
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
}


def resample_to_time_step(
    df: pl.DataFrame,
    time_step: timedelta,
    aggregation_methods: dict[str, AggregationMethod] | None = None,
) -> pl.DataFrame:
    """Resample a wide-format observations DataFrame to the target time_step.

    Expects columns: ``timestamp`` (datetime) + one column per parameter.
    Returns as-is if the data cadence already matches ``time_step``.
    """
    if df.is_empty() or df.height < 2:
        return df

    methods = (
        aggregation_methods
        if aggregation_methods is not None
        else _V0_AGGREGATION_FALLBACK
    )

    # Detect current cadence via median gap between sorted timestamps.
    timestamps = df["timestamp"].sort()
    diffs = timestamps.diff().drop_nulls()
    if diffs.is_empty():
        return df
    median_diff_us = diffs.cast(pl.Int64).median()
    target_us = time_step.total_seconds() * 1_000_000
    if (
        median_diff_us is not None
        # polars .median() is typed PythonLiteral; the cast column is Int64
        and abs(cast("float", median_diff_us) - target_us) < target_us * 0.01
    ):
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
        else:
            agg_exprs.append(pl.col(col).mean())

    resampled = (
        df.sort("timestamp")
        .group_by_dynamic("timestamp", every=_timedelta_to_polars(time_step))
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
        if basin is not None and basin.attributes:
            if model.data_requirements.static_features:
                missing_attrs = model.data_requirements.static_features - set(
                    basin.attributes.keys()
                )
                if missing_attrs:
                    log.warning(
                        "training_data.missing_static_attributes",
                        station_id=str(station_id),
                        missing=sorted(missing_attrs),
                    )
                    return None
            static_attributes = pl.DataFrame([basin.attributes])
    elif model.data_requirements.static_features:
        log.warning(
            "training_data.missing_static_attributes",
            station_id=str(station_id),
            missing=sorted(model.data_requirements.static_features),
        )
        return None

    past_targets_df = _observations_to_dataframe(observations, parameter)
    past_targets_df = resample_to_time_step(
        past_targets_df, time_step, aggregation_methods=None
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
) -> pl.DataFrame:
    if not future_features:
        return forcing_df.select("timestamp").clear()

    future_cols = sorted(future_features)
    future_forcing = resample_to_time_step(
        forcing_df.select(["timestamp", *future_cols]),
        time_step,
        aggregation_methods=None,
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
