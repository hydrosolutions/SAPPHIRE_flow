from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sapphire_flow.store.operational_weather_codec import (
    decode_cache_entry,
    encode_cache_entry,
    make_past_snapshot,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import (
    WeatherAttemptState,
    WeatherCacheAttempt,
    WeatherCacheEntry,
    WeatherCacheKey,
    WeatherLegOutcome,
    WeatherLegState,
)
from tests.fakes.operational_weather import FINGERPRINT, ISSUE, make_operational_window


def pending_entry() -> WeatherCacheEntry:
    station_id = StationId(uuid4())
    return WeatherCacheEntry(
        attempt=WeatherCacheAttempt(
            key=WeatherCacheKey(station_id=station_id, binding_fingerprint=FINGERPRINT),
            generation=1,
            id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        ),
        state=WeatherAttemptState.PENDING,
        past_outcome=WeatherLegOutcome(state=WeatherLegState.PENDING),
        future_attempts=(),
        past_snapshot=make_past_snapshot(
            window=make_operational_window(station_id),
            retrieved_at=ISSUE,
            snapshot_id=uuid4(),
            max_bytes=2 * 1024 * 1024,
        ),
    )


class TestOperationalWeatherCodec:
    def test_strict_json_round_trip_keeps_domain_invariants(self) -> None:
        entry = pending_entry()
        raw = encode_cache_entry(entry, max_past_bytes=2 * 1024 * 1024)
        assert decode_cache_entry(raw, max_past_bytes=2 * 1024 * 1024) == entry

    def test_corrupted_snapshot_hash_is_not_restored(self) -> None:
        entry = pending_entry()
        assert entry.past_snapshot is not None
        bad = replace(
            entry, past_snapshot=replace(entry.past_snapshot, payload_sha256="0" * 64)
        )
        with pytest.raises(ValueError, match="hash mismatch"):
            encode_cache_entry(bad, max_past_bytes=2 * 1024 * 1024)

    def test_snapshot_limit_does_not_truncate(self) -> None:
        with pytest.raises(ValueError, match="cache byte limit"):
            encode_cache_entry(pending_entry(), max_past_bytes=100)

    def test_unknown_payload_fields_are_rejected(self) -> None:
        raw = encode_cache_entry(pending_entry(), max_past_bytes=2 * 1024 * 1024)
        raw["unexpected"] = "not provenance"
        with pytest.raises(ValueError, match="version or fields"):
            decode_cache_entry(raw, max_past_bytes=2 * 1024 * 1024)

    def test_unknown_nested_fields_are_rejected(self) -> None:
        raw = encode_cache_entry(pending_entry(), max_past_bytes=2 * 1024 * 1024)
        raw["entry"]["past_outcome"]["unexpected"] = 1
        with pytest.raises(ValidationError, match="Unexpected keyword"):
            decode_cache_entry(raw, max_past_bytes=2 * 1024 * 1024)


def test_valid_window_exact_byte_boundary_uses_typed_size_refusal() -> None:
    from sapphire_flow.store.operational_weather_codec import (
        WeatherSnapshotSizeError,
        window_bytes,
    )

    window = make_operational_window(StationId(uuid4()))
    payload = window_bytes(window, max_bytes=2 * 1024 * 1024)
    assert window_bytes(window, max_bytes=len(payload)) == payload
    with pytest.raises(WeatherSnapshotSizeError, match="cache byte limit"):
        window_bytes(window, max_bytes=len(payload) - 1)
