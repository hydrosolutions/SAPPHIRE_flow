from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_forecast_outcome import finalized_outcome_state

if TYPE_CHECKING:
    from sapphire_flow.types.daily_forecast_claim import DailyClaim, DailyFailureReason
    from sapphire_flow.types.daily_forecast_outcome import DailyMemberOutcome
    from sapphire_flow.types.daily_forecast_recovery import (
        ConfirmedDailyTermination,
        DailyRecoveryStatus,
    )
    from sapphire_flow.types.datetime import UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyAttemptSnapshot:
    claim: DailyClaim
    state: DailyAttemptState
    finished_at: UtcDatetime | None
    outcomes: tuple[DailyMemberOutcome, ...] | None
    failure_reason: DailyFailureReason | None
    recovery: ConfirmedDailyTermination | None

    def __post_init__(self) -> None:
        if self.state is DailyAttemptState.ACTIVE:
            if any(
                value is not None
                for value in (
                    self.finished_at,
                    self.outcomes,
                    self.failure_reason,
                    self.recovery,
                )
            ):
                raise ValueError(
                    "active daily attempt cannot contain finalized metadata"
                )
            return
        if (
            self.finished_at is None
            or self.finished_at.utcoffset() != timedelta(0)
            or self.finished_at < self.claim.started_at
        ):
            raise ValueError("finalized daily attempt requires a valid finish time")
        if self.state in (DailyAttemptState.COMPLETE, DailyAttemptState.INCOMPLETE):
            if (
                self.outcomes is None
                or self.failure_reason is not None
                or self.recovery is not None
            ):
                raise ValueError("daily summary must contain outcomes only")
            if finalized_outcome_state(self.outcomes) is not self.state:
                raise ValueError("daily summary state disagrees with its outcomes")
            roster = {
                m.station_id: m.required_parameters for m in self.claim.frozen.members
            }
            if {m.station_id: m.required_parameters for m in self.outcomes} != roster:
                raise ValueError("daily summary does not cover its frozen roster")
            references = (
                ref for outcome in self.outcomes for ref in outcome.references
            )
            if any(
                ref.key.model_id != self.claim.key.model_id
                or ref.key.issued_at != self.claim.key.context.issue_time
                for ref in references
            ):
                raise ValueError("daily summary reference has wrong scoped identity")
        elif self.outcomes is not None:
            raise ValueError("failed or abandoned daily attempt cannot claim outcomes")
        elif self.state is DailyAttemptState.FAILED:
            if self.failure_reason is None or self.recovery is not None:
                raise ValueError("failed daily attempt requires a failure reason only")
        elif self.state is DailyAttemptState.ABANDONED:
            if self.failure_reason is not None or self.recovery is None:
                raise ValueError(
                    "abandoned daily attempt requires recovery confirmation"
                )
            if (
                self.recovery.attempt_id != self.claim.id
                or self.recovery.owner_id != self.claim.owner_id
            ):
                raise ValueError("daily recovery confirmation has wrong owner")


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyDaySnapshot:
    latest: DailyAttemptSnapshot | None
    authoritative: DailyAttemptSnapshot | None
    recovery_status: DailyRecoveryStatus
