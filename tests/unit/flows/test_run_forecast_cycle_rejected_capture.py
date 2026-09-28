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
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
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
    from pathlib import Path


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

    def test_thread_start_failure_logs_write_failed_once_per_buffered_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Code review finding, 2026-09-28: the outer `except Exception`
        (covering `thread.start()`/`.join()` themselves raising, as opposed
        to the thread's OWN write failing) is a DIFFERENT code path from
        `test_store_failure_logs_write_failed_once_per_buffered_entry`
        above — it shares `_log_rejected_write_failed`, but nothing had
        driven it with more than a hypothetical one-entry buffer before."""

        def _start_that_fails(self: threading.Thread) -> None:
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(threading.Thread, "start", _start_that_fails)
        store = _StubStore(behavior="succeed")
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
        assert store.calls == []

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


class TestCaptureRunsEvenWhenClosingTheHttpClientRaises:
    """Code review finding, 2026-09-28 (P1): `_capture_rejected_forecasts`
    sat in the SAME `try` as `created_http_client.close()` in the flow's
    outermost `finally`, so a `close()` failure skipped the D5/D6 write
    entirely. This drives a REAL `run_forecast_cycle_flow()` run — the only
    way `created_http_client` is ever non-`None` is the flow building its
    own `MeteoSwissNwpAdapter` (no injected `adapter=`), mirroring
    `test_run_forecast_cycle.py::test_constructs_meteoswiss_adapter_when_
    config_enabled` — with a QC rule set that rejects the station's only
    model, and `httpx.Client.close` patched to raise."""

    def test_close_failure_does_not_skip_the_capture_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.flows.run_forecast_cycle import run_forecast_cycle_flow
        from sapphire_flow.types.domain import ForecastQcRuleParams, ForecastQcRuleSet
        from tests.fakes.fake_stores import (
            FakeAlertStore,
            FakeBasinStore,
            FakeClimBaselineStore,
            FakeForecastStore,
            FakeHistoricalForcingStore,
            FakeModelArtifactStore,
            FakeModelStateStore,
            FakeObservationStore,
            FakePipelineHealthStore,
            FakeStationStore,
            FakeWeatherForecastStore,
        )
        from tests.unit.flows.test_run_forecast_cycle import (
            _MODEL_ID,
            _build_station_and_stores,
            _clock,
            _make_config,
            _SmallFakeModel,
            _write_forecast_cycle_config,
        )

        config_path = _write_forecast_cycle_config(
            tmp_path / "config.toml",
            f"""
[adapters.weather_forecast]
enabled = true
stac_base_url = "https://example.test/stac"
stac_collection = "test-collection"
scratch_path = "{tmp_path / "scratch"}"
""",
        )
        monkeypatch.setenv("SAPPHIRE_CONFIG", str(config_path))
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)

        class _PatchedMeteoSwissNwpAdapter:
            def __init__(self, *, http_client: object, **kwargs: object) -> None:
                pass

            def fetch_forecasts(
                self, station_configs: object, cycle_time: object
            ) -> dict[object, object]:
                return {}

        def _raising_close(self: object) -> None:
            raise RuntimeError("boom: connection pool teardown failed")

        monkeypatch.setattr(httpx.Client, "close", _raising_close)

        sid = StationId(uuid4())
        station_store = FakeStationStore()
        obs_store = FakeObservationStore()
        nwp_store = FakeWeatherForecastStore()
        artifact_store = FakeModelArtifactStore()
        forecast_store = FakeForecastStore()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            station_store,
            obs_store,
            nwp_store,
            artifact_store,
            FakeHistoricalForcingStore(),
        )
        capture_store = _StubStore(behavior="succeed")
        all_reject_qc_rules = ForecastQcRuleSet(
            version="test-all-reject",
            rules=(
                ForecastQcRuleParams(
                    rule_id="range_check",
                    rule_version="1.0",
                    parameter="discharge",
                    time_step=timedelta(hours=1),  # matches _SmallFakeModel's step
                    thresholds={"value_min": 1000.0, "value_max": 2000.0},
                ),
            ),
        )

        with (
            patch(
                "sapphire_flow.adapters.meteoswiss_nwp.MeteoSwissNwpAdapter",
                _PatchedMeteoSwissNwpAdapter,
            ),
            pytest.raises(RuntimeError, match="boom: connection pool teardown"),
        ):
            run_forecast_cycle_flow(
                station_store=station_store,
                obs_store=obs_store,
                weather_forecast_store=nwp_store,
                forecast_store=forecast_store,
                model_state_store=FakeModelStateStore(),
                artifact_store=artifact_store,
                alert_store=FakeAlertStore(),
                baseline_store=FakeClimBaselineStore(),
                basin_store=FakeBasinStore(),
                forcing_store=FakeHistoricalForcingStore(),
                pipeline_health_store=FakePipelineHealthStore(),
                models={_MODEL_ID: _SmallFakeModel()},
                config=_make_config(),
                qc_rules=all_reject_qc_rules,
                clock=_clock,
                rejected_forecast_store=capture_store,
            )

        # The close() failure propagates (unchanged, pre-existing Python
        # try/finally semantics — this fold does not and cannot suppress
        # it), but capture STILL ran before it did.
        assert capture_store.committed is True
        assert len(capture_store.calls) == 1
