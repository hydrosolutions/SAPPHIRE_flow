from __future__ import annotations

import importlib
from datetime import timedelta
from itertools import count
from typing import TYPE_CHECKING, Literal

import pytest

from sapphire_flow.adapters.recap_gateway import RecapAuthError
from sapphire_flow.exceptions import StoreError
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    ForecastDataUse,
    PipelineCheckType,
    PipelineHealthStatus,
)
from sapphire_flow.types.operational_weather import (
    WeatherAttemptState,
    WeatherFailureReason,
    WeatherLegState,
)
from tests.fakes.fake_stores import FakePipelineHealthStore
from tests.fakes.operational_weather import FakeOperationalWeatherCache
from tests.unit.adapters.test_recap_operational import ISSUE
from tests.unit.flows.test_run_forecast_cycle import _FakeCandidateAwareSource
from tests.unit.flows.test_selected_weather_cycle import (
    FixtureOperationalApi,
    _run_selected_cycle,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.types.ids import ModelId
    from sapphire_flow.types.operational_weather import (
        WeatherCacheAttempt,
        WeatherCacheCompletion,
        WeatherCacheEntry,
    )
    from sapphire_flow.types.write_principal import WritePrincipal
    from tests.fakes.fake_stores import FakeStationGroupStore


@pytest.mark.parametrize("failure", ["transient", "ack_lost", "persistent"])
def test_native_partial_finish_preserves_original_and_daily_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["transient", "ack_lost", "persistent"],
) -> None:
    problem = StoreError("original second-station persistence failure")
    caches: list[FakeOperationalWeatherCache] = []
    daily_calls: list[object] = []
    flow = importlib.import_module("sapphire_flow.flows.run_forecast_cycle")

    class DailyCleanup:
        def fail_unstarted(self, reason: object) -> None:
            daily_calls.append(reason)

    monkeypatch.setattr(
        flow, "prepare_daily_execution", lambda **kwargs: DailyCleanup()
    )

    class FailingCache(FakeOperationalWeatherCache):
        def __init__(self, station_store: object) -> None:
            super().__init__(station_store=station_store)
            self.calls: dict[object, list[WeatherCacheCompletion]] = {}
            self.failed_key: object | None = None

        def finish(
            self,
            *,
            attempt: WeatherCacheAttempt,
            principal: WritePrincipal,
            completion: WeatherCacheCompletion,
            earliest_cycle: UtcDatetime,
        ) -> WeatherCacheEntry:
            self.calls.setdefault(attempt.key, []).append(completion)
            if self.failed_key is None and len(self.calls) == 2:
                self.failed_key = attempt.key
            if attempt.key == self.failed_key and (
                len(self.calls[attempt.key]) == 1 or failure == "persistent"
            ):
                if failure == "ack_lost":
                    super().finish(
                        attempt=attempt,
                        principal=principal,
                        completion=completion,
                        earliest_cycle=earliest_cycle,
                    )
                raise problem
            return super().finish(
                attempt=attempt,
                principal=principal,
                completion=completion,
                earliest_cycle=earliest_cycle,
            )

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        cache = FailingCache(stores["station_store"])
        caches.append(cache)
        ticks = count()

        def clock() -> UtcDatetime:
            return ensure_utc(ISSUE + timedelta(seconds=next(ticks)))

        return {"operational_weather_cache": cache, "clock": clock}

    with pytest.raises(StoreError, match="original second-station") as raised:
        _run_selected_cycle(
            monkeypatch,
            group_run=True,
            capture_fault="none",
            station_count=3,
            configure_cycle=configure,
        )
    assert raised.value is problem
    assert len(daily_calls) == 1
    cache = caches[0]
    assert len(cache.entries) == 3
    states = [entry.state for entry in cache.entries.values()]
    assert states.count(WeatherAttemptState.COMPLETE) == (
        2 if failure == "persistent" else 3
    )
    assert states.count(WeatherAttemptState.PENDING) == (
        1 if failure == "persistent" else 0
    )


