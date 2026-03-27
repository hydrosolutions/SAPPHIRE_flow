from __future__ import annotations

import random
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from prefect import flow, task
from prefect.utilities.annotations import unmapped

from sapphire_flow.flows.compute_skills import compute_skills_task
from sapphire_flow.flows.run_hindcast import run_hindcast_flow
from sapphire_flow.protocols.forecast_model import (
    GroupForecastModel,
    StationForecastModel,
)
from sapphire_flow.services.model_registry import discover_models, register_models
from sapphire_flow.services.scope import determine_training_scope
from sapphire_flow.services.training import (
    store_and_promote_artifact,
    train_group_model,
    train_station_model,
)
from sapphire_flow.services.training_data import (
    assemble_group_training_data,
    assemble_station_training_data,
)
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.training import TrainingResult, TrainingUnit

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.types.datetime import UtcDatetime


@task(name="determine-scope")
def _determine_scope_task(
    model_ids: list[ModelId] | None,
    station_ids: list[StationId] | None,
    group_ids: list[StationGroupId] | None,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    time_step: timedelta,
    model_store: object,
    station_store: object,
    group_store: object,
) -> object:
    return determine_training_scope(
        model_ids=model_ids,
        station_ids=station_ids,
        group_ids=group_ids,
        period_start=period_start,
        period_end=period_end,
        time_step=time_step,
        model_store=model_store,
        station_store=station_store,
        group_store=group_store,
    )


@task(name="assemble-training-data")
def _assemble_data_task(
    unit: TrainingUnit,
    model: object,
    forcing_source: object,
    obs_store: object,
    basin_store: object,
    station_store: object,
    group_store: object,
) -> object:
    if unit.station_id is not None:
        return assemble_station_training_data(
            station_id=unit.station_id,
            model=model,
            period_start=unit.training_period_start,
            period_end=unit.training_period_end,
            time_step=unit.time_step,
            forcing_source=forcing_source,
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )
    else:
        group = group_store.fetch_group(unit.group_id)
        if group is None:
            return None
        return assemble_group_training_data(
            group=group,
            model=model,
            period_start=unit.training_period_start,
            period_end=unit.training_period_end,
            time_step=unit.time_step,
            forcing_source=forcing_source,
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
        )


@task(name="train-model")
def _train_model_task(
    unit: TrainingUnit,
    model: object,
    data: object,
    rng: random.Random,
) -> bytes:
    params: dict = {}
    if unit.station_id is not None:
        return train_station_model(model=model, data=data, params=params, rng=rng)
    else:
        return train_group_model(model=model, data=data, params=params, rng=rng)


@task(name="store-artifact")
def _store_artifact_task(
    unit: TrainingUnit,
    artifact_bytes: bytes,
    artifact_store: object,
    clock: Callable[[], UtcDatetime],
) -> object:
    return store_and_promote_artifact(
        artifact_store=artifact_store,
        model_id=unit.model_id,
        artifact_bytes=artifact_bytes,
        period_start=unit.training_period_start,
        period_end=unit.training_period_end,
        clock=clock,
        station_id=unit.station_id,
        group_id=unit.group_id,
    )


