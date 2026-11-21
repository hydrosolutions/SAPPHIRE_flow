"""Shared paused-state checks; callers enforce their own installation guard."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

    from prefect.client.orchestration import PrefectClient

POOLS = ("default", "ingest", "backup")
JOBS = {"forecast-cycle": "default", "backup-database": "backup"}
PREFECT_URL = "http://prefect-server:4200/api"


class DeploymentCheckError(RuntimeError):
    pass


async def pools(client: PrefectClient, action: Literal["create", "verify"]) -> None:
    inventory = await client.read_work_pools(limit=4)
    if action == "create":
        if inventory:
            raise DeploymentCheckError("existing_pools_refused")
        from prefect.client.schemas.actions import WorkPoolCreate

        for name in POOLS:
            await client.create_work_pool(
                WorkPoolCreate(name=name, type="process", is_paused=True)
            )
        inventory = await client.read_work_pools(limit=4)
    if {item.name for item in inventory} != set(POOLS) or len(inventory) != 3:
        raise DeploymentCheckError("pool_inventory_invalid")
    for name in POOLS:
        item = await client.read_work_pool(name)
        if item.type != "process" or item.is_paused is not True:
            raise DeploymentCheckError("pool_pause_not_confirmed")


async def no_execution(client: PrefectClient) -> None:
    from prefect.client.schemas.objects import StateType

    runs = await client.read_flow_runs(limit=101)
    if len(runs) > 100:
        raise DeploymentCheckError("flow_inventory_overflow")
    for run in runs:
        history = await client.read_flow_run_states(run.id)
        if len(history) > 1000:
            raise DeploymentCheckError("flow_history_overflow")
        states = [run.state, *history]
        if run.start_time is not None or any(
            state is None or state.type not in {StateType.SCHEDULED, StateType.PENDING}
            for state in states
        ):
            raise DeploymentCheckError("unexpected_flow_execution")


async def jobs(client: PrefectClient, action: Literal["pause", "verify"]) -> None:
    await pools(client, "verify")
    inventory = await client.read_deployments(limit=3)
    if (
        len(inventory) != 2
        or {item.name: item.work_pool_name for item in inventory} != JOBS
    ):
        raise DeploymentCheckError("deployment_inventory_invalid")
    for deployment in inventory:
        schedules = await client.read_deployment_schedules(deployment.id)
        if not schedules or len(schedules) > 4:
            raise DeploymentCheckError("schedule_inventory_invalid")
        if action == "pause":
            for schedule in schedules:
                await client.update_deployment_schedule(
                    deployment.id, schedule.id, active=False
                )
        readback = await client.read_deployment_schedules(deployment.id)
        if {item.id for item in readback} != {item.id for item in schedules} or any(
            item.active for item in readback
        ):
            raise DeploymentCheckError("schedule_pause_not_confirmed")
    await pools(client, "verify")
    await no_execution(client)


async def workers(client: PrefectClient, now: datetime) -> None:
    from prefect.client.schemas.filters import (
        WorkerFilter,
        WorkerFilterLastHeartbeatTime,
    )

    await jobs(client, "verify")
    recent = WorkerFilter(
        last_heartbeat_time=WorkerFilterLastHeartbeatTime.model_validate(
            {"after_": now - timedelta(seconds=90)}
        )
    )
    for name in POOLS:
        inventory = await client.read_workers_for_work_pool(
            name, worker_filter=recent, limit=2
        )
        if len(inventory) != 1:
            raise DeploymentCheckError("workers_not_ready")
        heartbeat = inventory[0].last_heartbeat_time
        if heartbeat is None or not -5 <= (now - heartbeat).total_seconds() <= 90:
            raise DeploymentCheckError("workers_not_ready")
    await pools(client, "verify")
    await no_execution(client)
