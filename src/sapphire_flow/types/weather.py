from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NamedTuple

if TYPE_CHECKING:
    from uuid import UUID

    import polars as pl
    import xarray as xr

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import SpatialRepresentation
    from sapphire_flow.types.ids import StationId


class WeatherForecastRecord(NamedTuple):
    id: UUID
    station_id: StationId
    nwp_source: str
    cycle_time: UtcDatetime
    valid_time: UtcDatetime
    parameter: str
    spatial_type: SpatialRepresentation
    band_id: int | None
    member_id: int | None
    value: float
    is_gap: bool
    gap_status: Literal["recovered", "unrecoverable"] | None
    created_at: UtcDatetime


class PointForecast(NamedTuple):
    nwp_source: str
    cycle_time: UtcDatetime
    values: pl.DataFrame


class BasinAverageForecast(NamedTuple):
    nwp_source: str
    cycle_time: UtcDatetime
    values: pl.DataFrame


class ElevationBandForecast(NamedTuple):
    nwp_source: str
    cycle_time: UtcDatetime
    values: pl.DataFrame


class GriddedForecast(NamedTuple):
    nwp_source: str
    cycle_time: UtcDatetime
    values: xr.Dataset


WeatherForecastResult = PointForecast | BasinAverageForecast | ElevationBandForecast
