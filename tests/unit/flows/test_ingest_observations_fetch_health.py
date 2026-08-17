"""Plan 175 T3/D8/D9 lock tests: the ingest flow must report a truthful
`stations_failed` instead of silently succeeding while dropping stations,
and must write the fetch `PipelineHealthRecord` immediately after fetch
reconciliation — before store/QC — so a later storage failure can never
suppress the fetch signal.
"""

from __future__ import annotations

import contextlib
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sapphire_flow.flows.ingest_observations import (
    IngestResult,
    ingest_observations_flow,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    FetchOutcomeCause,
    ObservationSource,
    PipelineCheckType,
    PipelineHealthStatus,
)
from sapphire_flow.types.observation import (
    HydroScraperBatchResult,
    RawObservation,
    StationFetchOutcome,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationConfig
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeClimBaselineStore,
    FakeObservationStore,
    FakePipelineHealthStore,
    FakeStationStore,
)

_NOW = ensure_utc(datetime(2026, 8, 17, 8, 0, tzinfo=UTC))


def _fixed_clock() -> UtcDatetime:
    return _NOW


def _obs(
    station_id: StationId, parameter: str = "discharge", value: float = 42.0
) -> RawObservation:
    return RawObservation(
        station_id=station_id,
        timestamp=_NOW,
        parameter=parameter,
        value=value,
        source=ObservationSource.MEASURED,
    )


class _FakeBatchAdapter:
    """A `HydroScraperAdapter` stand-in that returns pre-built per-station
    outcomes — exercising the typed batch method the ingest flow now calls,
    not the `StationDataSource` list façade."""

    def __init__(
        self, outcomes_by_station: dict[StationId, StationFetchOutcome]
    ) -> None:
        self._outcomes_by_station = outcomes_by_station

    def fetch_observations_batch(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> HydroScraperBatchResult:
        outcomes = tuple(
            self._outcomes_by_station[sc.id]
            for sc in station_configs
            if sc.id in self._outcomes_by_station
        )
        return HydroScraperBatchResult(outcomes=outcomes)


class _StorageFailingObsStore(FakeObservationStore):
    def store_raw_observations(
        self, observations: list[RawObservation]
    ) -> list[object]:
        raise RuntimeError("storage backend unavailable")


class TestMixedSuccessAndFailureReporting:
    def test_mixed_429_and_success_reports_nonzero_stations_failed(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern", rng=random.Random(1))
        s2 = make_station_config(code="2289", name="Rhein Basel", rng=random.Random(2))

        station_store = FakeStationStore()
        station_store.store_station(s1)
        station_store.store_station(s2)

        outcomes = {
            s1.id: StationFetchOutcome(
                station_id=s1.id,
                observations=(_obs(s1.id),),
                failure_cause=None,
                failure_detail=None,
            ),
            s2.id: StationFetchOutcome(
                station_id=s2.id,
                observations=(),
                failure_cause=FetchOutcomeCause.RATE_LIMITED,
                failure_detail="429 after retry exhaustion",
            ),
        }
        health_store = FakePipelineHealthStore()

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=FakeObservationStore(),
            baseline_store=FakeClimBaselineStore(),
            adapter=_FakeBatchAdapter(outcomes),
            clock=_fixed_clock,
            pipeline_health_store=health_store,
        )

        assert isinstance(result, IngestResult)
        assert result.stations_polled == 2
        assert result.stations_failed == 1

        records = health_store.fetch_recent(PipelineCheckType.OBSERVATION_INGEST_FETCH)
        assert len(records) == 1
        record = records[0]
        assert record.status is PipelineHealthStatus.WARNING
        for key in (
            "stations_polled",
            "stations_fetch_failed",
            "failure_counts_by_cause",
            "failed_station_ids",
        ):
            assert key in record.detail
        assert record.detail["stations_polled"] == 2
        assert record.detail["stations_fetch_failed"] == 1
        assert record.detail["failure_counts_by_cause"] == {"rate_limited": 1}
        assert record.detail["failed_station_ids"] == [str(s2.id)]

    def test_all_stations_429_does_not_report_success(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern", rng=random.Random(1))

        station_store = FakeStationStore()
        station_store.store_station(s1)

        outcomes = {
            s1.id: StationFetchOutcome(
                station_id=s1.id,
                observations=(),
                failure_cause=FetchOutcomeCause.RATE_LIMITED,
                failure_detail="429 after retry exhaustion",
            ),
        }
        health_store = FakePipelineHealthStore()

        result = ingest_observations_flow(
            station_store=station_store,
            obs_store=FakeObservationStore(),
            baseline_store=FakeClimBaselineStore(),
            adapter=_FakeBatchAdapter(outcomes),
            clock=_fixed_clock,
            pipeline_health_store=health_store,
        )

        # The defect being fixed: a mass-429 run used to look identical to
        # "no new data, all healthy" (stations_failed == 0). It must not.
        assert result.stations_failed == 1
        assert result.observations_fetched == 0
        assert len(result.errors) == 1

        records = health_store.fetch_recent(PipelineCheckType.OBSERVATION_INGEST_FETCH)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.CRITICAL

    def test_storage_failure_after_fetch_does_not_suppress_fetch_record(self) -> None:
        s1 = make_station_config(code="2135", name="Aare Bern", rng=random.Random(1))

        station_store = FakeStationStore()
        station_store.store_station(s1)

        outcomes = {
            s1.id: StationFetchOutcome(
                station_id=s1.id,
                observations=(_obs(s1.id),),
                failure_cause=None,
                failure_detail=None,
            ),
        }
        health_store = FakePipelineHealthStore()

        # The storage failure propagating is not what this test locks.
        with contextlib.suppress(RuntimeError):
            ingest_observations_flow(
                station_store=station_store,
                obs_store=_StorageFailingObsStore(),
                baseline_store=FakeClimBaselineStore(),
                adapter=_FakeBatchAdapter(outcomes),
                clock=_fixed_clock,
                pipeline_health_store=health_store,
            )

        records = health_store.fetch_recent(PipelineCheckType.OBSERVATION_INGEST_FETCH)
        assert len(records) == 1
        assert records[0].status is PipelineHealthStatus.OK
        assert records[0].detail["stations_fetch_failed"] == 0
