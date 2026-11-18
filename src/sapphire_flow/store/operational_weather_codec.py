from __future__ import annotations

import hashlib
import json
from typing import cast, overload
from uuid import UUID

from pydantic import TypeAdapter

from sapphire_flow.store.weather_period_wire import (
    COMPLETION_V2,
    ENTRY_V2,
    WINDOW_V2,
    dump_period_wire,
    load_period_wire,
)
from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import (
    OperationalWeatherWindow,
    WeatherCacheCompletion,
    WeatherCacheEntry,
    WeatherPastSnapshot,
)
from sapphire_flow.types.operational_weather_v2 import (
    OperationalWeatherWindowV2,
    WeatherCacheCompletionV2,
    WeatherCacheEntryV2,
    WeatherPastSnapshotV2,
)


class WeatherSnapshotSizeError(ValueError):
    pass


_NAMESPACE = {"UtcDatetime": UtcDatetime, "StationId": StationId, "UUID": UUID}
_WINDOW = TypeAdapter(OperationalWeatherWindow)
_ENTRY = TypeAdapter(WeatherCacheEntry)
_COMPLETION = TypeAdapter(WeatherCacheCompletion)
for _adapter in (_WINDOW, _ENTRY, _COMPLETION):
    _adapter.rebuild(_types_namespace=_NAMESPACE)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def window_bytes(
    window: OperationalWeatherWindow | OperationalWeatherWindowV2, *, max_bytes: int
) -> bytes:
    payload = _canonical(
        {"version": 2, "window": dump_period_wire(WINDOW_V2, window)}
        if isinstance(window, OperationalWeatherWindowV2)
        else _WINDOW.dump_python(window, mode="json")
    )
    if len(payload) > max_bytes:
        raise WeatherSnapshotSizeError(
            "operational past snapshot exceeds cache byte limit"
        )
    return payload


@overload
def make_past_snapshot(
    *,
    window: OperationalWeatherWindow,
    retrieved_at: UtcDatetime,
    snapshot_id: UUID,
    max_bytes: int,
) -> WeatherPastSnapshot: ...


@overload
def make_past_snapshot(
    *,
    window: OperationalWeatherWindowV2,
    retrieved_at: UtcDatetime,
    snapshot_id: UUID,
    max_bytes: int,
) -> WeatherPastSnapshotV2: ...


@overload
def make_past_snapshot(
    *,
    window: OperationalWeatherWindow | OperationalWeatherWindowV2,
    retrieved_at: UtcDatetime,
    snapshot_id: UUID,
    max_bytes: int,
) -> WeatherPastSnapshot | WeatherPastSnapshotV2: ...


def make_past_snapshot(
    *,
    window: OperationalWeatherWindow | OperationalWeatherWindowV2,
    retrieved_at: UtcDatetime,
    snapshot_id: UUID,
    max_bytes: int,
) -> WeatherPastSnapshot | WeatherPastSnapshotV2:
    payload = window_bytes(window, max_bytes=max_bytes)
    if isinstance(window, OperationalWeatherWindowV2):
        return WeatherPastSnapshotV2(
            id=snapshot_id,
            retrieved_at=retrieved_at,
            window=window,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
        )
    return WeatherPastSnapshot(
        id=snapshot_id,
        retrieved_at=retrieved_at,
        window=window,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def verify_past_snapshot(
    snapshot: WeatherPastSnapshot | WeatherPastSnapshotV2, *, max_bytes: int
) -> None:
    actual = hashlib.sha256(
        window_bytes(snapshot.window, max_bytes=max_bytes)
    ).hexdigest()
    if actual != snapshot.payload_sha256:
        raise ValueError("operational past snapshot hash mismatch")


def encode_cache_entry(
    entry: WeatherCacheEntry | WeatherCacheEntryV2, *, max_past_bytes: int
) -> dict[str, object]:
    if entry.past_snapshot is not None:
        verify_past_snapshot(entry.past_snapshot, max_bytes=max_past_bytes)
    if isinstance(entry, WeatherCacheEntryV2):
        return {"version": 2, "entry": dump_period_wire(ENTRY_V2, entry)}
    return {"version": 1, "entry": _ENTRY.dump_python(entry, mode="json")}


def decode_cache_entry(
    raw: object, *, max_past_bytes: int
) -> WeatherCacheEntry | WeatherCacheEntryV2:
    if not isinstance(raw, dict):
        raise ValueError("operational cache payload must be an object")
    payload = cast("dict[str, object]", raw)
    if (
        set(payload) != {"version", "entry"}
        or type(payload["version"]) is not int
        or payload["version"] not in (1, 2)
    ):
        raise ValueError("unsupported operational cache payload version or fields")
    entry = (
        load_period_wire(ENTRY_V2, payload["entry"])
        if payload["version"] == 2
        else _ENTRY.validate_json(
            _canonical(payload["entry"]), strict=True, extra="forbid"
        )
    )
    if entry.past_snapshot is not None:
        verify_past_snapshot(entry.past_snapshot, max_bytes=max_past_bytes)
    return entry


def completion_digest(
    completion: WeatherCacheCompletion | WeatherCacheCompletionV2,
    *,
    max_past_bytes: int,
) -> str:
    if completion.past_snapshot is not None:
        verify_past_snapshot(completion.past_snapshot, max_bytes=max_past_bytes)
    return hashlib.sha256(
        _canonical(
            {"version": 2, "completion": dump_period_wire(COMPLETION_V2, completion)}
            if isinstance(completion, WeatherCacheCompletionV2)
            else _COMPLETION.dump_python(
                completion,
                mode="json",
                exclude={"retained_future_receipts"}
                if not completion.retained_future_receipts
                else None,
            )
        )
    ).hexdigest()