@pytest.mark.parametrize("leg", ["past", "future"])
@pytest.mark.parametrize("invocation", ["current", "replay", "provisional"])
def test_selected_auth_fatal_emits_only_eligible_freshness(
    monkeypatch: pytest.MonkeyPatch,
    leg: Literal["past", "future"],
    invocation: Literal["current", "replay", "provisional"],
) -> None:
    health = FakePipelineHealthStore()
    caches: list[FakeOperationalWeatherCache] = []
    problem = RecapAuthError("fixture authorization failure", status_code=401)

    def fail(*args: object, **kwargs: object) -> object:
        raise problem

    monkeypatch.setattr(
        FixtureOperationalApi if leg == "past" else _FakeCandidateAwareSource,
        "operational" if leg == "past" else "fetch_requirement",
        fail,
    )

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        cache = FakeOperationalWeatherCache(station_store=stores["station_store"])
        caches.append(cache)
        return {
            "pipeline_health_store": health,
            "operational_weather_cache": cache,
            "cycle_time": ISSUE.isoformat() if invocation == "replay" else None,
        }

    with pytest.raises(RecapAuthError, match="fixture authorization") as raised:
        _run_selected_cycle(
            monkeypatch,
            group_run=True,
            capture_fault="none",
            configure_cycle=configure,
            data_use=ForecastDataUse.PROVISIONAL_RATING
            if invocation == "provisional"
            else ForecastDataUse.STANDARD,
        )
    assert raised.value is problem
    records = health.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS)
    assert len(records) == (1 if invocation == "current" else 0)
    assert all(record.status is PipelineHealthStatus.CRITICAL for record in records)
    assert caches[0].entries
    assert all(
        entry.state is WeatherAttemptState.FAILED
        for entry in caches[0].entries.values()
    )
    for entry in caches[0].entries.values():
        assert entry.past_outcome.state is (
            WeatherLegState.FATAL if leg == "past" else WeatherLegState.SUCCESS
        )
        assert entry.past_outcome.reason is (
            WeatherFailureReason.AUTHORIZATION if leg == "past" else None
        )
        assert all(
            attempt.outcome.state
            is (
                WeatherLegState.NOT_ATTEMPTED
                if leg == "past"
                else WeatherLegState.FATAL
            )
            for attempt in entry.future_attempts
        )
        assert all(
            attempt.outcome.reason
            is (
                WeatherFailureReason.PARENT_FAILURE
                if leg == "past"
                else WeatherFailureReason.AUTHORIZATION
            )
            for attempt in entry.future_attempts
        )


def test_selected_current_nonmidnight_failure_emits_once_before_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = FakePipelineHealthStore()
    caches: list[FakeOperationalWeatherCache] = []

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        cache = FakeOperationalWeatherCache(station_store=stores["station_store"])
        caches.append(cache)
        return {
            "pipeline_health_store": health,
            "operational_weather_cache": cache,
            "cycle_time": None,
            "clock": lambda: ensure_utc(ISSUE + timedelta(hours=1)),
        }

    with pytest.raises(ValueError, match="midnight"):
        _run_selected_cycle(
            monkeypatch, group_run=True, capture_fault="none", configure_cycle=configure
        )
    assert caches[0].entries == {}
    records = health.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS)
    assert len(records) == 1 and records[0].status is PipelineHealthStatus.CRITICAL


