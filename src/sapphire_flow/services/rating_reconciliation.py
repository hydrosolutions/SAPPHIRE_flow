from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from sapphire_flow.types.datetime import ensure_utc

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
    accepted = rejected = failed = 0
    reasons: set[str] = set()
    cursor = start
    while cursor < end:
        stop = ensure_utc(min(end, cursor + timedelta(hours=6)))
        try:
            records = writer.reconcile_chunk(
                binding, dhm, start=cursor, end=stop, at=at
            )
            accepted += sum(row.discharge is not None for row in records)
            rejected += sum(row.rejection is not None for row in records)
        except Exception as exc:
            # Raw storage/QC already committed; a derived failure must not undo it.
            failed += 1
            reasons.add(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
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
    )
