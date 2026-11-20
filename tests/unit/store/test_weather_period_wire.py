from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from sapphire_flow.store.operational_weather_codec import (
    completion_digest,
    decode_cache_entry,
    encode_cache_entry,
    verify_past_snapshot,
)
from sapphire_flow.types.operational_weather import (
    WeatherAttemptState,
    WeatherLegOutcome,
    WeatherLegState,
)
from sapphire_flow.types.operational_weather_v2 import (
    WeatherCacheCompletionV2,
    WeatherCacheEntryV2,
)
from sapphire_flow.types.time_grid import TimeGrid
from tests.fakes.weather_periods import ISSUE, period_attempt, period_snapshot

LIMIT = 2 * 1024 * 1024


def completed_entry() -> WeatherCacheEntryV2:
    snapshot = period_snapshot()
    success = WeatherLegOutcome(state=WeatherLegState.SUCCESS)
    completion = WeatherCacheCompletionV2(
        finished_at=ISSUE,
        past_outcome=success,
        future_attempts=(),
        past_snapshot=snapshot,
    )
    return WeatherCacheEntryV2(
        attempt=period_attempt(),
        state=WeatherAttemptState.COMPLETE,
        past_outcome=success,
        future_attempts=(),
        past_snapshot=snapshot,
        finished_at=ISSUE,
        completion_sha256=completion_digest(completion, max_past_bytes=LIMIT),
    )


class TestPeriodWire:
    def test_round_trip_includes_explicit_integer_grid(self) -> None:
        entry = completed_entry()
        raw = encode_cache_entry(entry, max_past_bytes=LIMIT)
        assert raw["version"] == 2
        assert raw["entry"]["attempt"]["grid"] == {
            "step_seconds": 86400,
            "phase_offset_seconds": 64800,
        }
        assert decode_cache_entry(raw, max_past_bytes=LIMIT) == entry

    def test_v2_cannot_enter_v1_decoder(self) -> None:
        raw = encode_cache_entry(completed_entry(), max_past_bytes=LIMIT)
        raw["version"] = 1
        with pytest.raises(ValueError, match="extra|UTC midnight|periods"):
            decode_cache_entry(raw, max_past_bytes=LIMIT)

    @pytest.mark.parametrize(
        "grid",
        [
            {},
            {"step_seconds": 86400},
            {"step_seconds": True, "phase_offset_seconds": 64800},
            {"step_seconds": 86400, "phase_offset_seconds": 64801},
        ],
    )
    def test_missing_or_malformed_grid_is_rejected(
        self, grid: dict[str, object]
    ) -> None:
        raw = deepcopy(encode_cache_entry(completed_entry(), max_past_bytes=LIMIT))
        raw["entry"]["attempt"]["grid"] = grid
        with pytest.raises(ValueError, match="grid|phase"):
            decode_cache_entry(raw, max_past_bytes=LIMIT)

    def test_grid_change_cannot_reuse_snapshot_hash(self) -> None:
        snapshot = period_snapshot()
        with pytest.raises(ValueError, match="declared grid|tile|UTC midnight"):
            replace(
                snapshot.window,
                grid=TimeGrid(step=timedelta(days=1), phase=timedelta(0)),
            )
        with pytest.raises(ValueError, match="hash mismatch"):
            verify_past_snapshot(
                replace(snapshot, payload_sha256="0" * 64), max_bytes=LIMIT
            )


class TestRadiationPeriodWire:
    def test_new_samples_roundtrip_with_unchanged_period_layout(self) -> None:
        from sapphire_flow.store.operational_weather_codec import make_past_snapshot
        from sapphire_flow.types.recap_intervals import RADIATION_CONTRACT_REVISION

        entry = completed_entry()
        snapshot = entry.past_snapshot
        assert snapshot is not None
        periods = tuple(
            replace(
                period,
                samples=period.samples
                + tuple(
                    replace(
                        sample,
                        parameter="solar_net_radiation",
                        value=100.0,
                        contract_revision=RADIATION_CONTRACT_REVISION,
                    )
                    for sample in period.samples
                    if sample.parameter == "precipitation"
                ),
            )
            for period in snapshot.window.periods
        )
        snapshot = make_past_snapshot(
            window=replace(snapshot.window, periods=periods),
            retrieved_at=snapshot.retrieved_at,
            snapshot_id=snapshot.id,
            max_bytes=LIMIT,
        )
        completion = WeatherCacheCompletionV2(
            finished_at=ISSUE,
            past_outcome=entry.past_outcome,
            future_attempts=(),
            past_snapshot=snapshot,
        )
        entry = replace(
            entry,
            past_snapshot=snapshot,
            completion_sha256=completion_digest(completion, max_past_bytes=LIMIT),
        )
        raw = encode_cache_entry(entry, max_past_bytes=LIMIT)
        assert decode_cache_entry(raw, max_past_bytes=LIMIT) == entry
