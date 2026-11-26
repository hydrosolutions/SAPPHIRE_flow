"""#501 Part 2: a paused station is not polled but stays eligible for QC/native work."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING

from sapphire_flow.flows.ingest_observations import ingest_observations_flow
from sapphire_flow.types.enums import (
    PipelineCheckType,
    PipelineHealthStatus,
    StationStatus,
)
from tests.fakes.fake_stores import (
    FakeClimBaselineStore,
    FakeObservationStore,
    FakePipelineHealthStore,
)
from tests.unit.adapters.test_dhm import NOW, station
from tests.unit.flows.test_ingest_observations_dhm import RULES, stations

if TYPE_CHECKING:
    import pytest

    from sapphire_flow.types.observation import RawObservation
    from sapphire_flow.types.station import StationConfig


class _Adapter:
    def __init__(self) -> None:
        self.polled: list[str] = []

    def fetch_observations_batch(self, configs: list[StationConfig], since: object):  # noqa: ANN202
        from sapphire_flow.types.observation import HydroScraperBatchResult

        self.polled = [c.code for c in configs]
        return HydroScraperBatchResult(outcomes=())


def _run(
    *configs: StationConfig, health: FakePipelineHealthStore | None = None
) -> tuple[_Adapter, FakePipelineHealthStore, object]:
    adapter, health = _Adapter(), health or FakePipelineHealthStore()
    result = ingest_observations_flow(
        station_store=stations(*configs),
        obs_store=FakeObservationStore(),
        baseline_store=FakeClimBaselineStore(),
        adapter=adapter,
        qc_rules=RULES,
        clock=lambda: NOW,
        pipeline_health_store=health,
    )
    return adapter, health, result


def _record(health: FakePipelineHealthStore):  # noqa: ANN202
    [record] = health.fetch_recent(PipelineCheckType.OBSERVATION_INGEST_FETCH)
    return record


def test_paused_station_is_not_polled_but_unpaused_one_is() -> None:
    live, dead = station("A"), replace(station("B"), ingest_paused=True)
    adapter, health, result = _run(live, dead)
    assert adapter.polled == ["A"]
    assert result.stations_polled == 1  # type: ignore[attr-defined]
    assert _record(health).detail["ingest_paused_station_ids"] == [str(dead.id)]


def test_default_is_unpaused_and_polls_everything() -> None:
    adapter, health, _ = _run(station("A"), station("B"))
    assert sorted(adapter.polled) == ["A", "B"]
    assert _record(health).detail["ingest_paused_station_ids"] == []


def test_all_paused_run_reports_paused_and_is_not_a_failure() -> None:
    dead = replace(station("B"), ingest_paused=True)
    adapter, health, result = _run(dead)
    assert adapter.polled == []
    assert result.stations_polled == 0  # type: ignore[attr-defined]
    record = _record(health)
    assert record.detail["ingest_paused_station_ids"] == [str(dead.id)]
    assert record.detail["stations_polled"] == 0
    assert record.status is PipelineHealthStatus.OK


def test_paused_station_still_gets_native_and_bafu_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead = replace(station("B"), ingest_paused=True)
    binding = SimpleNamespace(station_id=dead.id)
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        "sapphire_flow.flows.native_ingest.native_capture_bindings",
        lambda _cfg: (binding,),
    )
    monkeypatch.setattr(
        "sapphire_flow.flows.native_ingest.dispatch_native_captures",
        lambda *_a, **_k: SimpleNamespace(
            failed_station_ids=frozenset(),
            errors=(),
            canonical_observations=(),
        ),
    )

    def recovery(_reader: object, bindings: tuple[object, ...], *, now: object):  # noqa: ANN202
        seen["bindings"] = bindings
        return SimpleNamespace(
            windows=(), backlog=0, errors=(), failed_station_ids=frozenset()
        )

    monkeypatch.setattr(
        "sapphire_flow.flows.native_ingest.discover_native_recovery", recovery
    )

    def bafu(_batch: object, eligible: list[StationConfig], *_a: object):  # noqa: ANN202
        seen["bafu"] = [s.code for s in eligible]
        return None

    monkeypatch.setattr(
        "sapphire_flow.flows.ingest_observations.dispatch_bafu_captures", bafu
    )
    _run(dead)
    assert seen["bindings"] == (binding,)
    assert seen["bafu"] == ["B"]


def test_suspended_paused_station_is_not_reported() -> None:
    gone = replace(
        station("B"), ingest_paused=True, station_status=StationStatus.SUSPENDED
    )
    _, health, _ = _run(gone)
    assert _record(health).detail["ingest_paused_station_ids"] == []


def _unused(_: RawObservation) -> None: ...
