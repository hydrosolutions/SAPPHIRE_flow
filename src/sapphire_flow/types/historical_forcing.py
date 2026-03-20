from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import SpatialRepresentation

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import HistoricalForcingId, StationId


def _validate_band_id(spatial_type: SpatialRepresentation, band_id: int | None) -> None:
    if spatial_type == SpatialRepresentation.ELEVATION_BAND and band_id is None:
        raise ValueError("band_id is required when spatial_type is ELEVATION_BAND")
    if spatial_type != SpatialRepresentation.ELEVATION_BAND and band_id is not None:
        raise ValueError("band_id must be None when spatial_type is not ELEVATION_BAND")


@dataclass(frozen=True, kw_only=True, slots=True)
class RawHistoricalForcing:
    station_id: StationId
    source: str
    version: str
    valid_time: UtcDatetime
    parameter: str
    spatial_type: SpatialRepresentation
    band_id: int | None
    member_id: int | None
    value: float

    def __post_init__(self) -> None:
        _validate_band_id(self.spatial_type, self.band_id)


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoricalForcingRecord:
    id: HistoricalForcingId
    station_id: StationId
    source: str
    version: str
    valid_time: UtcDatetime
    parameter: str
    spatial_type: SpatialRepresentation
    band_id: int | None
    member_id: int | None
    value: float
    created_at: UtcDatetime

    def __post_init__(self) -> None:
        _validate_band_id(self.spatial_type, self.band_id)
