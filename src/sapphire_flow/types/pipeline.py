from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import (
        FlowRunState,
        PipelineCheckType,
        PipelineHealthStatus,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class PipelineHealthRecord:
    check_type: PipelineCheckType
    checked_at: UtcDatetime
    status: PipelineHealthStatus
    subject: str
    detail: dict  # type: ignore[type-arg]
    cycle_time: UtcDatetime | None
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class FlowRunStatus:
    flow_name: str
    run_id: str
    state: FlowRunState
    started_at: UtcDatetime | None
    duration_seconds: float | None
    error_message: str | None
