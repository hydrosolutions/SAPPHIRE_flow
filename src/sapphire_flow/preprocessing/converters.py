from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.weather import WeatherForecastRecord

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.weather import (
        BasinAverageForecast,
        ElevationBandForecast,
        PointForecast,
    )


def point_forecast_to_records(
    station_id: StationId,
    forecast: PointForecast,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], uuid.UUID],
) -> list[WeatherForecastRecord]:
    now = clock()
    records: list[WeatherForecastRecord] = []
    for row in forecast.values.iter_rows(named=True):
        records.append(
            WeatherForecastRecord(
                id=id_gen(),
                station_id=station_id,
                nwp_source=forecast.nwp_source,
                cycle_time=forecast.cycle_time,
                valid_time=row["valid_time"],
                parameter=row["parameter"],
                spatial_type=SpatialRepresentation.POINT,
                band_id=None,
                member_id=row["member_id"],
                value=row["value"],
                is_gap=False,
                gap_status=None,
                created_at=now,
            )
        )
    return records


def elevation_band_to_records(
    station_id: StationId,
    forecast: ElevationBandForecast,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], uuid.UUID],
) -> list[WeatherForecastRecord]:
    now = clock()
    records: list[WeatherForecastRecord] = []
    for row in forecast.values.iter_rows(named=True):
        records.append(
            WeatherForecastRecord(
                id=id_gen(),
                station_id=station_id,
                nwp_source=forecast.nwp_source,
                cycle_time=forecast.cycle_time,
                valid_time=row["valid_time"],
                parameter=row["parameter"],
                spatial_type=SpatialRepresentation.ELEVATION_BAND,
                band_id=row["band_id"],
                member_id=row["member_id"],
                value=row["value"],
                is_gap=False,
                gap_status=None,
                created_at=now,
            )
        )
    return records


def basin_avg_to_records(
    station_id: StationId,
    forecast: BasinAverageForecast,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], uuid.UUID],
) -> list[WeatherForecastRecord]:
    now = clock()
    records: list[WeatherForecastRecord] = []
    for row in forecast.values.iter_rows(named=True):
        records.append(
            WeatherForecastRecord(
                id=id_gen(),
                station_id=station_id,
                nwp_source=forecast.nwp_source,
                cycle_time=forecast.cycle_time,
                valid_time=row["valid_time"],
                parameter=row["parameter"],
                spatial_type=SpatialRepresentation.BASIN_AVERAGE,
                band_id=None,
                member_id=row["member_id"],
                value=row["value"],
                is_gap=False,
                gap_status=None,
                created_at=now,
            )
        )
    return records
