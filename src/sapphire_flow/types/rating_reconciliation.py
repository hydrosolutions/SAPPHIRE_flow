from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import InsufficientDataError

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ObservationId
    from sapphire_flow.types.native_capture import Sha256Digest
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge


class RatingHoldReason(Enum):
    MISSING_CAPTURE = "missing_capture"
    SOURCE_IDENTITY_MISMATCH = "source_identity_mismatch"
    REFERENCE_UNAVAILABLE = "reference_unavailable"
    UNRECONCILED = "unreconciled"
    CHUNK_LIMIT = "chunk_limit"
    STORAGE_ERROR = "storage_error"


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingHeldSource:
    observation_id: ObservationId | None
    source_version_sha256: Sha256Digest | None
    reason: RatingHoldReason
    timestamp: UtcDatetime | None = None
    source_snapshot_sha256: Sha256Digest | None = None

    def __post_init__(self) -> None:
        if self.reason is RatingHoldReason.UNRECONCILED and (
            self.observation_id is None
            or self.timestamp is None
            or self.source_snapshot_sha256 is None
        ):
            raise ValueError("unreconciled source requires immutable omission identity")


class RatingInputsUnavailable(InsufficientDataError):  # noqa: N818 - public contract name
    def __init__(self, held: tuple[RatingHeldSource, ...]) -> None:
        self.held = held
        super().__init__(
            "rating inputs unavailable: "
            + ",".join(sorted({source.reason.value for source in held}))
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingWindowRead:
    start: UtcDatetime
    end: UtcDatetime
    records: tuple[RatingDerivedDischarge, ...]
    held: tuple[RatingHeldSource, ...]
    source_count: int

    def __post_init__(self) -> None:
        if self.start >= self.end or self.source_count < 0:
            raise ValueError("invalid rating window bounds or source count")
        if self.source_count != len(self.records) + len(self.held):
            raise ValueError("rating window must account for every source")


class AuditRecordState(Enum):
    RECORDED = "recorded"
    FAILED = "failed"


class RatingEvidenceError(ValueError):
    def __init__(self, reason: RatingHoldReason, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingReconciliationOutcome:
    start: UtcDatetime
    end: UtcDatetime
    accepted: int
    rejected: int
    inserted: int
    held: tuple[RatingHeldSource, ...]
    qc_unassessed: int
    audit_record_state: AuditRecordState = AuditRecordState.RECORDED

    def __post_init__(self) -> None:
        if (
            self.start >= self.end
            or min(self.accepted, self.rejected, self.inserted, self.qc_unassessed) < 0
        ):
            raise ValueError("invalid rating reconciliation outcome")
        if self.inserted > self.accepted + self.rejected:
            raise ValueError("inserted records exceed reconciled sources")
