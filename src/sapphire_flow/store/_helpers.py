from __future__ import annotations

from typing import Any

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc


def utc_from_row(value: Any) -> UtcDatetime:
    return ensure_utc(value)


def utc_or_none(value: Any) -> UtcDatetime | None:
    return ensure_utc(value) if value is not None else None
