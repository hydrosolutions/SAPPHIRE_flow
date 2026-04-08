from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.types.enums import QcStatus

if TYPE_CHECKING:
    from datetime import timedelta

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


def _raw_forcing_to_dataframe(
    raw_records: list,
    station_id: StationId,
    parameters: list[str],
) -> pl.DataFrame | None:
    rows = [
        {"timestamp": r.valid_time, r.parameter: r.value}
        for r in raw_records
        if str(r.station_id) == str(station_id) and r.parameter in parameters
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

    weather_sources = station_store.fetch_weather_sources(station_id)
    if not weather_sources:
        log.warning("training_data.no_weather_sources", station_id=str(station_id))
        return None

    required_features = list(model.data_requirements.past_dynamic_features)
    raw_forcing = forcing_source.fetch_reanalysis(
        station_configs=weather_sources,
        start=period_start,
        end=period_end,
        parameters=required_features,
    )

    forcing_df = _raw_forcing_to_dataframe(raw_forcing, station_id, required_features)
    if forcing_df is None:
        log.warning("training_data.no_forcing", station_id=str(station_id))
        return None

    forcing_columns = set(forcing_df.columns) - {"timestamp"}
    missing_features = model.data_requirements.past_dynamic_features - forcing_columns
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
    # For training data, all forcing is historical — future_dynamic is empty
    # (same schema as past_dynamic but zero rows).
    future_dynamic_df = forcing_df.clear()

    return StationTrainingData(
        past_targets=past_targets_df,
        past_dynamic=forcing_df,
        future_dynamic=future_dynamic_df,
        static=static_attributes,
        time_step=time_step,
        val_start=None,
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
