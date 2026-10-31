from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc

if TYPE_CHECKING:
    from sapphire_flow.types.ids import StationGroupId, StationId
    from sapphire_flow.types.weather import GatewayHruName

GAUGE_POLYGONS = (
    ("447", "g_447"),
    ("450", "g_450"),
    ("604.5", "g_604_5"),
    ("647", "g_647"),
    ("670", "g_670"),
    ("684", "g_684"),
)
GATEWAY_HRU = "nepal6_20260923"
GATEWAY_HISTORY_SOURCE = "recap_era5_land_reanalysis"
HISTORY_PARAMETERS = frozenset({"precipitation", "temperature"})


@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherStationPolygon:
    station_id: StationId
    polygon_name: str

    def __post_init__(self) -> None:
        if not self.polygon_name or self.polygon_name != self.polygon_name.strip():
            raise ValueError("polygon_name must be nonempty without outer whitespace")


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalWeatherScope:
    group_id: StationGroupId
    gateway_hru_name: GatewayHruName
    station_polygons: tuple[WeatherStationPolygon, ...]

    def __post_init__(self) -> None:
        if (
            not self.gateway_hru_name
            or self.gateway_hru_name != self.gateway_hru_name.strip()
        ):
            raise ValueError(
                "gateway_hru_name must be nonempty without outer whitespace"
            )
        if type(self.station_polygons) is not tuple or not self.station_polygons:
            raise ValueError("station_polygons must be a nonempty tuple")
        if len({p.station_id for p in self.station_polygons}) != len(
            self.station_polygons
        ):
            raise ValueError("station_polygons contains duplicate station IDs")
        if len({p.polygon_name for p in self.station_polygons}) != len(
            self.station_polygons
        ):
            raise ValueError("station_polygons contains duplicate polygon names")


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalWeatherAdmission:
    scope: OperationalWeatherScope
    original_gateway_hru_name: GatewayHruName

    def __post_init__(self) -> None:
        name = self.original_gateway_hru_name
        if not name or name != name.strip():
            raise ValueError(
                "original_gateway_hru_name must be nonempty without outer whitespace"
            )


class ReadinessStatus(Enum):
    READY = "ready_for_model_onboarding"
    HELD = "held"


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoryWindow:
    start: UtcDatetime
    end: UtcDatetime

    def __post_init__(self) -> None:
        ensure_utc(self.start)
        ensure_utc(self.end)
        if self.start >= self.end:
            raise ValueError("history start must precede end")

    def batches(self) -> tuple[HistoryWindow, ...]:
        windows: list[HistoryWindow] = []
        start = self.start
        while start < self.end:
            end = ensure_utc(min(start + timedelta(days=31), self.end))
            windows.append(HistoryWindow(start=start, end=end))
            start = end
        return tuple(windows)


@dataclass(frozen=True, kw_only=True, slots=True)
class TrainingWindow(HistoryWindow):
    time_step: timedelta
    minimum_samples: int

    def __post_init__(self) -> None:
        HistoryWindow.__post_init__(self)
        if self.time_step < timedelta(days=1):
            raise ValueError("daily discharge cannot support subdaily training")
        if self.minimum_samples < 1:
            raise ValueError("minimum_samples must be positive")


@dataclass(frozen=True, kw_only=True, slots=True)
class StationReadiness:
    station_id: StationId
    code: str
    status: ReadinessStatus
    reasons: tuple[str, ...]
    qc_counts: tuple[tuple[str, int], ...]
    qc_versions: tuple[str, ...]
    usable_observations: int
    complete_samples: int
    overlap_start: UtcDatetime | None
    overlap_end: UtcDatetime | None


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoryCoverage:
    station_id: StationId
    code: str
    parameter: str
    rows: int
    start: UtcDatetime | None
    end: UtcDatetime | None
