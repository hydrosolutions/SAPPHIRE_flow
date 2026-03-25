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
    from sapphire_flow.types.model import GroupTrainingData, TrainingData
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


def _observations_to_dataframe(observations: list) -> pl.DataFrame:
    rows = [{"timestamp": o.timestamp, "value": o.value} for o in observations]
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
) -> TrainingData | None:
    from sapphire_flow.types.model import TrainingData

    station = station_store.fetch_station(station_id)
    if station is None:
        log.warning("training_data.station_not_found", station_id=str(station_id))
        return None

    parameter = station.forecast_target or "discharge"
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

    required_features = list(model.required_features)
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
    missing_features = model.required_features - forcing_columns
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
            if model.required_static_attributes:
                missing_attrs = model.required_static_attributes - set(
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
    elif model.required_static_attributes:
        log.warning(
            "training_data.missing_static_attributes",
            station_id=str(station_id),
            missing=sorted(model.required_static_attributes),
        )
        return None

    obs_df = _observations_to_dataframe(observations)
    targets_df = obs_df.select(["timestamp", "value"])

    return TrainingData(
        forcing=forcing_df,
        observations=obs_df,
        targets=targets_df,
        static_attributes=static_attributes,
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

    station_data = {}
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
        if data is not None:
            station_data[station_id] = data

    if not station_data:
        log.warning(
            "training_data.group_no_data",
            group_id=str(group.id),
        )
        return None

    return GroupTrainingData(
        group_id=group.id,
        station_data=station_data,
        time_step=time_step,
        val_start=None,
    )