@pytest.mark.parametrize("phase", ["begin", "proof", "finish"])
def test_current_selected_store_or_proof_fatal_emits_once(
    monkeypatch: pytest.MonkeyPatch,
    phase: Literal["begin", "proof", "finish"],
) -> None:
    health = FakePipelineHealthStore()
    problem = StoreError("fixture selected store or proof failure")
    caches: list[FakeOperationalWeatherCache] = []

    def fail(*args: object, **kwargs: object) -> object:
        raise problem

    if phase == "proof":
        refresh = importlib.import_module(
            "sapphire_flow.flows.operational_weather_refresh"
        )
        monkeypatch.setattr(refresh, "resolve_selected_weather_tracks", fail)
    else:
        monkeypatch.setattr(FakeOperationalWeatherCache, phase, fail)

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        cache = FakeOperationalWeatherCache(station_store=stores["station_store"])
        caches.append(cache)
        return {
            "pipeline_health_store": health,
            "operational_weather_cache": cache,
            "cycle_time": None,
        }

    with pytest.raises(StoreError, match="fixture selected store or proof") as raised:
        _run_selected_cycle(
            monkeypatch, group_run=True, capture_fault="none", configure_cycle=configure
        )
    assert raised.value is problem
    records = health.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS)
    assert len(records) == 1 and records[0].status is PipelineHealthStatus.CRITICAL
    states = [entry.state for entry in caches[0].entries.values()]
    if phase == "begin":
        assert states == []
    else:
        assert states == [
            WeatherAttemptState.FAILED
            if phase == "proof"
            else WeatherAttemptState.PENDING
        ]


def test_current_selected_setup_fatal_emits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sapphire_flow.exceptions import ConfigurationError

    health = FakePipelineHealthStore()

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        return {
            "pipeline_health_store": health,
            "cycle_time": None,
            "operational_weather_principal": object(),
        }

    with pytest.raises(ConfigurationError, match="valid run principal"):
        _run_selected_cycle(
            monkeypatch, group_run=True, capture_fault="none", configure_cycle=configure
        )
    records = health.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS)
    assert len(records) == 1 and records[0].status is PipelineHealthStatus.CRITICAL


@pytest.mark.parametrize("leg", ["past", "future"])
@pytest.mark.parametrize("category", ["configuration", "integrity", "unexpected"])
def test_native_fatal_category_is_local_to_attempted_leg(
    monkeypatch: pytest.MonkeyPatch,
    leg: Literal["past", "future"],
    category: Literal["configuration", "integrity", "unexpected"],
) -> None:
    from sapphire_flow.adapters.recap_gateway import (
        RecapConfigurationError,
        RecapPayloadIntegrityError,
    )

    problems = {
        "configuration": RecapConfigurationError("fixture fatal category"),
        "integrity": RecapPayloadIntegrityError("fixture fatal category"),
        "unexpected": RuntimeError("fixture fatal category"),
    }
    reasons = {
        "configuration": WeatherFailureReason.CONFIGURATION,
        "integrity": WeatherFailureReason.INTEGRITY,
        "unexpected": WeatherFailureReason.PARENT_FAILURE,
    }
    problem = problems[category]
    caches: list[FakeOperationalWeatherCache] = []

    def fail(*args: object, **kwargs: object) -> object:
        raise problem

    if leg == "past" and category == "unexpected":
        refresh = importlib.import_module(
            "sapphire_flow.flows.operational_weather_refresh"
        )
        monkeypatch.setattr(refresh, "fetch_combined_operational_windows", fail)
    else:
        monkeypatch.setattr(
            FixtureOperationalApi if leg == "past" else _FakeCandidateAwareSource,
            "operational" if leg == "past" else "fetch_requirement",
            fail,
        )

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        cache = FakeOperationalWeatherCache(station_store=stores["station_store"])
        caches.append(cache)
        return {"operational_weather_cache": cache}

    with pytest.raises(type(problem), match="fixture fatal category") as raised:
        _run_selected_cycle(
            monkeypatch, group_run=True, capture_fault="none", configure_cycle=configure
        )
    assert raised.value is problem
    entry = next(iter(caches[0].entries.values()))
    if leg == "past":
        assert entry.past_outcome.state is WeatherLegState.FATAL
        assert entry.past_outcome.reason is reasons[category]
        assert all(
            a.outcome.state is WeatherLegState.NOT_ATTEMPTED
            and a.outcome.reason is WeatherFailureReason.PARENT_FAILURE
            for a in entry.future_attempts
        )
    else:
        assert entry.past_outcome.state is WeatherLegState.SUCCESS
        assert all(
            a.outcome.state is WeatherLegState.FATAL
            and a.outcome.reason is reasons[category]
            for a in entry.future_attempts
        )


