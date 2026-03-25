from __future__ import annotations

import random
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from prefect import flow, task

from sapphire_flow.services.hindcast import run_group_hindcast, run_station_hindcast
from sapphire_flow.types.datetime import UtcDatetime  # noqa: TC001
from sapphire_flow.types.ids import (  # noqa: TC001
    ArtifactId,
    ModelId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.training import HindcastStepResult  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Callable  # noqa: TC003


@task(name="run-station-hindcast-task")
def _run_station_hindcast_task(
    model: object,
    artifact: object,
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    time_step: timedelta,
    forcing_source: object,
    obs_store: object,
    hindcast_store: object,
    station_store: object,
    basin_store: object,
    clock: Callable[[], UtcDatetime],
    rng: random.Random,
    hindcast_run_id: UUID,
) -> list[HindcastStepResult]:
    return run_station_hindcast(
        model=model,
        artifact=artifact,
        station_id=station_id,
        model_id=model_id,
        artifact_id=artifact_id,
        period_start=period_start,
        period_end=period_end,
        time_step=time_step,
        forcing_source=forcing_source,
        obs_store=obs_store,
        hindcast_store=hindcast_store,
        station_store=station_store,
        basin_store=basin_store,
        clock=clock,
        rng=rng,
        hindcast_run_id=hindcast_run_id,
    )


@task(name="run-group-hindcast-task")
def _run_group_hindcast_task(
    model: object,
    artifact: object,
    group: object,
    model_id: ModelId,
    artifact_id: ArtifactId,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    time_step: timedelta,
    forcing_source: object,
    obs_store: object,
    hindcast_store: object,
    station_store: object,
    basin_store: object,
    clock: Callable[[], UtcDatetime],
    rng: random.Random,
    hindcast_run_id: UUID,
) -> dict[StationId, list[HindcastStepResult]]:
    return run_group_hindcast(
        model=model,
        artifact=artifact,
        group=group,
        model_id=model_id,
        artifact_id=artifact_id,
        period_start=period_start,
        period_end=period_end,
        time_step=time_step,
        forcing_source=forcing_source,
        obs_store=obs_store,
        hindcast_store=hindcast_store,
        station_store=station_store,
        basin_store=basin_store,
        clock=clock,
        rng=rng,
        hindcast_run_id=hindcast_run_id,
    )


@flow(name="run-hindcast", log_prints=False)
def run_hindcast_flow(
    model_id: ModelId,
    artifact_id: ArtifactId,
    station_id: StationId | None = None,
    group_id: StationGroupId | None = None,
    period_start: UtcDatetime | None = None,
    period_end: UtcDatetime | None = None,
    time_step: timedelta = timedelta(days=1),
    model: object = None,
    artifact: object = None,
    forcing_source: object = None,
    obs_store: object = None,
    hindcast_store: object = None,
    station_store: object = None,
    basin_store: object = None,
    clock: object = None,
    rng: object = None,
    hindcast_run_id: UUID | None = None,
) -> list[HindcastStepResult] | dict[StationId, list[HindcastStepResult]]:
    from datetime import UTC, datetime

    from sapphire_flow.types.datetime import ensure_utc

    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731
    if rng is None:
        rng = random.Random()
    if hindcast_run_id is None:
        hindcast_run_id = uuid4()

    if period_start is None:
        period_start = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
    if period_end is None:
        period_end = clock()

    if station_id is not None:
        if model is None:
            raise ValueError("model must be provided for station hindcast")
        if artifact is None:
            raise ValueError("artifact must be provided for station hindcast")
        return _run_station_hindcast_task(
            model=model,
            artifact=artifact,
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=period_start,
            period_end=period_end,
            time_step=time_step,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=clock,
            rng=rng,
            hindcast_run_id=hindcast_run_id,
        )
    elif group_id is not None:
        if model is None:
            raise ValueError("model must be provided for group hindcast")
        if artifact is None:
            raise ValueError("artifact must be provided for group hindcast")
        group = station_store.fetch_group(group_id) if station_store else None
        if group is None:
            raise ValueError(f"Group {group_id} not found")
        return _run_group_hindcast_task(
            model=model,
            artifact=artifact,
            group=group,
            model_id=model_id,
            artifact_id=artifact_id,
            period_start=period_start,
            period_end=period_end,
            time_step=time_step,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=clock,
            rng=rng,
            hindcast_run_id=hindcast_run_id,
        )
    else:
        raise ValueError("Either station_id or group_id must be provided")
