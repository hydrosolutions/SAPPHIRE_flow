from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from sapphire_flow.types.daily_forecast import (
    OneShotDailyForecast,
    OneShotForecastTrigger,
    ScheduledDailyForecast,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc


class TestScheduledDailyForecast:
    @pytest.mark.parametrize("hour", [0, 6, 12, 18])
    def test_regular_ticks_share_midnight_issue(self, hour: int) -> None:
        tick = ensure_utc(datetime(2026, 10, 26, hour, tzinfo=UTC))
        context = ScheduledDailyForecast(scheduled_tick=tick)
        assert context.issue_time == datetime(2026, 10, 26, tzinfo=UTC)

    def test_delayed_run_keeps_scheduled_day(self) -> None:
        context = ScheduledDailyForecast(
            scheduled_tick=ensure_utc(datetime(2026, 10, 26, 18, tzinfo=UTC))
        )
        # There is deliberately no execution-clock input to daily identity.
        assert context.issue_time.date() == date(2026, 10, 26)
        assert context.scheduled_tick.hour == 18

    def test_issue_day_is_utc_not_local_calendar_date(self) -> None:
        local_tick = datetime(
            2026, 10, 27, 1, tzinfo=timezone(timedelta(hours=5, minutes=45))
        )
        context = ScheduledDailyForecast(scheduled_tick=ensure_utc(local_tick))
        assert context.issue_time == datetime(2026, 10, 26, tzinfo=UTC)

    def test_rejects_naive_tick_even_if_type_cast(self) -> None:
        with pytest.raises(ValueError, match="UTC"):
            ScheduledDailyForecast(scheduled_tick=UtcDatetime(datetime(2026, 10, 26)))

    def test_rejects_non_utc_tick_at_domain_boundary(self) -> None:
        with pytest.raises(ValueError, match="UTC"):
            ScheduledDailyForecast(
                scheduled_tick=UtcDatetime(
                    datetime(2026, 10, 26, tzinfo=timezone(timedelta(hours=1)))
                )
            )


class TestOneShotDailyForecast:
    @pytest.mark.parametrize("trigger", list(OneShotForecastTrigger))
    def test_explicit_target_date_defines_issue(
        self, trigger: OneShotForecastTrigger
    ) -> None:
        context = OneShotDailyForecast(trigger=trigger, target_date=date(2026, 10, 26))
        assert context.issue_time == datetime(2026, 10, 26, tzinfo=UTC)
        assert not isinstance(context, ScheduledDailyForecast)

    def test_missing_target_date_is_not_replaced_by_wall_clock(self) -> None:
        with pytest.raises(TypeError, match="target_date"):
            OneShotDailyForecast(trigger=OneShotForecastTrigger.MANUAL)  # type: ignore[call-arg]

    def test_datetime_is_not_accepted_as_date(self) -> None:
        with pytest.raises(ValueError, match="calendar date"):
            OneShotDailyForecast(
                trigger=OneShotForecastTrigger.REPLAY,
                target_date=datetime(2026, 10, 26, 18, tzinfo=UTC),
            )


@pytest.mark.parametrize("value", [None, "2026-10-26", 42])
def test_one_shot_rejects_non_date(value: object) -> None:
    with pytest.raises(ValueError, match="calendar date"):
        OneShotDailyForecast(trigger=OneShotForecastTrigger.MANUAL, target_date=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, "manual", "scheduled"])
def test_one_shot_rejects_untyped_trigger(value: object) -> None:
    with pytest.raises(ValueError, match="manual/replay trigger"):
        OneShotDailyForecast(trigger=value, target_date=date(2026, 10, 26))  # type: ignore[arg-type]
