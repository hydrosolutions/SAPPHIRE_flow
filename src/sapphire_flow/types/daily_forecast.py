from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc


@dataclass(frozen=True, kw_only=True, slots=True)
class ScheduledDailyForecast:
    scheduled_tick: UtcDatetime

    def __post_init__(self) -> None:
        if self.scheduled_tick.utcoffset() != timedelta(0):
            raise ValueError("scheduled_tick must be an aware UTC datetime")

    @property
    def issue_time(self) -> UtcDatetime:
        return ensure_utc(
            datetime.combine(self.scheduled_tick.date(), time.min, tzinfo=UTC)
        )


class OneShotForecastTrigger(Enum):
    MANUAL = "manual"
    REPLAY = "replay"


@dataclass(frozen=True, kw_only=True, slots=True)
class OneShotDailyForecast:
    trigger: OneShotForecastTrigger
    target_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.trigger, OneShotForecastTrigger):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("trigger must be a manual/replay trigger")
        if not isinstance(self.target_date, date) or isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            self.target_date, datetime
        ):
            raise ValueError("target_date must be a calendar date, not a datetime")

    @property
    def issue_time(self) -> UtcDatetime:
        return ensure_utc(datetime.combine(self.target_date, time.min, tzinfo=UTC))


class DailyAttemptOrdinal(Enum):
    INITIAL = 1
    RETRY = 2


class DailyAttemptState(Enum):
    ACTIVE = "active"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass(frozen=True, kw_only=True, slots=True)
class ScheduledDailyAttempt:
    context: ScheduledDailyForecast
    ordinal: DailyAttemptOrdinal
    state: DailyAttemptState


class DailyAttemptDecision(Enum):
    INITIAL = "initial"
    RETRY = "retry"
    OWNED = "owned"
    CLOSED = "closed"
    EXHAUSTED = "exhausted"
    NOT_LATER = "not_later"
