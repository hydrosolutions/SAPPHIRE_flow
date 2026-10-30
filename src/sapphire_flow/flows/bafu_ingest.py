from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

import structlog

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.bafu_capture import NormalizedDischargeCapture
from sapphire_flow.types.bafu_capture_batch import (
    BafuCaptureBatchId,
    BafuStationCaptureBatch,
)
from sapphire_flow.types.bafu_unit_registry import (
    BafuDischargeKey,
    BafuGaugeCode,
    UnitRefreshState,
)
from sapphire_flow.types.bafu_write_result import BafuApplicability, BafuWriteStatus
from sapphire_flow.types.enums import StationKind

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_unit_registry import BafuUnitRegistry
    from sapphire_flow.types.bafu_write_result import BafuWriteResult
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import HydroScraperBatchResult, RawObservation
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)


class BafuCaptureWriter(Protocol):
    def write(self, batch: BafuStationCaptureBatch) -> BafuWriteResult: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class BafuDispatchResult:
    captured_station_ids: frozenset[StationId]
    failed_station_ids: frozenset[StationId]
    results: tuple[BafuWriteResult, ...]
    canonical_observations: tuple[RawObservation, ...]
    observations_written: int
    errors: tuple[str, ...]
    refresh_due_station_ids: frozenset[StationId] = frozenset()


def dispatch_bafu_captures(
    fetched: HydroScraperBatchResult,
    stations: list[StationConfig],
    registry: BafuUnitRegistry | None,
    writer: BafuCaptureWriter | None,
) -> BafuDispatchResult:
    if (registry is None) != (writer is None):
        raise ConfigurationError("BAFU unit registry and writer are required together")
    captured = fetched.captured_outcomes
    if captured and (registry is None or writer is None):
        raise ConfigurationError(
            "BAFU captures require unit registry and writer; generic fallback forbidden"
        )
    by_id = {station.id: station for station in stations}
    results: list[BafuWriteResult] = []
    canonical: list[RawObservation] = []
    failed: set[StationId] = set()
    errors: list[str] = []
    written = 0
    for outcome in captured:
        assert registry is not None and writer is not None
        station = by_id[outcome.station_id]
        if station.network != "bafu" or station.station_kind not in (
            StationKind.RIVER,
            StationKind.LAKE,
        ):
            raise ConfigurationError("BAFU capture has an invalid station binding")
        try:
            batch = BafuStationCaptureBatch(
                batch_id=BafuCaptureBatchId(uuid4()),
                tenant_id=station.tenant_id,
                station_id=station.id,
                key=BafuDischargeKey(
                    gauge=BafuGaugeCode(value=station.code),
                    kind="river"
                    if station.station_kind is StationKind.RIVER
                    else "lake",
                ),
                received_at=outcome.source_captures[0].received_at,
                registry=registry,
                observations=outcome.observations,
                captures=outcome.source_captures,
            )
        except ValueError as exc:
            failed.add(station.id)
            errors.append(f"BAFU batch invalid for {station.id}: {exc}")
            log.warning(
                "ingest.bafu_capture_invalid",
                station_id=str(station.id),
                error=str(exc),
            )
            continue
        try:
            result = writer.write(batch)
        except Exception as exc:  # station failure is isolated; never split or retry
            failed.add(station.id)
            errors.append(f"BAFU capture failed for {station.id}: {type(exc).__name__}")
            log.warning(
                "ingest.bafu_capture_failed",
                station_id=str(station.id),
                batch_id=str(batch.batch_id),
                error_type=type(exc).__name__,
            )
            continue
        results.append(result)
        log.info(
            "ingest.bafu_capture_result",
            station_id=str(station.id),
            batch_id=str(batch.batch_id),
            status=result.status.value,
            applicability=result.applicability.value,
        )
        if (
            result.applicability is not BafuApplicability.CURRENT
            or result.status
            not in (
                BafuWriteStatus.COMMITTED,
                BafuWriteStatus.REPLAYED,
                BafuWriteStatus.CURRENT,
            )
        ):
            failed.add(station.id)
            errors.append(
                f"BAFU capture unresolved for {station.id}: "
                f"{result.status.value}/{result.applicability.value}"
            )
            continue
        written += result.observations_written
        canonical.extend(result.canonical_observations)
        if batch.observations and not result.canonical_observations:
            failed.add(station.id)
            errors.append(
                f"BAFU capture withheld all canonical output for {station.id}"
            )
    return BafuDispatchResult(
        captured_station_ids=frozenset(outcome.station_id for outcome in captured),
        failed_station_ids=frozenset(failed),
        results=tuple(results),
        canonical_observations=tuple(canonical),
        observations_written=written,
        errors=tuple(errors),
        refresh_due_station_ids=frozenset(
            outcome.station_id
            for outcome in captured
            if any(
                isinstance(capture.result, NormalizedDischargeCapture)
                and capture.result.resolution.refresh is UnitRefreshState.DUE
                for capture in outcome.source_captures
            )
        ),
    )
