from __future__ import annotations

import hashlib
import json
from typing import cast
from uuid import UUID

from pydantic import TypeAdapter

from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import (
    OperationalWeatherWindow,
    WeatherCacheCompletion,
    WeatherCacheEntry,
    WeatherPastSnapshot,
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


def window_bytes(window: OperationalWeatherWindow, *, max_bytes: int) -> bytes:
    payload = _canonical(_WINDOW.dump_python(window, mode="json"))
    if len(payload) > max_bytes:
        raise WeatherSnapshotSizeError(
            "operational past snapshot exceeds cache byte limit"
        )
    return payload


def make_past_snapshot(
    *,
    window: OperationalWeatherWindow,
    retrieved_at: UtcDatetime,
    snapshot_id: UUID,
    max_bytes: int,
) -> WeatherPastSnapshot:
    payload = window_bytes(window, max_bytes=max_bytes)
    return WeatherPastSnapshot(
        id=snapshot_id,
        retrieved_at=retrieved_at,
        window=window,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def verify_past_snapshot(snapshot: WeatherPastSnapshot, *, max_bytes: int) -> None:
    actual = hashlib.sha256(
        window_bytes(snapshot.window, max_bytes=max_bytes)
    ).hexdigest()
    if actual != snapshot.payload_sha256:
        raise ValueError("operational past snapshot hash mismatch")


def encode_cache_entry(
    entry: WeatherCacheEntry, *, max_past_bytes: int
) -> dict[str, object]:
    if entry.past_snapshot is not None:
        verify_past_snapshot(entry.past_snapshot, max_bytes=max_past_bytes)
    return {"version": 1, "entry": _ENTRY.dump_python(entry, mode="json")}


def decode_cache_entry(raw: object, *, max_past_bytes: int) -> WeatherCacheEntry:
    if not isinstance(raw, dict):
        raise ValueError("operational cache payload must be an object")
    payload = cast("dict[str, object]", raw)
    if (
        set(payload) != {"version", "entry"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
    ):
        raise ValueError("unsupported operational cache payload version or fields")
    entry = _ENTRY.validate_json(
        _canonical(payload["entry"]), strict=True, extra="forbid"
    )
    if entry.past_snapshot is not None:
        verify_past_snapshot(entry.past_snapshot, max_bytes=max_past_bytes)
    return entry


def completion_digest(
    completion: WeatherCacheCompletion, *, max_past_bytes: int
) -> str:
    if completion.past_snapshot is not None:
        verify_past_snapshot(completion.past_snapshot, max_bytes=max_past_bytes)
    return hashlib.sha256(
        _canonical(_COMPLETION.dump_python(completion, mode="json"))
    ).hexdigest()
