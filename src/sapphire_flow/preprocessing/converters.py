from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.forcing_sources import ForcingSource
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

# Every reanalysis provenance tag (Plan 115b4 §6C): a row destined for
# ``weather_forecasts`` (a FORECAST product) must never carry one of these as
# its ``nwp_source`` — that would mean a reanalysis row landed in the
# forecast table, silently merging two distinct provenance streams (Plan 071
# §243). Centralized here so all three converters below share one guard.
_REANALYSIS_SOURCE_TAGS: frozenset[str] = frozenset(s.value for s in ForcingSource)


def _reject_reanalysis_tag(nwp_source: str) -> None:
    if nwp_source in _REANALYSIS_SOURCE_TAGS:
        raise ConfigurationError(
            f"nwp_source={nwp_source!r} is a reanalysis provenance tag; "
            "reanalysis rows must never be written into weather_forecasts "
            "(the forecast table) — WeatherForecastRecord.nwp_source must "
            "identify a FORECAST product, not a reanalysis source."
        )


def point_forecast_to_records(
    station_id: StationId,
    forecast: PointForecast,
    clock: Callable[[], UtcDatetime],
    id_gen: Callable[[], uuid.UUID],
) -> list[WeatherForecastRecord]:
    _reject_reanalysis_tag(forecast.nwp_source)
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
    _reject_reanalysis_tag(forecast.nwp_source)
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
    _reject_reanalysis_tag(forecast.nwp_source)
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
