from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.rating_reconciliation import (
    AuditRecordState,
    RatingEvidenceError,
    RatingHoldReason,
    RatingInputsUnavailable,
    RatingReconciliationOutcome,
)

if TYPE_CHECKING:
    from sapphire_flow.config.dhm import DhmConfig
    from sapphire_flow.config.provisional_rating import NativeRatingBinding
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge


class RatingReconciliationState(Enum):
    COMPLETE = "complete"
    HELD = "held"


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingReconciliationResult:
    station_id: StationId
    state: RatingReconciliationState
    accepted: int
    rejected: int
    failed_chunks: int
    reasons: tuple[str, ...]
    inserted: int = 0
    held_count: int = 0
    qc_unassessed: int = 0
    audit_record_state: AuditRecordState | None = None


class RatingDerivedWriter(Protocol):
    def reconcile_chunk(
        self,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> tuple[RatingDerivedDischarge, ...]: ...


@runtime_checkable
class RatingOutcomeWriter(Protocol):
    def reconcile_chunk_outcome(
        self,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> RatingReconciliationOutcome: ...


def reconcile_rating_window(
    writer: RatingDerivedWriter,
    binding: NativeRatingBinding,
    dhm: DhmConfig,
    *,
    start: UtcDatetime,
    end: UtcDatetime,
    at: UtcDatetime,
) -> RatingReconciliationResult:
    if start >= end or end > at or end - start > timedelta(days=366):
        raise ValueError("rating reconciliation requires past bounds up to 366 days")
    accepted = rejected = failed = inserted = held_count = unassessed = 0
    audit_states: list[AuditRecordState] = []
    reasons: set[str] = set()
    cursor = start
    while cursor < end:
        stop = ensure_utc(min(end, cursor + timedelta(hours=6)))
        try:
            if isinstance(writer, RatingOutcomeWriter):
                outcome = writer.reconcile_chunk_outcome(
                    binding, dhm, start=cursor, end=stop, at=at
                )
                accepted += outcome.accepted
                rejected += outcome.rejected
                inserted += outcome.inserted
                held_count += len(outcome.held)
                unassessed += outcome.qc_unassessed
                audit_states.append(outcome.audit_record_state)
                if (
                    outcome.held
                    or outcome.audit_record_state is AuditRecordState.FAILED
                ):
                    failed += 1
                    reasons.update(source.reason.value for source in outcome.held)
            else:
                records = writer.reconcile_chunk(
                    binding, dhm, start=cursor, end=stop, at=at
                )
                accepted += sum(row.discharge is not None for row in records)
                rejected += sum(row.rejection is not None for row in records)
        except Exception as exc:
            failed += 1
            if isinstance(exc, RatingInputsUnavailable):
                reasons.update(source.reason.value for source in exc.held)
            elif isinstance(exc, RatingEvidenceError):
                reasons.add(exc.reason.value)
            else:
                reasons.add(RatingHoldReason.STORAGE_ERROR.value)
        cursor = stop
    return RatingReconciliationResult(
        station_id=binding.station_id,
        state=RatingReconciliationState.HELD
        if failed
        else RatingReconciliationState.COMPLETE,
        accepted=accepted,
        rejected=rejected,
        failed_chunks=failed,
        reasons=tuple(sorted(reasons)),
        inserted=inserted,
        held_count=held_count,
        qc_unassessed=unassessed,
        audit_record_state=(
            AuditRecordState.FAILED
            if AuditRecordState.FAILED in audit_states
            else AuditRecordState.RECORDED
            if audit_states
            else None
        ),
    )
