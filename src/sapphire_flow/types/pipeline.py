from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus


class PipelineHealthRecord(NamedTuple):
    check_type: PipelineCheckType
    checked_at: UtcDatetime
    status: PipelineHealthStatus
    subject: str
    detail: dict  # type: ignore[type-arg]
    cycle_time: UtcDatetime | None
    created_at: UtcDatetime


class FlowRunStatus(NamedTuple):
    flow_name: str
    run_id: str
    state: str
    started_at: UtcDatetime | None
    duration_seconds: float | None
    error_message: str | None
