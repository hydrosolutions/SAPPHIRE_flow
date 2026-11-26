from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

import httpx

from sapphire_flow.adapters.dhm import DhmAdapter
from sapphire_flow.config.dhm import parse_dhm_config
from sapphire_flow.flows.ingest_observations import ingest_observations_flow
from sapphire_flow.types.enums import (
    PipelineCheckType,
    PipelineHealthStatus,
    StationStatus,
)
from tests.fakes.fake_adapters import FakeStationDataSource
from tests.fakes.fake_stores import (
    FakeClimBaselineStore,
    FakeObservationStore,
    FakePipelineHealthStore,
)
from tests.unit.adapters.test_dhm import NOW, reading, response, settings, station
from tests.unit.flows.test_ingest_observations_dhm import (
    RULES,
    observation,
    stations,
)

if TYPE_CHECKING:
    import pytest

    from sapphire_flow.types.station import StationConfig

DAY = 60 * 24


def _run(
    monkeypatch: pytest.MonkeyPatch,
    config: StationConfig,
    store: FakeObservationStore,
    health: FakePipelineHealthStore,
    rows: list[dict[str, object]],
) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: response(200, json={"results": rows})
        )
    )
    monkeypatch.setattr(
        "sapphire_flow.flows.ingest_observations._load_adapter_config",
        lambda: parse_dhm_config(settings()),
    )
    monkeypatch.setattr(
        "sapphire_flow.flows.ingest_observations._dhm_http_client",
        lambda _config: client,
    )
    monkeypatch.setattr(
        "sapphire_flow.adapters.dhm.DhmAdapter",
        partial(DhmAdapter, sleeper=lambda _: None),
    )
    ingest_observations_flow(
        station_store=stations(config),
        obs_store=store,
        baseline_store=FakeClimBaselineStore(),
        qc_rules=RULES,
        clock=lambda: NOW,
        pipeline_health_store=health,
    )


def _record(health: FakePipelineHealthStore):  # noqa: ANN202
    [record] = health.fetch_recent(PipelineCheckType.OBSERVATION_INGEST_FETCH)
    return record


def test_capped_interval_is_recorded_open_and_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = station()
    store = FakeObservationStore()
    old = observation(config, DAY * 30)
    store.store_raw_observations([old])
    health = FakePipelineHealthStore()
    _run(monkeypatch, config, store, health, [])
    record = _record(health)
    assert record.detail["lookback_capped"] == [
        {
            "station_id": str(config.id),
            "start": old.timestamp.isoformat(),
            "end": None,
        }
    ]
    assert record.detail["stations_fetch_failed"] == 0
    assert record.status is PipelineHealthStatus.OK


def test_capped_interval_ends_at_first_new_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = station()
    store = FakeObservationStore()
    old = observation(config, DAY * 30)
    store.store_raw_observations([old])
    first = observation(config, DAY * 2)
    rows = [
        reading(waterLevelOn=o.timestamp.isoformat())
        for o in (observation(config, 10), first)
    ]
    health = FakePipelineHealthStore()
    _run(monkeypatch, config, store, health, rows)
    [capped] = _record(health).detail["lookback_capped"]
    assert capped["start"] == old.timestamp.isoformat()
    assert capped["end"] == first.timestamp.isoformat()


def test_uncapped_station_is_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    config = station()
    store = FakeObservationStore()
    store.store_raw_observations([observation(config, DAY * 3)])
    health = FakePipelineHealthStore()
    _run(monkeypatch, config, store, health, [])
    assert _record(health).detail["lookback_capped"] == []


def test_skipped_only_run_writes_a_health_record() -> None:
    health = FakePipelineHealthStore()
    weather = replace(station("W"), station_status=StationStatus.SUSPENDED)
    ingest_observations_flow(
        station_store=stations(weather),
        obs_store=FakeObservationStore(),
        baseline_store=FakeClimBaselineStore(),
        adapter=FakeStationDataSource([]),
        qc_rules=RULES,
        clock=lambda: NOW,
        pipeline_health_store=health,
    )
    record = _record(health)
    assert record.detail["stations_polled"] == 0
    assert record.status is PipelineHealthStatus.OK
