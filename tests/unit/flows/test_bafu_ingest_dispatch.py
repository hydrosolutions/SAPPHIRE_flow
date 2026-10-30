from dataclasses import replace
from datetime import UTC, datetime

import pytest

from sapphire_flow.adapters.bafu_discharge_capture import capture_bafu_discharge
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.bafu_ingest import dispatch_bafu_captures
from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch
from sapphire_flow.types.bafu_unit_registry import (
    BafuDischargeKey,
    BafuGaugeCode,
    BafuUnitRegistry,
)
from sapphire_flow.types.bafu_write_result import (
    BafuApplicability,
    BafuWriteResult,
    BafuWriteStatus,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import FetchOutcomeCause, ObservationSource
from sapphire_flow.types.observation import (
    HydroScraperBatchResult,
    RawObservation,
    StationFetchOutcome,
)
from sapphire_flow.types.station import StationConfig
from tests.conftest import make_station_config


def test_empty_batch_needs_no_unit_dependencies() -> None:
    result = dispatch_bafu_captures(
        HydroScraperBatchResult(outcomes=()), [], None, None
    )
    assert result.results == ()


def test_configuration_requires_registry_and_writer_together() -> None:
    with pytest.raises(ConfigurationError, match="registry and writer"):
        dispatch_bafu_captures(
            HydroScraperBatchResult(outcomes=()),
            [],
            BafuUnitRegistry(version="test", rules=()),
            None,
        )


class UnknownWriter:
    def __init__(self) -> None:
        self.batches: list[BafuStationCaptureBatch] = []

    def write(self, batch: BafuStationCaptureBatch) -> BafuWriteResult:
        self.batches.append(batch)
        return BafuWriteResult(
            batch_id=batch.batch_id,
            status=BafuWriteStatus.UNKNOWN,
            applicability=BafuApplicability.UNKNOWN,
        )


def captured_input() -> tuple[StationConfig, BafuUnitRegistry, StationFetchOutcome]:
    station = make_station_config(code="2206", network="bafu")
    registry = BafuUnitRegistry(version="unresolved", rules=())
    now = ensure_utc(datetime(2026, 10, 28, tzinfo=UTC))
    capture = capture_bafu_discharge(
        key=BafuDischargeKey(gauge=BafuGaugeCode(value="2206"), kind="river"),
        literals=("22",),
        measurement_times=(now.isoformat(),),
        payload_sha256="a" * 64,
        registry=registry,
        now=now,
    )
    outcome = StationFetchOutcome(
        station_id=station.id,
        observations=(),
        failure_cause=None,
        failure_detail=None,
        source_captures=(capture,),
    )
    return station, registry, outcome


@pytest.mark.parametrize("failed", [False, True])
def test_empty_or_failed_capture_is_dispatched_once_and_unknown_not_retried(
    failed: bool,
) -> None:
    station, registry, outcome = captured_input()
    if failed:
        outcome = replace(
            outcome,
            failure_cause=FetchOutcomeCause.MALFORMED_RESPONSE,
            failure_detail="bad",
        )
    writer = UnknownWriter()
    result = dispatch_bafu_captures(
        HydroScraperBatchResult(outcomes=(outcome,)), [station], registry, writer
    )
    assert len(writer.batches) == 1
    assert writer.batches[0].captures == outcome.source_captures
    assert result.failed_station_ids == frozenset({station.id})
    assert result.results[0].status is BafuWriteStatus.UNKNOWN
    assert result.canonical_observations == ()


def test_capture_cannot_fall_back_without_dependencies() -> None:
    station, _, outcome = captured_input()
    with pytest.raises(ConfigurationError, match="generic fallback forbidden"):
        dispatch_bafu_captures(
            HydroScraperBatchResult(outcomes=(outcome,)), [station], None, None
        )


def test_other_parameters_stay_in_failed_atomic_batch() -> None:
    station, registry, outcome = captured_input()
    other = RawObservation(
        station_id=station.id,
        timestamp=outcome.source_captures[0].received_at,
        parameter="water_level",
        value=3.0,
        source=ObservationSource.MEASURED,
    )
    outcome = replace(outcome, observations=(other,))
    writer = UnknownWriter()
    result = dispatch_bafu_captures(
        HydroScraperBatchResult(outcomes=(outcome,)), [station], registry, writer
    )
    assert writer.batches[0].observations == (other,)
    assert result.observations_written == 0
    assert result.canonical_observations == ()


@pytest.mark.parametrize("has_other", [False, True])
def test_flow_preserves_capture_and_never_falls_back_on_unknown(
    monkeypatch: pytest.MonkeyPatch, has_other: bool
) -> None:
    import sapphire_flow.flows.ingest_observations as module
    from sapphire_flow.types.domain import QcRuleSet
    from tests.fakes.fake_stores import (
        FakeClimBaselineStore,
        FakeObservationStore,
        FakePipelineHealthStore,
        FakeStationStore,
    )

    station, registry, outcome = captured_input()
    if has_other:
        outcome = replace(
            outcome,
            observations=(
                RawObservation(
                    station_id=station.id,
                    timestamp=outcome.source_captures[0].received_at,
                    parameter="water_level",
                    value=3.0,
                    source=ObservationSource.MEASURED,
                ),
            ),
        )
    stations = FakeStationStore()
    stations.store_station(station)
    observations = FakeObservationStore()
    health = FakePipelineHealthStore()
    writer = UnknownWriter()
    monkeypatch.setattr(
        module,
        "_fetch_observations_task",
        lambda *args: HydroScraperBatchResult(outcomes=(outcome,)),
    )

    def forbid_generic(*args: object) -> int:
        pytest.fail("captured station reached generic writer")

    monkeypatch.setattr(module, "_store_raw_task", forbid_generic)
    result = module.ingest_observations_flow.fn(
        station_store=stations,
        obs_store=observations,
        baseline_store=FakeClimBaselineStore(),
        adapter=object(),
        qc_rules=QcRuleSet(version="none", rules=()),
        clock=lambda: outcome.source_captures[0].received_at,
        pipeline_health_store=health,
        bafu_unit_registry=registry,
        bafu_unit_writer=writer,
    )
    assert len(writer.batches) == 1
    assert result.stations_failed == 1
    assert result.observations_stored == 0
    assert result.bafu_unit_results[0].status is BafuWriteStatus.UNKNOWN
    from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus

    record = health.fetch_recent(PipelineCheckType.OBSERVATION_INGEST_FETCH, limit=1)[0]
    assert record.status is PipelineHealthStatus.CRITICAL
    assert record.detail["stations_fetch_failed"] == 0
    assert record.detail["unit_capture_failed_station_ids"] == [str(station.id)]


def test_invalid_batch_does_not_stop_second_station() -> None:
    from uuid import uuid4

    from sapphire_flow.types.ids import StationId

    station, registry, outcome = captured_input()
    second = replace(station, id=StationId(uuid4()))
    valid = replace(outcome, station_id=second.id)
    invalid = replace(
        outcome,
        observations=(
            RawObservation(
                station_id=station.id,
                timestamp=outcome.source_captures[0].received_at,
                parameter="water_level",
                value=float("nan"),
                source=ObservationSource.MEASURED,
            ),
        ),
    )
    from sapphire_flow.types.bafu_write_result import BafuOutcomeCounts

    class SuccessfulWriter(UnknownWriter):
        def write(self, batch: BafuStationCaptureBatch) -> BafuWriteResult:
            self.batches.append(batch)
            return BafuWriteResult(
                batch_id=batch.batch_id,
                status=BafuWriteStatus.CURRENT,
                applicability=BafuApplicability.CURRENT,
                counts=BafuOutcomeCounts(),
            )

    writer = SuccessfulWriter()
    result = dispatch_bafu_captures(
        HydroScraperBatchResult(outcomes=(invalid, valid)),
        [station, second],
        registry,
        writer,
    )
    assert [batch.station_id for batch in writer.batches] == [second.id]
    assert result.failed_station_ids == frozenset({station.id})
    assert len(result.errors) == 1


def test_refresh_due_is_visible_without_withholding() -> None:
    from sapphire_flow.flows.ingest_observations import _append_fetch_health_record
    from sapphire_flow.types.bafu_capture import NormalizedDischargeCapture
    from sapphire_flow.types.bafu_unit_registry import BafuUnitRule
    from sapphire_flow.types.bafu_write_result import BafuOutcomeCounts
    from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus
    from sapphire_flow.types.observation_units import DischargeSourceUnit
    from tests.fakes.fake_stores import FakePipelineHealthStore

    station, _, outcome = captured_input()
    capture = outcome.source_captures[0]
    now = capture.received_at
    registry = BafuUnitRegistry(
        version="due",
        rules=(
            BafuUnitRule(
                rule_id="due",
                key=capture.key,
                unit=DischargeSourceUnit.LITRES_PER_SECOND,
                valid_from=now,
                valid_until=None,
                verified_at=now,
                refresh_due=now,
                evidence_reference="synthetic",
                evidence_sha256="a" * 64,
            ),
        ),
    )
    capture = capture_bafu_discharge(
        key=capture.key,
        literals=("22",),
        measurement_times=(now.isoformat(),),
        payload_sha256="a" * 64,
        registry=registry,
        now=now,
    )
    assert isinstance(capture.result, NormalizedDischargeCapture)
    observation = RawObservation(
        station_id=station.id,
        timestamp=now,
        parameter="discharge",
        value=0.022,
        source=ObservationSource.MEASURED,
    )
    outcome = replace(outcome, observations=(observation,), source_captures=(capture,))

    class SuccessfulWriter:
        def write(self, batch: BafuStationCaptureBatch) -> BafuWriteResult:
            return BafuWriteResult(
                batch_id=batch.batch_id,
                status=BafuWriteStatus.CURRENT,
                applicability=BafuApplicability.CURRENT,
                counts=BafuOutcomeCounts(),
                canonical_observations=batch.observations,
            )

    result = dispatch_bafu_captures(
        HydroScraperBatchResult(outcomes=(outcome,)),
        [station],
        registry,
        SuccessfulWriter(),
    )
    assert result.canonical_observations == (observation,)
    health = FakePipelineHealthStore()
    _append_fetch_health_record(
        health, checked_at=now, outcomes=(outcome,), unit_dispatch=result
    )
    record = health.fetch_recent(PipelineCheckType.OBSERVATION_INGEST_FETCH)[0]
    assert record.status is PipelineHealthStatus.WARNING
    assert record.detail["unit_refresh_due_station_ids"] == [str(station.id)]


@pytest.mark.parametrize("second_fetch_failed", [False, True])
def test_dispatch_health_unions_failures_without_rewriting_fetch_counts(
    second_fetch_failed: bool,
) -> None:
    from uuid import uuid4

    from sapphire_flow.flows.ingest_observations import _append_fetch_health_record
    from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus
    from sapphire_flow.types.ids import StationId
    from tests.fakes.fake_stores import FakePipelineHealthStore

    station, registry, outcome = captured_input()
    # Successful fetch, failed dispatch. Supply an ancillary row so a missing
    # discharge unit does not independently classify this station as fetch-failed.
    outcome = replace(
        outcome,
        observations=(
            RawObservation(
                station_id=station.id,
                timestamp=outcome.source_captures[0].received_at,
                parameter="water_level",
                value=2.0,
                source=ObservationSource.MEASURED,
            ),
        ),
    )
    result = dispatch_bafu_captures(
        HydroScraperBatchResult(outcomes=(outcome,)),
        [station],
        registry,
        UnknownWriter(),
    )
    second = StationFetchOutcome(
        station_id=StationId(uuid4()),
        observations=(),
        failure_cause=FetchOutcomeCause.NO_DATA if second_fetch_failed else None,
        failure_detail="no data" if second_fetch_failed else None,
    )
    health = FakePipelineHealthStore()
    _append_fetch_health_record(
        health,
        checked_at=outcome.source_captures[0].received_at,
        outcomes=(outcome, second),
        unit_dispatch=result,
    )
    record = health.fetch_recent(PipelineCheckType.OBSERVATION_INGEST_FETCH)[0]
    assert record.status is (
        PipelineHealthStatus.CRITICAL
        if second_fetch_failed
        else PipelineHealthStatus.WARNING
    )
    assert record.detail["stations_fetch_failed"] == int(second_fetch_failed)
    assert record.detail["unit_capture_failed_station_ids"] == [str(station.id)]