@pytest.mark.parametrize("failure", ["archive", "proof"])
def test_actual_future_archive_failure_retains_safe_reason(
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["archive", "proof"],
) -> None:
    from dataclasses import replace

    from sapphire_flow.services.operational_weather_proof import WeatherProofError

    caches: list[FakeOperationalWeatherCache] = []
    problem = StoreError("fixture archive write failure")

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        cache = FakeOperationalWeatherCache(station_store=stores["station_store"])
        caches.append(cache)
        archive = stores["nwp_store"]
        original = archive.fetch_weather_forecasts

        def fail(*args: object, **kwargs: object) -> object:
            raise problem

        def corrupt(*args: object, **kwargs: object) -> object:
            return [
                replace(row, value=row.value + 1.0) for row in original(*args, **kwargs)
            ]

        monkeypatch.setattr(
            archive,
            "store_weather_forecasts"
            if failure == "archive"
            else "fetch_weather_forecasts",
            fail if failure == "archive" else corrupt,
        )
        return {"operational_weather_cache": cache}

    with pytest.raises(
        StoreError if failure == "archive" else WeatherProofError,
        match="archive write|differs from fetched",
    ):
        _run_selected_cycle(
            monkeypatch, group_run=True, capture_fault="none", configure_cycle=configure
        )
    entry = next(iter(caches[0].entries.values()))
    assert entry.past_outcome.state is WeatherLegState.SUCCESS
    assert all(a.outcome.state is WeatherLegState.FATAL for a in entry.future_attempts)
    assert all(
        a.outcome.reason
        is (
            WeatherFailureReason.STORAGE
            if failure == "archive"
            else WeatherFailureReason.INTEGRITY
        )
        for a in entry.future_attempts
    )


@pytest.mark.parametrize("status", [429, 503])
def test_transient_http_status_allows_native_complete_cache_stale_fallback(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    import requests
    from recap_client import ApiClientConfig

    from sapphire_flow.adapters.recap_gateway import RecapTransientError
    from sapphire_flow.adapters.recap_operational_http import (
        BoundedOperationalTransport,
    )

    response = requests.Response()
    response.status_code = status
    response._content = b"proxy page" * 10000
    response._content_consumed = True
    session = requests.Session()
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: response)
    transport = BoundedOperationalTransport(
        config=ApiClientConfig(
            base_url="https://fixture.invalid", api_key="fixture-key"
        ),
        max_bytes=1024,
        session=session,
    )
    with pytest.raises(RecapTransientError, match="upstream unavailable") as raised:
        transport.get_parquet_df("exports/ecmwf/operational", {})
    forecasts = _run_selected_cycle(
        monkeypatch,
        group_run=True,
        capture_fault="none",
        refresh_case="past_unavailable",
        past_refresh_error=raised.value,
    )
    assert forecasts is not None and len(forecasts) == 1


def test_valid_snapshot_over_configured_cap_preserves_typed_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sapphire_flow.store.operational_weather_codec import WeatherSnapshotSizeError

    refresh = importlib.import_module("sapphire_flow.flows.operational_weather_refresh")
    original = refresh.make_past_snapshot
    observed: list[WeatherSnapshotSizeError] = []
    caches: list[FakeOperationalWeatherCache] = []

    def capture_size_failure(**kwargs: object) -> object:
        try:
            return original(**kwargs)
        except WeatherSnapshotSizeError as exc:
            observed.append(exc)
            raise

    monkeypatch.setattr(refresh, "make_past_snapshot", capture_size_failure)

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        cache = FakeOperationalWeatherCache(station_store=stores["station_store"])
        caches.append(cache)
        small = config.operational_weather.model_copy(update={"max_cache_bytes": 1})
        return {
            "operational_weather_cache": cache,
            "config": config.model_copy(update={"operational_weather": small}),
        }

    with pytest.raises(WeatherSnapshotSizeError, match="cache byte limit") as raised:
        _run_selected_cycle(
            monkeypatch, group_run=True, capture_fault="none", configure_cycle=configure
        )
    assert observed == [raised.value]
    entry = next(iter(caches[0].entries.values()))
    assert entry.past_outcome.state is WeatherLegState.FATAL
    assert entry.past_outcome.reason is WeatherFailureReason.CONFIGURATION
    assert all(
        a.outcome.state is WeatherLegState.NOT_ATTEMPTED
        and a.outcome.reason is WeatherFailureReason.PARENT_FAILURE
        for a in entry.future_attempts
    )


