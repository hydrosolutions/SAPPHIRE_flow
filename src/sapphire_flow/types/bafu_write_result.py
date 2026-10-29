from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_capture_batch import BafuCaptureBatchId
    from sapphire_flow.types.observation import RawObservation


class BafuWriteStatus(Enum):
    COMMITTED = "committed"
    REPLAYED = "replayed"
    CURRENT = "current"
    SUPERSEDED = "superseded"
    NOT_COMMITTED = "not_committed"
    UNKNOWN = "unknown"


class BafuApplicability(Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuOutcomeCounts:
    inserted: int = 0
    restated: int = 0
    evidence_only: int = 0
    unchanged: int = 0
    quarantined: int = 0
    rejected: int = 0

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.values()):
            raise ValueError("outcome counts must be nonnegative")

    def values(self) -> tuple[int, ...]:
        return (
            self.inserted,
            self.restated,
            self.evidence_only,
            self.unchanged,
            self.quarantined,
            self.rejected,
        )

    @property
    def total(self) -> int:
        return sum(self.values())


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuWriteResult:
    batch_id: BafuCaptureBatchId
    status: BafuWriteStatus
    applicability: BafuApplicability
    counts: BafuOutcomeCounts | None = None
    result_digest: str | None = None
    event_ids: tuple[int, ...] = ()
    observations_written: int = 0
    canonical_observations: tuple[RawObservation, ...] = ()

    def __post_init__(self) -> None:
        if self.observations_written < 0 or self.observations_written > len(
            self.canonical_observations
        ):
            raise ValueError("written count must fit canonical observations")
        if (
            self.canonical_observations
            and self.applicability is not BafuApplicability.CURRENT
        ):
            raise ValueError("only current results expose canonical observations")
        if self.observations_written and self.status is not BafuWriteStatus.COMMITTED:
            raise ValueError("only acknowledged new commits report writes")
        has_receipt = self.status in (
            BafuWriteStatus.COMMITTED,
            BafuWriteStatus.REPLAYED,
        )
        has_evidence = has_receipt or self.status is BafuWriteStatus.CURRENT
        if has_receipt != (self.result_digest is not None):
            raise ValueError("only committed/replayed results carry a receipt digest")
        if (
            self.result_digest is not None
            and re.fullmatch(r"[0-9a-f]{64}", self.result_digest) is None
        ):
            raise ValueError("receipt digest must be lowercase SHA-256")
        if has_evidence != (self.counts is not None):
            raise ValueError("evidenced results require outcome counts")
        if self.counts is not None and self.counts.total != len(self.event_ids):
            raise ValueError("outcome counts must match evidence references")
        if any(event_id < 1 for event_id in self.event_ids):
            raise ValueError("event references must be positive")
        if not has_evidence and self.event_ids:
            raise ValueError("unevidenced result cannot carry event references")


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuJournalQuota:
    max_station_events: int
    max_station_receipts: int
    max_station_evidence_bytes: int
    max_batch_bytes: int

    def __post_init__(self) -> None:
        if (
            min(
                self.max_station_events,
                self.max_station_receipts,
                self.max_station_evidence_bytes,
                self.max_batch_bytes,
            )
            < 1
        ):
            raise ValueError("journal quotas must be positive")


class BafuCapacityError(RuntimeError):
    pass


class BafuCommitAcknowledgementError(RuntimeError):
    """A transaction factory could not establish whether commit was acknowledged."""
