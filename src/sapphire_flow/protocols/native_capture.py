from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ObservationId, StationId
    from sapphire_flow.types.native_capture import (
        CaptureBatchContext,
        CaptureCommit,
        CapturedSourceVersion,
        CaptureQcWindow,
        CaptureRecoveryWindow,
        NativeCapture,
    )
    from sapphire_flow.types.observation import RawObservation


class NativeCaptureWriter(Protocol):
    def commit_batch(
        self, batch: CaptureBatchContext, observations: tuple[RawObservation, ...]
    ) -> CaptureCommit: ...


class NativeCaptureReader(Protocol):
    def resolve(
        self, versions: tuple[CapturedSourceVersion, ...]
    ) -> tuple[NativeCapture, ...]: ...

    def pending_qc(
        self,
        station_id: StationId,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        limit: int,
    ) -> CaptureRecoveryWindow: ...


@runtime_checkable
class CaptureQcRecoveryReader(Protocol):
    def fetch_capture_qc_window(
        self,
        recovery: CaptureRecoveryWindow,
        *,
        context_window_hours: float,
        excluded_ids: frozenset[ObservationId] = frozenset(),
    ) -> CaptureQcWindow: ...
