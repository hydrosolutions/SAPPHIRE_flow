from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import ForcingType
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId, TenantId

MAX_REVIEW_HEADERS = 100_000
MAX_REVIEW_VALUES = 1_000_000
MAX_REVIEW_SOURCES = 100
MAX_REVIEW_BYTES = 2 * 1024 * 1024


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
