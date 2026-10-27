from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sapphire_flow.types.daily_forecast import (
    DailyAttemptState,
    OneShotDailyForecast,
)
from sapphire_flow.types.daily_forecast_claim import DailyClaim
from sapphire_flow.types.daily_forecast_outcome import finalized_outcome_state
from sapphire_flow.types.enums import ForecastDataUse

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.daily_forecast_claim import (
        DailyFailureReason,
        FrozenDailyAssignment,
    )
    from sapphire_flow.types.daily_forecast_outcome import DailyMemberOutcome
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId, StationGroupId, TenantId


@dataclass(frozen=True, kw_only=True, slots=True)
class OneShotDailyKey:
    tenant_id: TenantId
    deployment: str
    group_id: StationGroupId
    model_id: ModelId
    context: OneShotDailyForecast
    data_use: ForecastDataUse = ForecastDataUse.STANDARD

    def __post_init__(self) -> None:
        if not isinstance(self.context, OneShotDailyForecast):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("one-shot requires an explicit manual/replay context")
        if not self.deployment.strip():
            raise ValueError("daily deployment must not be empty")
        if self.data_use is not ForecastDataUse.STANDARD:
            raise ValueError("daily evaluation supports STANDARD data only")


@dataclass(frozen=True, kw_only=True, slots=True)
class OneShotDailyExecution:
    """Internal execution identity, not proof of output isolation or authorization."""

    id: UUID
    owner_id: UUID
    key: OneShotDailyKey
    frozen: FrozenDailyAssignment
    started_at: UtcDatetime

    def __post_init__(self) -> None:
        if not isinstance(self.key, OneShotDailyKey):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("one-shot execution requires a one-shot key")
        if self.started_at.utcoffset() != timedelta(0):
            raise ValueError("started_at must be aware UTC")


DailyExecution = DailyClaim | OneShotDailyExecution


@dataclass(frozen=True, kw_only=True, slots=True)
class OneShotDailySnapshot:
    execution: OneShotDailyExecution
    state: DailyAttemptState
    finished_at: UtcDatetime | None
    outcomes: tuple[DailyMemberOutcome, ...] | None
    failure_reason: DailyFailureReason | None

    def __post_init__(self) -> None:
        if self.state is DailyAttemptState.ACTIVE:
            if any(
                v is not None
                for v in (self.finished_at, self.outcomes, self.failure_reason)
            ):
                raise ValueError("active one-shot cannot contain finalized metadata")
            return
        if (
            self.finished_at is None
            or self.finished_at.utcoffset() != timedelta(0)
            or self.finished_at < self.execution.started_at
        ):
            raise ValueError("one-shot requires a valid finish time")
        if self.state is DailyAttemptState.FAILED:
            if self.failure_reason is None or self.outcomes is not None:
                raise ValueError("failed one-shot requires only a failure reason")
            return
        if self.state not in (DailyAttemptState.COMPLETE, DailyAttemptState.INCOMPLETE):
            raise ValueError("one-shot recovery is unsupported")
        if self.outcomes is None or self.failure_reason is not None:
            raise ValueError("one-shot summary requires outcomes only")
        if finalized_outcome_state(self.outcomes) is not self.state:
            raise ValueError("one-shot summary state disagrees with outcomes")
        if {m.station_id: m.required_parameters for m in self.outcomes} != {
            m.station_id: m.required_parameters for m in self.execution.frozen.members
        }:
            raise ValueError("one-shot summary must match frozen roster")
