from datetime import UTC, datetime

import pytest

from sapphire_flow.services.daily_forecast_dispatch import next_daily_attempt
from sapphire_flow.types.daily_forecast import (
    DailyAttemptDecision,
    DailyAttemptOrdinal,
    DailyAttemptState,
    ScheduledDailyAttempt,
    ScheduledDailyForecast,
)
from sapphire_flow.types.datetime import ensure_utc


def _tick(day: int = 26, hour: int = 6) -> ScheduledDailyForecast:
    return ScheduledDailyForecast(
        scheduled_tick=ensure_utc(datetime(2026, 10, day, hour, tzinfo=UTC))
    )


class TestNextDailyAttempt:
    def test_first_tick_is_eligible(self) -> None:
        assert next_daily_attempt(_tick(), None) is DailyAttemptDecision.INITIAL

    @pytest.mark.parametrize("ordinal", list(DailyAttemptOrdinal))
    def test_unfinished_owner_blocks_even_a_later_tick(
        self, ordinal: DailyAttemptOrdinal
    ) -> None:
        previous = ScheduledDailyAttempt(
            context=_tick(hour=0), ordinal=ordinal, state=DailyAttemptState.ACTIVE
        )
        assert (
            next_daily_attempt(_tick(hour=18), previous) is DailyAttemptDecision.OWNED
        )

    @pytest.mark.parametrize("ordinal", list(DailyAttemptOrdinal))
    def test_closed_day_is_not_reopened(self, ordinal: DailyAttemptOrdinal) -> None:
        previous = ScheduledDailyAttempt(
            context=_tick(hour=0), ordinal=ordinal, state=DailyAttemptState.COMPLETE
        )
        assert next_daily_attempt(_tick(), previous) is DailyAttemptDecision.CLOSED

    @pytest.mark.parametrize(
        "state",
        [
            DailyAttemptState.INCOMPLETE,
            DailyAttemptState.FAILED,
            DailyAttemptState.ABANDONED,
        ],
    )
    def test_first_unsuccessful_attempt_permits_one_later_retry(
        self, state: DailyAttemptState
    ) -> None:
        previous = ScheduledDailyAttempt(
            context=_tick(hour=0), ordinal=DailyAttemptOrdinal.INITIAL, state=state
        )
        assert next_daily_attempt(_tick(), previous) is DailyAttemptDecision.RETRY

    @pytest.mark.parametrize(
        "state",
        [
            DailyAttemptState.INCOMPLETE,
            DailyAttemptState.FAILED,
            DailyAttemptState.ABANDONED,
        ],
    )
    def test_second_attempt_exhausts_budget(self, state: DailyAttemptState) -> None:
        previous = ScheduledDailyAttempt(
            context=_tick(), ordinal=DailyAttemptOrdinal.RETRY, state=state
        )
        assert (
            next_daily_attempt(_tick(hour=12), previous)
            is DailyAttemptDecision.EXHAUSTED
        )

    @pytest.mark.parametrize("hour", [0, 6])
    def test_same_or_earlier_tick_cannot_consume_retry(self, hour: int) -> None:
        previous = ScheduledDailyAttempt(
            context=_tick(),
            ordinal=DailyAttemptOrdinal.INITIAL,
            state=DailyAttemptState.FAILED,
        )
        assert (
            next_daily_attempt(_tick(hour=hour), previous)
            is DailyAttemptDecision.NOT_LATER
        )

    def test_previous_day_cannot_be_passed_as_this_days_history(self) -> None:
        previous = ScheduledDailyAttempt(
            context=_tick(day=25),
            ordinal=DailyAttemptOrdinal.INITIAL,
            state=DailyAttemptState.FAILED,
        )
        with pytest.raises(ValueError, match="same logical day"):
            next_daily_attempt(_tick(), previous)
