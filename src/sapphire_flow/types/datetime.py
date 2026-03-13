from datetime import UTC, datetime
from typing import NewType

UtcDatetime = NewType("UtcDatetime", datetime)


def ensure_utc(dt: datetime) -> UtcDatetime:
    if dt.tzinfo is None:
        raise ValueError(f"Naive datetime not allowed: {dt!r}")
    return UtcDatetime(dt.astimezone(UTC))
