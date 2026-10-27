from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import FlowRunState

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import PrincipalId


class DailyWriterState(Enum):
    ACTIVE = "active"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class DailyRecoveryStatus(Enum):
    UNTOUCHED = "untouched"
    OPERATOR_RECOVERED = "operator_recovered"


@dataclass(frozen=True, kw_only=True, slots=True)
class ConfirmedDailyTermination:
    attempt_id: UUID
    owner_id: UUID
    operator: PrincipalId
    confirmed_at: UtcDatetime
    run_state: FlowRunState
    writer_state: DailyWriterState

    def __post_init__(self) -> None:
        if not self.operator.strip() or len(self.operator) > 128:
            raise ValueError("daily recovery requires a bounded operator identity")
        if self.confirmed_at.utcoffset() != timedelta(0):
            raise ValueError("daily recovery confirmation must be aware UTC")
        if self.run_state not in (
            FlowRunState.COMPLETED,
            FlowRunState.FAILED,
            FlowRunState.CANCELLED,
            FlowRunState.CRASHED,
        ):
            raise ValueError("daily owner must be confirmed terminal")
        if self.writer_state is not DailyWriterState.STOPPED:
            raise ValueError("daily writers must be confirmed stopped")
