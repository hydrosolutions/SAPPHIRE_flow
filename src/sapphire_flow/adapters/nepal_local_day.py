"""DHM daily-date boundary in Nepal's historical civil time."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc

_KATHMANDU = ZoneInfo("Asia/Kathmandu")


def nepal_day_start(day: date) -> UtcDatetime:
    candidate = datetime.combine(day, time.min, tzinfo=_KATHMANDU)
    utc = candidate.astimezone(UTC)
    resolved = utc.astimezone(_KATHMANDU)
    if resolved.date() != day:
        raise ValueError(f"Nepal local date {day} has no valid instant")
    return ensure_utc(utc)


def nepal_local_date(at: UtcDatetime) -> date:
    return at.astimezone(_KATHMANDU).date()
