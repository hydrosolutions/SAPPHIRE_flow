from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from uuid import UUID

    import polars as pl
    import xarray as xr

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import SpatialRepresentation
    from sapphire_flow.types.ids import StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherForecastRecord:
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
    is_gap: bool = False  # v1 (Flow 11): always False until gap recovery implemented
    gap_status: Literal["recovered", "unrecoverable"] | None = None  # v1 (Flow 11)
    created_at: UtcDatetime

    def __post_init__(self) -> None:
        if self.is_gap and self.gap_status is None:
            raise ValueError("gap_status must be set when is_gap is True")


@dataclass(frozen=True, kw_only=True, slots=True)
class PointForecast:
    nwp_source: str
    cycle_time: UtcDatetime
    values: pl.DataFrame


@dataclass(frozen=True, kw_only=True, slots=True)
class BasinAverageForecast:
    nwp_source: str
    cycle_time: UtcDatetime
    values: pl.DataFrame


@dataclass(frozen=True, kw_only=True, slots=True)
class ElevationBandForecast:
    nwp_source: str
    cycle_time: UtcDatetime
    values: pl.DataFrame


@dataclass(frozen=True, kw_only=True, slots=True)
class GriddedForecast:
    nwp_source: str
    cycle_time: UtcDatetime
    values: xr.Dataset
    # True iff the adapter walked back >=1 cycle to resolve this grid.
    # Threaded into NwpCycleSource.FALLBACK provenance by the forecast cycle.
    fallback_used: bool = False


WeatherForecastResult = PointForecast | BasinAverageForecast | ElevationBandForecast
