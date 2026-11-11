from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

from sapphire_flow.types.enums import ForecastDataUse, ObservationSource, QcStatus

if TYPE_CHECKING:
    from datetime import time
    from zoneinfo import ZoneInfo

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationId, TenantId


class OverviewAvailability(Enum):
    AVAILABLE = "available"
    EXPIRED = "expired"
    INVALID_SERIES = "invalid_series"
    COMPACT_UNAVAILABLE = "compact_series_unavailable"


class OverviewFreshness(Enum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "freshness_unknown"


class QuartileMethod(Enum):
    STORED = "stored_quantiles"
    MEMBERS = "member_empirical_type7"


@dataclass(frozen=True, kw_only=True, slots=True)
class OverviewPoint:
    valid_time: UtcDatetime
    median: float
    p25: float | None
    p75: float | None


@dataclass(frozen=True, kw_only=True, slots=True)
class OverviewGap:
    start: UtcDatetime
    end: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class OverviewSeries:
    availability: OverviewAvailability
    points: tuple[OverviewPoint, ...] = ()
    peak: OverviewPoint | None = None
    support_start: UtcDatetime | None = None
    support_end: UtcDatetime | None = None
    gaps: tuple[OverviewGap, ...] = ()
    original_point_count: int = 0
    quartile_method: QuartileMethod | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class OverviewFreshnessPolicy:
    tenant_id: TenantId
    network: str
    model_id: ModelId
    parameter: str
    data_use: ForecastDataUse
    # Product day and delivery deadline are local to `timezone`; the persisted
    # issue time may be declared in another zone, e.g. 00:00 UTC for a Swiss product.
    timezone: ZoneInfo
    issue_timezone: ZoneInfo
    issue_time: time
    delivery_deadline: time

    def __post_init__(self) -> None:
        if not self.network or not self.parameter or not self.model_id:
            raise ValueError("freshness policy requires output identity")
        if (
            self.issue_time.tzinfo is not None
            or self.delivery_deadline.tzinfo is not None
        ):
            raise ValueError("policy clock times must be local wall times")
        if self.data_use is ForecastDataUse.EXPIRED_RATING_TEST:
            raise ValueError("overview does not admit expired rating tests")


@dataclass(frozen=True, kw_only=True, slots=True)
class OverviewFreshnessResult:
    freshness: OverviewFreshness
    expected_issue: UtcDatetime | None = None
    deadline: UtcDatetime | None = None


class ProtectedObservationVisibility(Enum):
    EXCLUDE = "exclude"
    DETAIL_AUTHORIZED = "detail_authorized"


class ObservationAvailability(Enum):
    AVAILABLE = "available"
    NO_ELIGIBLE = "no_eligible_reading"
    AMBIGUOUS_SOURCE = "ambiguous_source"


@dataclass(frozen=True, kw_only=True, slots=True)
class OverviewObservation:
    timestamp: UtcDatetime
    value: float
    source: ObservationSource
    qc_status: QcStatus


@dataclass(frozen=True, kw_only=True, slots=True)
class OverviewObservationSlot:
    end: UtcDatetime
    availability: ObservationAvailability
    sample: OverviewObservation | None


@dataclass(frozen=True, kw_only=True, slots=True)
class OverviewObservations:
    station_id: StationId
    parameter: Literal["water_level", "discharge"]
    latest: OverviewObservationSlot
    latest_failed_at: UtcDatetime | None
    hourly: tuple[OverviewObservationSlot, ...]
