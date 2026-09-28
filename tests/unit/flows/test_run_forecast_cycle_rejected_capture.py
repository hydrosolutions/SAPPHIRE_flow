"""Plan 404 T2 — the flow-level rejected-forecast capture mechanics:
`_collect_rejected_forecasts` (D5, buffered before any early exit) and
`_capture_rejected_forecasts`/`_run_rejected_capture` (D6, the daemon-thread
deadline + abandon-before-commit contract), tested in isolation from the
full forecast cycle so the daemon-thread timing logic gets fast, focused
coverage.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog.testing

from sapphire_flow.exceptions import CaptureAbandonedError
from sapphire_flow.flows import run_forecast_cycle as flow_module
from sapphire_flow.services.run_station_forecast import (
    AssignmentFailure,
    AssignmentFailureCause,
)
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedForecastEntry,
    RejectedParameterPayload,
)
from tests.conftest import make_forecast_ensemble

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest


class _StubStore:
    """A minimal `RejectedForecastStore` double whose `write_batch` behavior
    is controlled per test: succeed, raise, or block on a TEST-OWNED event
    (released in the test's own `finally`, never left running)."""

    def __init__(
        self, *, behavior: str = "succeed", block_event: threading.Event | None = None
    ) -> None:
        self.behavior = behavior
        self.block_event = block_event
        self.calls: list[list[RejectedForecastEntry]] = []
        self.last_abandon: threading.Event | None = None
        self.committed = False

    def write_batch(
        self, entries: Sequence[RejectedForecastEntry], *, abandon: threading.Event
    ) -> None:
        self.calls.append(list(entries))
        self.last_abandon = abandon
        if self.behavior == "block":
            assert self.block_event is not None
            self.block_event.wait()
            if abandon.is_set():
                raise CaptureAbandonedError("abandoned before commit")
            self.committed = True
            return
        if self.behavior == "raise":
            raise RuntimeError("boom")
        self.committed = True


def _payload(
    *, station_id: StationId | None = None, model_id: ModelId | None = None
) -> RejectedAssignmentPayload:
    sid = station_id or StationId(uuid4())
    ensemble = make_forecast_ensemble(station_id=sid, n_steps=1, n_members=1)
    return RejectedAssignmentPayload(
        station_id=sid,
        model_id=model_id or ModelId("m"),
        model_artifact_id=None,
        issued_at=ensemble.issued_at,
        parameters=(
            RejectedParameterPayload(
                ensemble=ensemble, qc_status=QcStatus.QC_FAILED, qc_flags=()
            ),
        ),
    )


class TestCollectRejectedForecasts:
    def test_collects_only_failures_carrying_a_rejected_payload(self) -> None:
        buffer: list[RejectedForecastEntry] = []
        attempt_id = uuid4()
        rejected_payload = _payload()
        failed = {
            ModelId("a"): AssignmentFailure(
                cause=AssignmentFailureCause.QC_FAILED,
                detail="x",
                rejected=rejected_payload,
            ),
            # Not a QC rejection -- no payload, must be skipped.
            ModelId("b"): AssignmentFailure(
                cause=AssignmentFailureCause.MODEL_NOT_FOUND, detail="y"
            ),
        }
        flow_module._collect_rejected_forecasts(buffer, attempt_id, failed)
        assert len(buffer) == 1
        assert buffer[0].attempt_id == attempt_id
        assert buffer[0].payload is rejected_payload

    def test_no_failures_leaves_buffer_untouched(self) -> None:
        buffer: list[RejectedForecastEntry] = []
        flow_module._collect_rejected_forecasts(buffer, uuid4(), {})
        assert buffer == []

    def test_appends_to_a_buffer_already_holding_entries(self) -> None:
        attempt_id = uuid4()
        existing = RejectedForecastEntry(attempt_id=attempt_id, payload=_payload())
        buffer: list[RejectedForecastEntry] = [existing]
        rejected_payload = _payload()
        failed = {
            ModelId("a"): AssignmentFailure(
                cause=AssignmentFailureCause.QC_FAILED,
                detail="x",
                rejected=rejected_payload,
            )
        }
        flow_module._collect_rejected_forecasts(buffer, attempt_id, failed)
        assert buffer == [
            existing,
            RejectedForecastEntry(attempt_id=attempt_id, payload=rejected_payload),
        ]


class TestCaptureRejectedForecasts:
    def test_no_store_never_calls_write_batch(self) -> None:
        entries = [RejectedForecastEntry(attempt_id=uuid4(), payload=_payload())]
        flow_module._capture_rejected_forecasts(None, entries)
        # Nothing to assert on `None` — the contract is simply "no crash,
        # no attempted write" (there is no store to record a call on).

    def test_empty_buffer_never_calls_write_batch(self) -> None:
        store = _StubStore()
        flow_module._capture_rejected_forecasts(store, [])
        assert store.calls == []

    def test_successful_write_is_silent(self) -> None:
        store = _StubStore(behavior="succeed")
        entries = [RejectedForecastEntry(attempt_id=uuid4(), payload=_payload())]
        with structlog.testing.capture_logs() as logs:
            flow_module._capture_rejected_forecasts(store, entries)
        assert store.committed is True
        assert [log_entry["event"] for log_entry in logs] == []

    def test_store_failure_logs_write_failed_once_per_buffered_entry(self) -> None:
        store = _StubStore(behavior="raise")
        entries = [
            RejectedForecastEntry(attempt_id=uuid4(), payload=_payload()),
            RejectedForecastEntry(attempt_id=uuid4(), payload=_payload()),
        ]
        with structlog.testing.capture_logs() as logs:
            flow_module._capture_rejected_forecasts(store, entries)
        failed_events = [
            log_entry
            for log_entry in logs
            if log_entry["event"] == "rejected_forecast.write_failed"
        ]
        assert len(failed_events) == 2

    def test_deadline_exceeded_sets_abandon_and_logs_timed_out_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(flow_module, "REJECTED_CAPTURE_DEADLINE_S", 0.2)
        block_event = threading.Event()
        store = _StubStore(behavior="block", block_event=block_event)
        entries = [RejectedForecastEntry(attempt_id=uuid4(), payload=_payload())]
        try:
            with structlog.testing.capture_logs() as logs:
                flow_module._capture_rejected_forecasts(store, entries)
            timed_out = [
                log_entry
                for log_entry in logs
                if log_entry["event"] == "rejected_forecast.write_timed_out"
            ]
            assert len(timed_out) == 1
            assert timed_out[0]["assignment_count"] == 1
            failed = [
                log_entry
                for log_entry in logs
                if log_entry["event"] == "rejected_forecast.write_failed"
            ]
            assert failed == []
            assert store.last_abandon is not None
            assert store.last_abandon.is_set()
        finally:
            # Release the blocked thread so it can observe `abandon` and
            # exit; no thread outlives the test.
            block_event.set()
            time.sleep(0.05)
        # The store's own write_batch observed the abandon flag and raised
        # CaptureAbandonedError internally (mirrors the real store) —
        # nothing committed.
        assert store.committed is False

    def test_a_slow_but_successful_write_within_the_deadline_is_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(flow_module, "REJECTED_CAPTURE_DEADLINE_S", 2.0)
        block_event = threading.Event()
        store = _StubStore(behavior="block", block_event=block_event)
        entries = [RejectedForecastEntry(attempt_id=uuid4(), payload=_payload())]

        def _release_soon() -> None:
            time.sleep(0.05)
            block_event.set()

        releaser = threading.Thread(target=_release_soon, daemon=True)
        releaser.start()
        try:
            with structlog.testing.capture_logs() as logs:
                flow_module._capture_rejected_forecasts(store, entries)
            assert [log_entry["event"] for log_entry in logs] == []
            assert store.committed is True
        finally:
            block_event.set()
            releaser.join(timeout=1)