@flow(name="train-models", log_prints=False)
def train_models_flow(
    model_ids: list[str] | None = None,
    station_ids: list[str] | None = None,
    group_ids: list[str] | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    time_step_hours: int = 24,
    model_store: object = None,
    station_store: object = None,
    group_store: object = None,
    obs_store: object = None,
    basin_store: object = None,
    artifact_store: object = None,
    hindcast_store: object = None,
    skill_store: object = None,
    flow_regime_store: object = None,
    forcing_source: object = None,
    models: dict | None = None,
    clock: object = None,
    rng: object = None,
    deployment_config: object = None,
) -> list[TrainingResult]:
    from datetime import UTC, datetime
    from uuid import UUID

    from sapphire_flow.types.datetime import ensure_utc

    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731
    if rng is None:
        rng = random.Random()

    # Parse period
    if period_start is not None:
        parsed_start: UtcDatetime = ensure_utc(datetime.fromisoformat(period_start))
    else:
        now = clock()
        parsed_start = ensure_utc(
            datetime(now.year - 5, now.month, now.day, tzinfo=UTC)
        )

    if period_end is not None:
        parsed_end: UtcDatetime = ensure_utc(datetime.fromisoformat(period_end))
    else:
        parsed_end = clock()

    time_step = timedelta(hours=time_step_hours)

    typed_model_ids: list[ModelId] | None = (
        [ModelId(m) for m in model_ids] if model_ids is not None else None
    )
    typed_station_ids: list[StationId] | None = None
    if station_ids is not None:
        typed_station_ids = [StationId(UUID(s)) for s in station_ids]
    typed_group_ids: list[StationGroupId] | None = None
    if group_ids is not None:
        typed_group_ids = [StationGroupId(UUID(g)) for g in group_ids]

    # Discover and register models if not pre-provided
    if models is None:
        discovered = discover_models()
        register_models(discovered, model_store, clock)
        models = discovered
    else:
        register_models(models, model_store, clock)

    # T.1: determine scope
    scope = _determine_scope_task(
        model_ids=typed_model_ids,
        station_ids=typed_station_ids,
        group_ids=typed_group_ids,
        period_start=parsed_start,
        period_end=parsed_end,
        time_step=time_step,
        model_store=model_store,
        station_store=station_store,
        group_store=group_store,
    )

    results: list[TrainingResult] = []

    for unit in scope.units:
        model_instance = models.get(unit.model_id)
        if model_instance is None:
            results.append(
                TrainingResult(
                    training_unit=unit,
                    artifact_id=None,
                    hindcast_steps=[],
                    skill_computed=False,
                    error=f"model {unit.model_id} not found in discovered models",
                )
            )
            continue

        # T.2: assemble training data
        data = _assemble_data_task(
            unit=unit,
            model=model_instance,
            forcing_source=forcing_source,
            obs_store=obs_store,
            basin_store=basin_store,
            station_store=station_store,
            group_store=group_store,
        )

        if data is None:
            results.append(
                TrainingResult(
                    training_unit=unit,
                    artifact_id=None,
                    hindcast_steps=[],
                    skill_computed=False,
                    error="insufficient data",
                )
            )
            continue

        # T.3: train and store artifact
        artifact_bytes = _train_model_task(
            unit=unit,
            model=model_instance,
            data=data,
            rng=rng,
        )
        artifact_id = _store_artifact_task(
            unit=unit,
            artifact_bytes=artifact_bytes,
            artifact_store=artifact_store,
            clock=clock,
        )

        # Deserialize artifact for hindcast
        loaded_artifact = model_instance.deserialize_artifact(artifact_bytes)
        hindcast_run_id = uuid4()

        # T.4: run hindcast (as subflow)
        hindcast_steps_raw = run_hindcast_flow(
            model_id=unit.model_id,
            artifact_id=artifact_id,
            station_id=unit.station_id,
            group_id=unit.group_id,
            period_start=unit.training_period_start,
            period_end=unit.training_period_end,
            time_step=unit.time_step,
            model=model_instance,
            artifact=loaded_artifact,
            forcing_source=forcing_source,
            obs_store=obs_store,
            hindcast_store=hindcast_store,
            station_store=station_store,
            basin_store=basin_store,
            clock=clock,
            rng=rng,
            hindcast_run_id=hindcast_run_id,
        )

        # Flatten hindcast results to list[HindcastStepResult]
        if isinstance(hindcast_steps_raw, dict):
            hindcast_steps = [
                step for steps in hindcast_steps_raw.values() for step in steps
            ]
        else:
            hindcast_steps = hindcast_steps_raw

        # T.5: compute skills per station × parameter (task.map fan-out)
        # NOTE: task.map() with unmapped() store args requires in-process task runner
        # (ThreadPoolTaskRunner). Stores hold SQLAlchemy connections that are not
        # pickle-serializable — distributed/subprocess runners would fail.
        skill_computed = False
        station_ids_for_skill: list[StationId] = (
            [unit.station_id] if unit.station_id is not None else list(unit.station_ids)
        )

        assert isinstance(model_instance, (StationForecastModel, GroupForecastModel))
        target_parameters = model_instance.data_requirements.target_parameters
        skill_pairs = [
            (sid, param)
            for sid in station_ids_for_skill
            for param in sorted(target_parameters)
        ]
        futures = compute_skills_task.map(
            station_id=[sid for sid, _ in skill_pairs],
            model_id=unmapped(unit.model_id),
            artifact_id=unmapped(artifact_id),
            parameter=[param for _, param in skill_pairs],
            hindcast_run_id=unmapped(hindcast_run_id),
            hindcast_store=unmapped(hindcast_store),
            obs_store=unmapped(obs_store),
            skill_store=unmapped(skill_store),
            station_store=unmapped(station_store),
            flow_regime_store=unmapped(flow_regime_store),
            deployment_config=unmapped(deployment_config),
            clock=unmapped(clock),
        )
        skill_results = [f.result() for f in futures]
        skill_computed = any(scores for scores, _ in skill_results)

        results.append(
            TrainingResult(
                training_unit=unit,
                artifact_id=artifact_id,
                hindcast_steps=hindcast_steps,
                skill_computed=skill_computed,
                error=None,
            )
        )

    return results
