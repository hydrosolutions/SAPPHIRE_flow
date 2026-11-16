from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ForecastDataUse, QcStatus
from sapphire_flow.types.ids import StationId  # noqa: TC001

HistorySource = Literal["measured", "supplied", "rating_derived"]


class HistoryResolution(Enum):
    NATIVE = "native"
    HOURLY = "hourly"


class HistoryOutcome(Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    HELD = "held"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationHistoryRequest:
    station_id: StationId
    start: datetime
    end: datetime
    generated_at: datetime
    resolution: HistoryResolution
    purposes: tuple[ForecastDataUse, ...]

    def __post_init__(self) -> None:
        for value in (self.start, self.end, self.generated_at):
            if ensure_utc(value) != value or value.utcoffset() != timedelta(0):
                raise ValueError("history requires UTC instants")
        if not self.start < self.end <= self.generated_at:
            raise ValueError("history requires start < end <= generated_at")
        if self.end - self.start > timedelta(days=7):
            raise ValueError("history window exceeds seven days")
        if not self.purposes or len(set(self.purposes)) != len(self.purposes):
            raise ValueError("history purposes must be nonempty and unique")
        if any(
            p not in (ForecastDataUse.STANDARD, ForecastDataUse.PROVISIONAL_RATING)
            for p in self.purposes
        ):
            raise ValueError("unsupported history purpose")


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoryCurve:
    curve_id: str
    content_sha256: str
    version: str
    valid_from: datetime
    valid_to: datetime | None


@dataclass(frozen=True, kw_only=True, slots=True)
class HistorySample:
    timestamp: datetime
    source: HistorySource
    data_use: ForecastDataUse
    value: float | None
    outcome: HistoryOutcome
    reason: str | None
    source_qc_status: QcStatus
    source_assessment: Literal["assessed", "unassessed"]
    warnings: tuple[str, ...] = ()
    curve: HistoryCurve | None = None
    stored_at: datetime | None = None
    policy: str | None = None

    def __post_init__(self) -> None:
        if (self.value is not None) != (self.outcome is HistoryOutcome.ACCEPTED):
            raise ValueError("history value and outcome disagree")
        if self.value is not None and not math.isfinite(self.value):
            raise ValueError("history value must be finite")
        if self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("history sample requires UTC timestamp")
        if (self.source == "rating_derived") != (
            self.data_use is ForecastDataUse.PROVISIONAL_RATING
        ):
            raise ValueError("history source and purpose disagree")
        if (
            self.source == "rating_derived"
            and self.value is not None
            and (self.value < 0 or self.curve is None or self.stored_at is None)
        ):
            raise ValueError(
                "derived history requires nonnegative value and provenance"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoryCounts:
    source: int
    accepted: int
    rejected: int
    held: int
    rejected_source_qc: int


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoryPoint:
    timestamp: datetime
    end: datetime | None
    value: float | None
    minimum: float | None
    maximum: float | None
    sample_count: int
    outcome: HistoryOutcome | None
    stored_at: datetime | None
    counts: HistoryCounts
    completeness: Literal["complete", "partial"]
    interval: Literal["native", "complete_hour", "clipped_hour", "unfinished_hour"]
    source_qc_status: QcStatus | None
    source_assessment: Literal["assessed", "unassessed"] | None
    source_qc_counts: dict[str, int]
    source_assessment_counts: dict[str, int]
    warnings: tuple[str, ...]
    reasons: tuple[str, ...]
    curve_refs: tuple[str, ...]
    policies: tuple[str, ...]
    stored_at_min: datetime | None
    stored_at_max: datetime | None


@dataclass(frozen=True, kw_only=True, slots=True)
class HistorySeries:
    source: HistorySource
    data_use: ForecastDataUse
    availability: Literal["no_source", "no_eligible_result", "available"]
    completeness: Literal["complete", "partial"]
    latest_timestamp: datetime | None
    counts: HistoryCounts
    points: tuple[HistoryPoint, ...]
    coverage: Literal["unknown"] = "unknown"


@dataclass(frozen=True, kw_only=True, slots=True)
class StationHistoryResponse:
    station_id: str
    start: datetime
    end: datetime
    generated_at: datetime
    resolution: HistoryResolution
    aggregation: Literal["native", "sample_mean"]
    series: tuple[HistorySeries, ...]
    curves: dict[str, HistoryCurve]
    schema_version: Literal[1] = 1
    parameter: Literal["discharge"] = "discharge"
    unit: Literal["m³/s"] = "m³/s"
    derived_basis: Literal["stored_worker_result"] = "stored_worker_result"
