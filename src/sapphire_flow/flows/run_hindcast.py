from __future__ import annotations

import hashlib
import os
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


@task(
    name="run-station-hindcast-task",
    task_run_name="hindcast-station-{model_id}-{station_id}",
)
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


@task(
    name="run-group-hindcast-task",
    task_run_name="hindcast-group-{model_id}-{group.id}",
)
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


def _resolve_hindcast_run_name() -> str:
    from prefect import runtime

    params = runtime.flow_run.parameters or {}
    model_id = params.get("model_id")
    period_start = params.get("period_start") or runtime.flow_run.scheduled_start_time
    period_end = params.get("period_end")
    name = f"hindcast-{model_id}-{period_start:%Y%m%d}"
    if period_end is not None:
        name = f"{name}-{period_end:%Y%m%d}"
    return name


@flow(
    name="run-hindcast",
    log_prints=False,
    flow_run_name=_resolve_hindcast_run_name,
)
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
    group_store: object = None,
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

    if station_id is None and group_id is None:
        raise ValueError("Either station_id or group_id must be provided")

    # --- Production setup ---
    _conn: object = None
    if station_store is None:
        database_url = os.environ["DATABASE_URL"]

        from sapphire_flow.flows._db import setup_production_stores

        _conn, stores = setup_production_stores(database_url)
        station_store = stores["station_store"]
        group_store = stores["group_store"]
        obs_store = stores["obs_store"]
        hindcast_store = stores["hindcast_store"]
        basin_store = stores["basin_store"]
        artifact_store = stores["artifact_store"]
        forcing_store = stores["forcing_store"]
    else:
        artifact_store = None  # not available when stores are caller-provided
        forcing_store = None

    if forcing_source is None and forcing_store is not None:
        from sapphire_flow.adapters.store_backed_reanalysis import (
            StoreBackedReanalysisSource,
        )

        forcing_source = StoreBackedReanalysisSource(forcing_store)

    if model is None:
        from sapphire_flow.services.model_registry import discover_models

        all_models = discover_models()
        model = all_models.get(model_id)
        if model is None:
            raise ValueError(
                f"Model {model_id} not found in registry. "
                f"Available: {list(all_models.keys())}"
            )

    if artifact is None:
        if artifact_store is None:
            raise ValueError(
                "Cannot resolve artifact: artifact_store is only available "
                "when stores self-resolve from DATABASE_URL. Either pass "
                "artifact explicitly or omit station_store to trigger "
                "full self-resolution."
            )
        result = artifact_store.fetch_artifact(artifact_id)
        if result is None:
            raise ValueError(f"Artifact {artifact_id} not found in store")
        _, artifact_bytes = result

        # SHA-256 integrity verification
        record = artifact_store.fetch_artifact_record(artifact_id)
        if record is not None:
            computed_hash = hashlib.sha256(artifact_bytes).hexdigest()
            if computed_hash != record.sha256_hash:
                raise ValueError(
                    f"SHA-256 mismatch for artifact {artifact_id}: "
                    f"computed={computed_hash[:8]}... "
                    f"stored={record.sha256_hash[:8]}..."
                )

        artifact = model.deserialize_artifact(artifact_bytes)

    _required = {
        "station_store": station_store,
        "obs_store": obs_store,
        "hindcast_store": hindcast_store,
        "basin_store": basin_store,
        "forcing_source": forcing_source,
    }
    if group_id is not None:
        _required["group_store"] = group_store
    _missing = [k for k, v in _required.items() if v is None]
    if _missing:
        raise ValueError(
            f"Required dependencies are None: {_missing}. "
            "Either pass all stores explicitly or omit station_store to "
            "trigger full self-resolution from DATABASE_URL."
        )

    if station_id is not None:
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
        group = group_store.fetch_group(group_id) if group_store else None
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
