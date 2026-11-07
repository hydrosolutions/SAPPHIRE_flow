from dataclasses import replace
from datetime import timedelta
from itertools import count
from typing import Literal
from uuid import uuid4

import pytest

from sapphire_flow.flows.operational_weather_refresh import (
    OperationalWeatherRefresh,
    StationWeatherRefresh,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.operational_weather import (
    PastWeatherRequirement,
    WeatherAttemptState,
    WeatherCacheAttempt,
    WeatherCacheCompletion,
    WeatherCacheEntry,
    WeatherCacheKey,
    WeatherLegState,
)
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeStationStore
from tests.fakes.operational_weather import ISSUE, FakeOperationalWeatherCache
from tests.unit.adapters.test_recap_operational import make_binding
from tests.unit.flows.test_combined_operational_ingestion import (
    RecordingOperationalSource,
)


class TestOperationalWeatherRefresh:
    def test_pending_is_published_before_fetch_and_terminal_before_view(self) -> None:
        source = make_binding()
        station = make_station_config(station_id=source.station_id)
        stations = FakeStationStore()
        stations.store_station(station)
        cache = FakeOperationalWeatherCache(station_store=stations)
        key = WeatherCacheKey(station_id=station.id, binding_fingerprint="a" * 64)
        start = ensure_utc(ISSUE - timedelta(days=1))
        owner = OperationalWeatherRefresh(
            specs=(
                StationWeatherRefresh(
                    key=key,
                    source=source,
                    past_requirement=PastWeatherRequirement(
                        start=start,
                        end=ISSUE,
                        parameters=("precipitation", "temperature"),
                    ),
                    future_requests=(),
                ),
            ),
            cache=cache,
            principal=WritePrincipal(id=None, tenant_id=station.tenant_id),
            issue_time=ISSUE,
            clock=lambda: ISSUE,
            id_gen=uuid4,
            max_past_bytes=2 * 1024 * 1024,
        )
        owner.begin()
        pending = cache.fetch(key)
        assert (
            pending is not None
            and pending.past_outcome.state is WeatherLegState.PENDING
        )
        owner.fetch_past(
            adapter=RecordingOperationalSource(),
            station_store=stations,
            audit_log_store=None,
            start=start,
        )
        view = owner.finish(earliest_cycle=start)
        assert view[station.id].entry.past_outcome.state is WeatherLegState.SUCCESS
        assert cache.fetch(key) == view[station.id].entry


@pytest.mark.parametrize("failure", ["transient", "ack_lost", "persistent"])
def test_partial_finish_reuses_identity_and_independently_finalizes(
    failure: Literal["transient", "ack_lost", "persistent"],
) -> None:
    stations = FakeStationStore()
    sources = [replace(make_binding(), station_id=StationId(uuid4())) for _ in range(3)]
    configs = [make_station_config(station_id=source.station_id) for source in sources]
    for station in configs:
        stations.store_station(station)
    calls: list[tuple[WeatherCacheAttempt, WeatherCacheCompletion]] = []
    original_error = RuntimeError("second station finish failure")

    class FailingCache(FakeOperationalWeatherCache):
        def finish(
            self,
            *,
            attempt: WeatherCacheAttempt,
            principal: WritePrincipal,
            completion: WeatherCacheCompletion,
            earliest_cycle: UtcDatetime,
        ) -> WeatherCacheEntry:
            calls.append((attempt, completion))
            second_calls = sum(
                a.key.station_id == sources[1].station_id for a, _ in calls
            )
            if attempt.key.station_id == sources[1].station_id and (
                second_calls == 1 or failure == "persistent"
            ):
                if failure == "ack_lost":
                    super().finish(
                        attempt=attempt,
                        principal=principal,
                        completion=completion,
                        earliest_cycle=earliest_cycle,
                    )
                raise original_error
            return super().finish(
                attempt=attempt,
                principal=principal,
                completion=completion,
                earliest_cycle=earliest_cycle,
            )

    cache = FailingCache(station_store=stations)
    ticks = count()

    def advancing_clock() -> UtcDatetime:
        return ensure_utc(ISSUE + timedelta(seconds=next(ticks)))

    keys = [
        WeatherCacheKey(station_id=source.station_id, binding_fingerprint="a" * 64)
        for source in sources
    ]
    owner = OperationalWeatherRefresh(
        specs=tuple(
            StationWeatherRefresh(
                key=key, source=source, past_requirement=None, future_requests=()
            )
            for key, source in zip(keys, sources, strict=True)
        ),
        cache=cache,
        principal=WritePrincipal(id=None, tenant_id=configs[0].tenant_id),
        issue_time=ISSUE,
        clock=advancing_clock,
        id_gen=uuid4,
        max_past_bytes=2 * 1024 * 1024,
    )
    owner.begin()
    with pytest.raises(RuntimeError, match="second station") as raised:
        owner.finish(earliest_cycle=ISSUE)
    assert raised.value is original_error
    first = cache.fetch(keys[0])
    cleanup_errors = owner.finalize_after_failure(earliest_cycle=ISSUE)
    assert cache.fetch(keys[0]) == first
    assert sum(a.key == keys[0] for a, _ in calls) == 1
    second_completions = [
        completion for attempt, completion in calls if attempt.key == keys[1]
    ]
    assert (
        len(second_completions) == 2 and second_completions[0] is second_completions[1]
    )
    assert cache.fetch(keys[2]).state is WeatherAttemptState.COMPLETE
    if failure == "persistent":
        assert cleanup_errors == (original_error,)
        assert cache.fetch(keys[1]).state is WeatherAttemptState.PENDING
    else:
        assert cleanup_errors == ()
        assert len(owner.finish(earliest_cycle=ISSUE)) == 3


def test_cleanup_preserves_success_unused_and_distinguishes_unattempted_scopes() -> (
    None
):
    from sapphire_flow.services.operational_weather_proof import prove_future_receipt
    from sapphire_flow.types.operational_weather import (
        WeatherFailureReason,
        WeatherLegOutcome,
    )
    from tests.unit.services.test_operational_weather_proof import proof_rows

    scope, rows = proof_rows()
    source = replace(make_binding(), station_id=scope.station_id)
    station = make_station_config(station_id=scope.station_id)
    stations = FakeStationStore()
    stations.store_station(station)
    cache = FakeOperationalWeatherCache(station_store=stations)
    key = WeatherCacheKey(station_id=station.id, binding_fingerprint="a" * 64)
    requests = (
        scope.request,
        replace(scope.request, source="ifs-failed"),
        replace(scope.request, source="ifs-unattempted"),
    )
    owner = OperationalWeatherRefresh(
        specs=(
            StationWeatherRefresh(
                key=key, source=source, past_requirement=None, future_requests=requests
            ),
        ),
        cache=cache,
        principal=WritePrincipal(id=None, tenant_id=station.tenant_id),
        issue_time=ISSUE,
        clock=lambda: ISSUE,
        id_gen=uuid4,
        max_past_bytes=2 * 1024 * 1024,
    )
    owner.begin()
    owner.record_future(
        request=requests[0],
        outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
        receipt=prove_future_receipt(
            scope=scope,
            cycle_time=ISSUE,
            validated_at=ISSUE,
            fetched=rows,
            readback=rows,
        ),
    )
    owner.fail_future(requests[:2], reason=WeatherFailureReason.AUTHORIZATION)
    assert owner.finalize_after_failure(earliest_cycle=ISSUE) == ()
    entry = cache.fetch(key)
    assert entry is not None and entry.past_outcome.state is WeatherLegState.UNUSED
    outcomes = {attempt.scope: attempt.outcome for attempt in entry.future_attempts}
    assert outcomes[requests[0]] == WeatherLegOutcome(state=WeatherLegState.SUCCESS)
    assert outcomes[requests[1]] == WeatherLegOutcome(
        state=WeatherLegState.FATAL, reason=WeatherFailureReason.AUTHORIZATION
    )
    assert outcomes[requests[2]] == WeatherLegOutcome(
        state=WeatherLegState.NOT_ATTEMPTED, reason=WeatherFailureReason.PARENT_FAILURE
    )