@pytest.mark.parametrize("status", [401, 403, 302])
def test_known_fatal_http_status_cannot_reuse_warm_cache_when_body_is_broken(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    from typing import cast

    import requests
    from recap_client import ApiClientConfig

    from sapphire_flow.adapters.recap_gateway import (
        RecapAuthError,
        RecapConfigurationError,
    )
    from sapphire_flow.adapters.recap_operational_http import (
        BoundedOperationalTransport,
    )
    from tests.fakes.fake_stores import FakeForecastStore

    response = requests.Response()
    response.status_code = status

    def chunks(chunk_size: int) -> Iterator[bytes]:
        yield b"proxy error prefix"
        raise requests.exceptions.ChunkedEncodingError("credential-canary")

    monkeypatch.setattr(response, "iter_content", chunks)
    monkeypatch.setattr(response, "close", lambda: None)
    session = requests.Session()
    monkeypatch.setattr(session, "get", lambda *args, **kwargs: response)
    transport = BoundedOperationalTransport(
        config=ApiClientConfig(
            base_url="https://fixture.invalid", api_key="fixture-key"
        ),
        max_bytes=1024,
        session=session,
    )
    expected = RecapAuthError if status in (401, 403) else RecapConfigurationError
    with pytest.raises(expected, match="operational weather") as raised:
        transport.get_parquet_df("exports/ecmwf/operational", {})
    caches: list[FakeOperationalWeatherCache] = []
    second_stores: list[FakeForecastStore] = []
    warmed_snapshot_ids: list[object] = []

    def configure(
        stores: dict[str, object],
        groups: FakeStationGroupStore | None,
        config: DeploymentConfig,
        mid: ModelId,
    ) -> dict[str, object]:
        cache = FakeOperationalWeatherCache(station_store=stores["station_store"])
        caches.append(cache)
        return {"operational_weather_cache": cache}

    def configure_refresh(stores: dict[str, object]) -> dict[str, object]:
        entry = next(iter(caches[0].entries.values()))
        assert entry.past_outcome.state is WeatherLegState.SUCCESS
        assert entry.past_snapshot is not None
        warmed_snapshot_ids.append(entry.past_snapshot.id)
        second_stores.append(cast("FakeForecastStore", stores["forecast_store"]))
        return {}

    with pytest.raises(expected, match="operational weather") as propagated:
        _run_selected_cycle(
            monkeypatch,
            group_run=True,
            capture_fault="none",
            refresh_case="past_unavailable",
            past_refresh_error=raised.value,
            configure_cycle=configure,
            configure_refresh=configure_refresh,
        )
    assert propagated.value is raised.value
    entry = next(iter(caches[0].entries.values()))
    assert entry.past_outcome.state is WeatherLegState.FATAL
    assert entry.past_outcome.reason is (
        WeatherFailureReason.AUTHORIZATION
        if status in (401, 403)
        else WeatherFailureReason.CONFIGURATION
    )
    assert (
        entry.past_snapshot is not None
        and entry.past_snapshot.id == warmed_snapshot_ids[0]
    )
    assert second_stores[0].fetch_forecasts_for_cycle(ISSUE) == []
