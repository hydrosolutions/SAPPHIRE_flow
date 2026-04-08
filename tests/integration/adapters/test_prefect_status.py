from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from prefect.client.schemas.objects import StateType

from sapphire_flow.adapters.prefect_status import PrefectStatusAdapter
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import FlowRunState

_SINCE: UtcDatetime = ensure_utc(datetime(2024, 6, 1, tzinfo=UTC))
_RUN_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_START_TIME = datetime(2024, 6, 15, 10, 0, tzinfo=UTC)


@dataclass
class _FakeState:
    message: str | None = None


@dataclass
class _FakeFlowRun:
    id: uuid.UUID
    state_type: StateType
    start_time: datetime | None
    total_run_time: timedelta | None
    state: _FakeState | None


class FakeSyncPrefectClient:
    def __init__(self, flow_runs: list[_FakeFlowRun]) -> None:
        self._flow_runs = flow_runs

    def read_flow_runs(self, **kwargs: Any) -> list[_FakeFlowRun]:
        return self._flow_runs


class _RaisingClient:
    def read_flow_runs(self, **kwargs: Any) -> list[_FakeFlowRun]:
        raise RuntimeError("connection refused")


def _make_run(
    state_type: StateType,
    start_time: datetime | None = _START_TIME,
    total_run_time: timedelta | None = timedelta(seconds=60),
    message: str | None = None,
) -> _FakeFlowRun:
    return _FakeFlowRun(
        id=_RUN_ID,
        state_type=state_type,
        start_time=start_time,
        total_run_time=total_run_time,
        state=_FakeState(message=message),
    )


class TestPrefectStatusAdapter:
    def test_state_mapping_all_nine(self) -> None:
        expected = [
            (StateType.SCHEDULED, FlowRunState.PENDING),
            (StateType.PENDING, FlowRunState.PENDING),
            (StateType.RUNNING, FlowRunState.RUNNING),
            (StateType.PAUSED, FlowRunState.RUNNING),
            (StateType.COMPLETED, FlowRunState.COMPLETED),
            (StateType.FAILED, FlowRunState.FAILED),
            (StateType.CRASHED, FlowRunState.CRASHED),
            (StateType.CANCELLING, FlowRunState.CANCELLING),
            (StateType.CANCELLED, FlowRunState.CANCELLED),
        ]
        for state_type, expected_state in expected:
            run = _make_run(state_type)
            client = FakeSyncPrefectClient([run])
            adapter = PrefectStatusAdapter(client)  # type: ignore[arg-type]
            results = adapter.fetch_recent_runs(["my-flow"], _SINCE)
            assert results[0].state == expected_state, f"Failed for {state_type}"

    def test_duration_present(self) -> None:
        run = _make_run(StateType.COMPLETED, total_run_time=timedelta(seconds=123.5))
        adapter = PrefectStatusAdapter(FakeSyncPrefectClient([run]))  # type: ignore[arg-type]
        results = adapter.fetch_recent_runs(["flow"], _SINCE)
        assert results[0].duration_seconds == 123.5

    def test_duration_missing(self) -> None:
        run = _make_run(StateType.COMPLETED, total_run_time=None)
        adapter = PrefectStatusAdapter(FakeSyncPrefectClient([run]))  # type: ignore[arg-type]
        results = adapter.fetch_recent_runs(["flow"], _SINCE)
        assert results[0].duration_seconds is None

    def test_error_message_failed_run(self) -> None:
        run = _make_run(StateType.FAILED, message="Task X failed")
        adapter = PrefectStatusAdapter(FakeSyncPrefectClient([run]))  # type: ignore[arg-type]
        results = adapter.fetch_recent_runs(["flow"], _SINCE)
        assert results[0].error_message == "Task X failed"

    def test_error_message_completed_run(self) -> None:
        run = _make_run(StateType.COMPLETED, message="Some message")
        adapter = PrefectStatusAdapter(FakeSyncPrefectClient([run]))  # type: ignore[arg-type]
        results = adapter.fetch_recent_runs(["flow"], _SINCE)
        assert results[0].error_message is None

    def test_started_at_none_for_pending(self) -> None:
        run = _make_run(StateType.PENDING, start_time=None)
        adapter = PrefectStatusAdapter(FakeSyncPrefectClient([run]))  # type: ignore[arg-type]
        results = adapter.fetch_recent_runs(["flow"], _SINCE)
        assert results[0].started_at is None

    def test_client_failure_wraps_in_adapter_error(self) -> None:
        adapter = PrefectStatusAdapter(_RaisingClient())  # type: ignore[arg-type]
        with pytest.raises(AdapterError, match="connection refused"):
            adapter.fetch_recent_runs(["flow"], _SINCE)

    def test_unknown_state_raises_adapter_error(self) -> None:
        run = _make_run(StateType.COMPLETED)
        run = _FakeFlowRun(
            id=_RUN_ID,
            state_type="UNKNOWN_BOGUS_STATE",  # type: ignore[arg-type]
            start_time=_START_TIME,
            total_run_time=timedelta(seconds=1),
            state=_FakeState(),
        )
        adapter = PrefectStatusAdapter(FakeSyncPrefectClient([run]))  # type: ignore[arg-type]
        with pytest.raises(AdapterError, match="Unknown Prefect state type"):
            adapter.fetch_recent_runs(["flow"], _SINCE)
