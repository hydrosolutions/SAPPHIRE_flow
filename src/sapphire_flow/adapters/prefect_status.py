from __future__ import annotations

import time
from typing import TYPE_CHECKING, ClassVar

import structlog
from prefect.client.schemas.filters import (
    FlowFilter,
    FlowFilterName,
    FlowRunFilter,
    FlowRunFilterStartTime,
)
from prefect.client.schemas.objects import StateType

from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import FlowRunState
from sapphire_flow.types.pipeline import FlowRunStatus

if TYPE_CHECKING:
    from prefect.client.orchestration import SyncPrefectClient

    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger()


class PrefectStatusAdapter:
    _STATE_MAP: ClassVar[dict[StateType, FlowRunState]] = {
        StateType.SCHEDULED: FlowRunState.PENDING,
        StateType.PENDING: FlowRunState.PENDING,
        StateType.RUNNING: FlowRunState.RUNNING,
        StateType.PAUSED: FlowRunState.RUNNING,
        StateType.COMPLETED: FlowRunState.COMPLETED,
        StateType.FAILED: FlowRunState.FAILED,
        StateType.CRASHED: FlowRunState.CRASHED,
        StateType.CANCELLING: FlowRunState.CANCELLING,
        StateType.CANCELLED: FlowRunState.CANCELLED,
    }

    def __init__(self, client: SyncPrefectClient) -> None:
        self._client = client

    @classmethod
    def _map_state(cls, state_type: StateType) -> FlowRunState:
        try:
            return cls._STATE_MAP[state_type]
        except KeyError:
            raise AdapterError(f"Unknown Prefect state type: {state_type!r}") from None

    def fetch_recent_runs(
        self,
        flow_names: list[str],
        since: UtcDatetime,
    ) -> list[FlowRunStatus]:
        start = time.perf_counter()
        try:
            results: list[FlowRunStatus] = []
            for flow_name in flow_names:
                flow_runs = self._client.read_flow_runs(
                    flow_filter=FlowFilter(name=FlowFilterName(any_=[flow_name])),
                    flow_run_filter=FlowRunFilter(
                        start_time=FlowRunFilterStartTime(
                            after_=since  # type: ignore[arg-type]  # UtcDatetime is a datetime subtype
                        )
                    ),
                )
                for flow_run in flow_runs:
                    if flow_run.state_type is None:
                        raise AdapterError(f"Flow run {flow_run.id} has no state_type")
                    mapped_state = self._map_state(flow_run.state_type)
                    error_message: str | None = None
                    if (
                        mapped_state in (FlowRunState.FAILED, FlowRunState.CRASHED)
                        and flow_run.state is not None
                    ):
                        error_message = flow_run.state.message
                    started_at = (
                        ensure_utc(flow_run.start_time)
                        if flow_run.start_time is not None
                        else None
                    )
                    # Prefect types declare total_run_time as timedelta (not
                    # Optional), but runtime value can be None for runs that
                    # haven't started yet.
                    run_time: float | None = (
                        flow_run.total_run_time.total_seconds()  # type: ignore[union-attr]
                        if getattr(flow_run, "total_run_time", None) is not None
                        else None
                    )
                    results.append(
                        FlowRunStatus(
                            flow_name=flow_name,
                            run_id=str(flow_run.id),
                            state=mapped_state,
                            started_at=started_at,
                            duration_seconds=run_time,
                            error_message=error_message,
                        )
                    )
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"Failed to fetch Prefect flow runs: {exc}") from exc

        end = time.perf_counter()
        log.info(
            "pipeline.status_fetch_completed",
            flow_count=len(flow_names),
            run_count=len(results),
            duration_ms=round((end - start) * 1000, 1),
        )
        return results
