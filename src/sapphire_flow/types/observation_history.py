from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum, IntEnum
from typing import TYPE_CHECKING

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource, QcStatus

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId


class HistoryWindow(IntEnum):
    THREE_DAYS = 72
    SEVEN_DAYS = 168


class BlockClipping(Enum):
    FULL = "full"
    CLIPPED = "clipped"


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoryGrid:
    as_of: UtcDatetime
    window: HistoryWindow

    def __post_init__(self) -> None:
        if self.as_of.utcoffset() != timedelta(0):
            raise ValueError("history cutoff must be UTC")
        if type(self.window) is not HistoryWindow:
            raise ValueError("history window must be typed")

    @property
    def start(self) -> UtcDatetime:
        return ensure_utc(self.as_of - timedelta(hours=self.window.value))

    @staticmethod
    def floor(value: UtcDatetime) -> UtcDatetime:
        return ensure_utc(
            value.replace(hour=value.hour // 3 * 3, minute=0, second=0, microsecond=0)
        )

    @property
    def grid_start(self) -> UtcDatetime:
        return self.floor(self.start)

    @property
    def native_start(self) -> UtcDatetime:
        return self.floor(ensure_utc(self.as_of - timedelta(hours=1)))

    @property
    def block_count(self) -> int:
        return (self.native_start - self.grid_start) // timedelta(hours=3)

    @property
    def clipping(self) -> BlockClipping:
        return (
            BlockClipping.FULL
            if self.start == self.grid_start
            else BlockClipping.CLIPPED
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoryRead:
    station_ids: tuple[StationId, ...]
    start: UtcDatetime
    as_of: UtcDatetime
    excluded_delivery_id: str
    parameter: str = "discharge"
    source: ObservationSource = ObservationSource.MEASURED
    qc_status: QcStatus = QcStatus.QC_PASSED
    # Provisional safeguards; operational admission requires cost qualification.
    station_row_cap: int = 2500
    fleet_row_cap: int = 500000

    def __post_init__(self) -> None:
        if len(self.station_ids) > 200 or len(set(self.station_ids)) != len(
            self.station_ids
        ):
            raise ValueError("history station scope must be unique and bounded")
        if (
            any(t.utcoffset() != timedelta(0) for t in (self.start, self.as_of))
            or self.start > self.as_of
        ):
            raise ValueError("history requires an inclusive UTC window")
        if not self.excluded_delivery_id:
            raise ValueError("history requires protected delivery exclusion")
        if (
            self.parameter != "discharge"
            or self.source is not ObservationSource.MEASURED
            or self.qc_status is not QcStatus.QC_PASSED
        ):
            raise ValueError("history requires QC-passed measured discharge")
        if (
            not 1 <= self.station_row_cap <= 2500
            or not 1 <= self.fleet_row_cap <= 500000
        ):
            raise ValueError("history row bounds exceed provisional safeguards")


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoryReading:
    station_id: StationId
    timestamp: UtcDatetime
    value: float

    def __post_init__(self) -> None:
        if self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("history reading must use UTC")


@dataclass(frozen=True, kw_only=True, slots=True)
class BlockSummary:
    mean: float | None
    minimum: float | None
    maximum: float | None
    count: int

    def __post_init__(self) -> None:
        values = (self.mean, self.minimum, self.maximum)
        if type(self.count) is not int or self.count < 0:
            raise ValueError("history count must be a nonnegative integer")
        if self.count == 0:
            if any(v is not None for v in values):
                raise ValueError("empty history block must have null statistics")
        elif any(v is None or not math.isfinite(v) for v in values):
            raise ValueError("nonempty history block requires finite statistics")
        elif (
            self.minimum is None
            or self.mean is None
            or self.maximum is None
            or not self.minimum <= self.mean <= self.maximum
        ):
            raise ValueError("history mean must lie within observed extrema")
        elif self.count == 1 and not self.minimum == self.mean == self.maximum:
            raise ValueError("history singleton requires equal statistics")


@dataclass(frozen=True, kw_only=True, slots=True)
class StationHistory:
    station_id: StationId
    coarse: tuple[BlockSummary, ...]
    native: tuple[HistoryReading, ...]


class HistoryRowLimitError(ValueError):
    def __init__(
        self, *, bound: int, eligible_rows: int, station_id: StationId | None = None
    ) -> None:
        super().__init__("eligible observation history exceeds bound")
        self.bound = bound
        self.eligible_rows = eligible_rows
        self.station_id = station_id


class HistoryReadTimeoutError(RuntimeError):
    pass
