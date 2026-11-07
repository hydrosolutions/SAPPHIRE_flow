from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.store.operational_weather_cache import WeatherCacheConflictError
from sapphire_flow.store.operational_weather_codec import make_past_snapshot
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.operational_weather import (
    WeatherAttemptState,
    WeatherCacheCompletion,
    WeatherCacheKey,
    WeatherLegOutcome,
    WeatherLegState,
)
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeStationStore
from tests.fakes.operational_weather import (
    FINGERPRINT,
    ISSUE,
    FakeOperationalWeatherCache,
    make_operational_window,
)

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import OperationalWeatherCacheStore


class TestFakeOperationalWeatherCache:
    def test_protocol_terminal_retry_and_generation_conflict(self) -> None:
        station = make_station_config()
        stations = FakeStationStore()
        stations.store_station(station)
        store: OperationalWeatherCacheStore = FakeOperationalWeatherCache(stations)
        key = WeatherCacheKey(station_id=station.id, binding_fingerprint=FINGERPRINT)
        principal = WritePrincipal(id=None, tenant_id=station.tenant_id)
        attempt = store.begin(
            key=key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
        completion = WeatherCacheCompletion(
            finished_at=ISSUE,
            future_attempts=(),
            past_outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
            past_snapshot=make_past_snapshot(
                window=make_operational_window(station.id),
                retrieved_at=ISSUE,
                snapshot_id=uuid4(),
                max_bytes=2 * 1024 * 1024,
            ),
        )
        first = store.finish(
            attempt=attempt,
            principal=principal,
            completion=completion,
            earliest_cycle=ISSUE,
        )
        assert first.state is WeatherAttemptState.COMPLETE
        assert (
            store.finish(
                attempt=attempt,
                principal=principal,
                completion=completion,
                earliest_cycle=ISSUE,
            )
            == first
        )
        newer = store.begin(
            key=key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
        assert newer.generation == 2
        pending = store.fetch(key)
        assert pending is not None and pending.past_snapshot == first.past_snapshot
        with pytest.raises(WeatherCacheConflictError, match="superseded"):
            store.finish(
                attempt=attempt,
                principal=principal,
                completion=completion,
                earliest_cycle=ISSUE,
            )

    def test_late_completion_can_expire_receipts_after_nominal_issue(self) -> None:
        station = make_station_config()
        stations = FakeStationStore()
        stations.store_station(station)
        store = FakeOperationalWeatherCache(stations)
        key = WeatherCacheKey(station_id=station.id, binding_fingerprint=FINGERPRINT)
        principal = WritePrincipal(id=None, tenant_id=station.tenant_id)
        completed_at = ensure_utc(ISSUE + timedelta(days=3))
        attempt = store.begin(
            key=key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=completed_at,
        )
        completion = WeatherCacheCompletion(
            finished_at=completed_at,
            future_attempts=(),
            past_outcome=WeatherLegOutcome(state=WeatherLegState.UNUSED),
        )
        with pytest.raises(ValueError, match="expiry boundary follows completion"):
            store.finish(
                attempt=attempt,
                principal=principal,
                completion=completion,
                earliest_cycle=ensure_utc(completed_at + timedelta(seconds=1)),
            )
        entry = store.finish(
            attempt=attempt,
            principal=principal,
            completion=completion,
            earliest_cycle=ensure_utc(ISSUE + timedelta(days=1)),
        )
        assert entry.state is WeatherAttemptState.COMPLETE
        assert entry.future_receipts == ()
