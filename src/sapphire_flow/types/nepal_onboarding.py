from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc

if TYPE_CHECKING:
    from sapphire_flow.types.ids import StationId

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
