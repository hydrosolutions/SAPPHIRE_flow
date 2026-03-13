from __future__ import annotations

from datetime import UTC, datetime

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc


class FakeClock:
    def __init__(self, fixed: UtcDatetime | None = None) -> None:
        self._time = fixed or ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))

    def __call__(self) -> UtcDatetime:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time = ensure_utc(
            datetime.fromtimestamp(self._time.timestamp() + seconds, tz=UTC)
        )
