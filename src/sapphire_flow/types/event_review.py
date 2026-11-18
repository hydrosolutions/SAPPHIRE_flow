from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType, QcStatus
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId, TenantId

MAX_REVIEW_HEADERS = 100_000
MAX_REVIEW_VALUES = 1_000_000
MAX_REVIEW_SOURCES = 100
MAX_REVIEW_BYTES = 2 * 1024 * 1024
MAX_REVIEW_POINTS = 8_784


class ReviewPolicy(StrEnum):
    ORDINARY = "ordinary"
    PUBLISHED_ONLY = "published_only"


class ReviewKind(StrEnum):
    OPERATIONAL = "operational"
    HINDCAST = "hindcast"


class ReviewCompleteness(StrEnum):
    UNKNOWN = "unknown"
    INCOMPLETE = "incomplete"


class ReviewAvailability(StrEnum):
    AVAILABLE = "available"
    ABSENT = "absent"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewOptionsQuery:
    station_id: StationId
    tenant_id: TenantId
    policy: ReviewPolicy


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewSource:
    kind: ReviewKind
    model_id: ModelId
    model_name: str
    artifact_id: ArtifactId | None
    artifact_sha256: str | None
    training_period_start: UtcDatetime | None
    training_period_end: UtcDatetime | None
    hindcast_run_id: UUID | None
    forcing_type: ForcingType | None
    time_step_seconds: int | None
    units: str
    lead_hours: tuple[int, ...]
    first_valid_time: UtcDatetime | None
    last_valid_time: UtcDatetime | None
    completeness: ReviewCompleteness
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.time_step_seconds is not None and self.time_step_seconds <= 0:
            raise ValueError("review source cadence must be positive")
        if tuple(sorted(set(self.lead_hours))) != self.lead_hours or any(
            lead < 0 for lead in self.lead_hours
        ):
            raise ValueError("review leads must be sorted unique nonnegative hours")
        if self.kind is ReviewKind.HINDCAST and (
            self.hindcast_run_id is None
            or self.artifact_id is None
            or self.forcing_type is None
            or self.time_step_seconds is None
        ):
            raise ValueError(
                "hindcast source requires run, artifact, forcing and cadence"
            )


class ReviewCatalogLimitError(ValueError):
    pass


class ReviewQueryTimeoutError(RuntimeError):
    pass


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewOptions:
    station_id: StationId
    sources: tuple[ReviewSource, ...]
    operational_availability: ReviewAvailability
    hindcast_availability: ReviewAvailability


class ReviewSourceMode(StrEnum):
    OPERATIONAL = "operational"
    HINDCAST = "hindcast"
    OPERATIONAL_WITH_HINDCAST = "operational_with_hindcast"


class ReviewPointState(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    QC_REJECTED = "qc_rejected"
    WITHHELD = "withheld"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewSeriesQuery:
    scope: ReviewOptionsQuery
    model_id: ModelId
    lead_hours: int
    start: UtcDatetime
    end: UtcDatetime
    requested_at: UtcDatetime
    source_mode: ReviewSourceMode
    hindcast_run_id: UUID | None = None
    hindcast_artifact_id: ArtifactId | None = None
    forcing_type: ForcingType | None = None
    time_step_seconds: int | None = None

    def __post_init__(self) -> None:
        if not self.model_id or not 0 <= self.lead_hours <= 24 * 366:
            raise ValueError("Invalid model or lead")
        if not self.start < self.end <= self.requested_at:
            raise ValueError("Expected start < end <= current time")
        if self.end - self.start > timedelta(days=366):
            raise ValueError("Review period exceeds 366 days")
        identity = (
            self.hindcast_run_id,
            self.hindcast_artifact_id,
            self.forcing_type,
            self.time_step_seconds,
        )
        if self.source_mode is ReviewSourceMode.OPERATIONAL:
            if any(value is not None for value in identity):
                raise ValueError("Operational mode accepts no hindcast identity")
        elif any(value is None for value in identity):
            raise ValueError("Hindcast mode requires the complete source identity")
        if self.time_step_seconds is not None and self.time_step_seconds <= 0:
            raise ValueError("Cadence must be positive")


ReviewErrorCode = Literal[
    "review_catalog_limit_exceeded",
    "review_query_timeout",
    "review_series_limit_exceeded",
    "review_source_unsupported",
    "review_source_not_found",
    "lead_not_offered",
    "invalid_review_query",
]


class ReviewSeriesError(ValueError):
    def __init__(self, code: ReviewErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ReviewErrorCode = code


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewSeriesRecord:
    forecast_id: UUID
    source: ReviewSource
    issued_at: UtcDatetime
    valid_time: UtcDatetime
    stored_at: UtcDatetime
    representation: EnsembleRepresentation
    qc_status: QcStatus
    values: tuple[tuple[float | None, float], ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewSeriesSnapshot:
    sources: tuple[ReviewSource, ...]
    records: tuple[ReviewSeriesRecord, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewPoint:
    valid_time: UtcDatetime
    issued_at: UtcDatetime
    forecast_id: UUID | None
    stored_at: UtcDatetime | None
    state: ReviewPointState
    reason: str | None
    qc_status: QcStatus | None
    median: float | None = None
    p25: float | None = None
    p75: float | None = None
    quartile_method: Literal["stored_quantiles", "member_empirical_type7"] | None = None
    band_reason: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewGap:
    after: UtcDatetime
    before: UtcDatetime
    reason: str = "no_recorded_point_between_timestamps"


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewSegment:
    source: ReviewSource
    points: tuple[ReviewPoint, ...]
    gaps: tuple[ReviewGap, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class ReviewSeries:
    query: ReviewSeriesQuery
    state: ReviewPointState
    reason: str | None
    segments: tuple[ReviewSegment, ...]
