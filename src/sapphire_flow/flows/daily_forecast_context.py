from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prefect.context import FlowRunContext

from sapphire_flow.types.daily_forecast import ScheduledDailyForecast
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from uuid import UUID

    from prefect.client.schemas.objects import FlowRun


@dataclass(frozen=True, kw_only=True, slots=True)
class ScheduledDailyRun:
    owner_id: UUID
    context: ScheduledDailyForecast


def scheduled_daily_run(
    run: FlowRun | None,
    *,
    expected_deployment_id: UUID,
) -> ScheduledDailyRun | None:
    if (
        run is None
        or not run.auto_scheduled
        or run.deployment_id != expected_deployment_id
        or run.parent_task_run_id is not None
        or run.expected_start_time is None
    ):
        return None
    return ScheduledDailyRun(
        owner_id=run.id,
        context=ScheduledDailyForecast(
            scheduled_tick=ensure_utc(run.expected_start_time)
        ),
    )


def current_scheduled_daily_run(
    *, expected_deployment_id: UUID
) -> ScheduledDailyRun | None:
    context = FlowRunContext.get()
    return scheduled_daily_run(
        context.flow_run if context is not None else None,
        expected_deployment_id=expected_deployment_id,
    )
