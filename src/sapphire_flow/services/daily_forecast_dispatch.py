from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.types.daily_forecast import (
    DailyAttemptDecision,
    DailyAttemptOrdinal,
    DailyAttemptState,
)

if TYPE_CHECKING:
    from sapphire_flow.types.daily_forecast import (
        ScheduledDailyAttempt,
        ScheduledDailyForecast,
    )


def next_daily_attempt(
    context: ScheduledDailyForecast,
    previous: ScheduledDailyAttempt | None,
) -> DailyAttemptDecision:
    """Eligibility only; the store must serialize the daily key before calling."""
    if previous is None:
        return DailyAttemptDecision.INITIAL
    if previous.context.issue_time != context.issue_time:
        raise ValueError("attempt history must belong to the same logical day")
    if previous.state is DailyAttemptState.ACTIVE:
        return DailyAttemptDecision.OWNED
    if previous.state is DailyAttemptState.COMPLETE:
        return DailyAttemptDecision.CLOSED
    if previous.ordinal is DailyAttemptOrdinal.RETRY:
        return DailyAttemptDecision.EXHAUSTED
    if context.scheduled_tick <= previous.context.scheduled_tick:
        return DailyAttemptDecision.NOT_LATER
    return DailyAttemptDecision.RETRY
