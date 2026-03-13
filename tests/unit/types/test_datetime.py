from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sapphire_flow.types.datetime import ensure_utc


class TestEnsureUtc:
    def test_utc_passthrough(self) -> None:
        dt = datetime(2025, 1, 1, tzinfo=UTC)
        result = ensure_utc(dt)
        assert result == dt
        assert result.tzinfo == UTC

    def test_aware_converts(self) -> None:
        tz_plus2 = timezone(timedelta(hours=2))
        dt = datetime(2025, 1, 1, 14, 0, tzinfo=tz_plus2)
        result = ensure_utc(dt)
        assert result.hour == 12
        assert result.tzinfo == UTC

    def test_naive_rejected(self) -> None:
        with pytest.raises(ValueError, match="Naive datetime"):
            ensure_utc(datetime(2025, 1, 1))

    def test_return_type(self) -> None:
        dt = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
        result = ensure_utc(dt)
        # UtcDatetime is a NewType, so it's still a datetime at runtime
        assert isinstance(result, datetime)
