from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from prefect import flow, task

from sapphire_flow.services.skill.combined_skill import (
    compute_bma_skill_cross_validated,
    compute_combined_skill,
)
from sapphire_flow.services.skill.service import compute_skill_for_station
from sapphire_flow.types.enums import ForcingType, ModelCombinationStrategy, SkillSource
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId  # noqa: TC001
from sapphire_flow.types.skill import SkillDiagram, SkillScore  # noqa: TC001


def _fetch_hindcasts(
    hindcast_store: object,
    station_id: StationId,
    model_id: ModelId,
    period_start: object,
    period_end: object,
    hindcast_run_id: UUID | None,
    parameter: str,
) -> list:
    return hindcast_store.fetch_hindcasts(
        station_id=station_id,
        model_id=model_id,
        start=period_start,
        end=period_end,
        hindcast_run_id=hindcast_run_id,
        parameter=parameter,
    )


def _fetch_observations(
    obs_store: object,
    station_id: StationId,
    period_start: object,
    period_end: object,
    parameter: str,
) -> list:
    from sapphire_flow.types.enums import QcStatus

    return obs_store.fetch_observations(
        station_id=station_id,
        parameter=parameter,
        start=period_start,
        end=period_end,
        qc_status=QcStatus.QC_PASSED,
    )


def _store_skill_results(
    skill_store: object,
    scores: list[SkillScore],
    diagrams: list[SkillDiagram],
) -> None:
    skill_store.store_skill_scores(scores)
    skill_store.store_skill_diagrams(diagrams)


@task(name="compute-skills-task", log_prints=False)
def compute_skills_task(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    parameter: str,
    hindcast_run_id: UUID | None = None,
    hindcast_store: object = None,
    obs_store: object = None,
    skill_store: object = None,
    station_store: object = None,
    flow_regime_store: object = None,
    deployment_config: object = None,
    clock: object = None,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    from datetime import UTC, datetime

    from sapphire_flow.types.datetime import ensure_utc

    structlog.contextvars.bind_contextvars(
        station_id=str(station_id),
        parameter=parameter,
    )

    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    broad_start = ensure_utc(datetime(1970, 1, 1, tzinfo=UTC))
    broad_end = ensure_utc(datetime(2100, 1, 1, tzinfo=UTC))

    hindcasts = _fetch_hindcasts(
        hindcast_store,
        station_id,
        model_id,
        broad_start,
        broad_end,
        hindcast_run_id,
        parameter=parameter,
    )

    if not hindcasts:
        return [], []

    hindcast_steps = [hc.hindcast_step for hc in hindcasts]
    period_start = min(hindcast_steps)
    period_end = max(hindcast_steps)

    observations = _fetch_observations(
        obs_store, station_id, period_start, period_end, parameter=parameter
    )

    thresholds = station_store.fetch_thresholds(station_id) if station_store else []
    flow_regime_config = (
        flow_regime_store.fetch_latest(station_id, parameter)
        if flow_regime_store
        else None
    )

    seasons = []
    if deployment_config is not None:
        seasons = deployment_config.get_season_definitions()

    scores, diagrams = compute_skill_for_station(
        station_id=station_id,
        model_id=model_id,
        artifact_id=artifact_id,
        hindcasts=hindcasts,
        observations=observations,
        thresholds=thresholds,
        flow_regime_config=flow_regime_config,
        seasons=seasons,
        skill_source=SkillSource.HINDCAST_REANALYSIS,
        forcing_type=ForcingType.REANALYSIS,
        clock=clock,
        uuid_factory=uuid4,
        parameter=parameter,
    )

    _store_skill_results(skill_store, scores, diagrams)

    return scores, diagrams


@task(name="compute-combined-skills-task", log_prints=False)
def compute_combined_skills_task(
    station_id: StationId,
    parameter: str,
    strategy: ModelCombinationStrategy,
    hindcast_store: object = None,
    obs_store: object = None,
    skill_store: object = None,
    station_store: object = None,
    flow_regime_store: object = None,
    deployment_config: object = None,
    clock: object = None,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    from datetime import UTC, datetime
    from uuid import uuid4

    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.enums import SkillSource

    if strategy == ModelCombinationStrategy.PRIMARY:
        return [], []

    structlog.contextvars.bind_contextvars(
        station_id=str(station_id),
        parameter=parameter,
        strategy=strategy.value,
    )

    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    broad_start = ensure_utc(datetime(1970, 1, 1, tzinfo=UTC))
    broad_end = ensure_utc(datetime(2100, 1, 1, tzinfo=UTC))

    hindcasts_by_model = hindcast_store.fetch_hindcasts_by_station(
        station_id=station_id,
        parameter=parameter,
        period_start=broad_start,
        period_end=broad_end,
    )

    if len(hindcasts_by_model) < 2:
        return [], []

    all_steps = sorted(
        {hc.hindcast_step for hcs in hindcasts_by_model.values() for hc in hcs}
    )
    period_start = min(all_steps)
    period_end = max(all_steps)

    observations = _fetch_observations(
        obs_store, station_id, period_start, period_end, parameter=parameter
    )

    thresholds = station_store.fetch_thresholds(station_id) if station_store else []
    flow_regime_config = (
        flow_regime_store.fetch_latest(station_id, parameter)
        if flow_regime_store
        else None
    )

    seasons = []
    if deployment_config is not None:
        seasons = deployment_config.get_season_definitions()

    if strategy == ModelCombinationStrategy.BMA:
        scores, diagrams = compute_bma_skill_cross_validated(
            station_id=station_id,
            parameter=parameter,
            hindcasts_by_model=hindcasts_by_model,
            observations=observations,
            thresholds=thresholds,
            flow_regime_config=flow_regime_config,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,
            uuid_factory=uuid4,
            skill_store=skill_store,
        )
    else:
        scores, diagrams = compute_combined_skill(
            station_id=station_id,
            parameter=parameter,
            strategy=strategy,
            hindcasts_by_model=hindcasts_by_model,
            observations=observations,
            thresholds=thresholds,
            flow_regime_config=flow_regime_config,
            seasons=seasons,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            clock=clock,
            uuid_factory=uuid4,
        )

    _store_skill_results(skill_store, scores, diagrams)

    return scores, diagrams


@flow(name="compute-combined-skills", log_prints=False)
def compute_combined_skills_flow(
    station_id: StationId,
    parameter: str,
    strategy: ModelCombinationStrategy,
    hindcast_store: object = None,
    obs_store: object = None,
    skill_store: object = None,
    station_store: object = None,
    flow_regime_store: object = None,
    deployment_config: object = None,
    clock: object = None,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    return compute_combined_skills_task(
        station_id=station_id,
        parameter=parameter,
        strategy=strategy,
        hindcast_store=hindcast_store,
        obs_store=obs_store,
        skill_store=skill_store,
        station_store=station_store,
        flow_regime_store=flow_regime_store,
        deployment_config=deployment_config,
        clock=clock,
    )


@flow(name="compute-skills", log_prints=False)
def compute_skills_flow(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    parameter: str,
    hindcast_run_id: UUID | None = None,
    hindcast_store: object = None,
    obs_store: object = None,
    skill_store: object = None,
    station_store: object = None,
    flow_regime_store: object = None,
    deployment_config: object = None,
    clock: object = None,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    return compute_skills_task(
        station_id=station_id,
        model_id=model_id,
        artifact_id=artifact_id,
        parameter=parameter,
        hindcast_run_id=hindcast_run_id,
        hindcast_store=hindcast_store,
        obs_store=obs_store,
        skill_store=skill_store,
        station_store=station_store,
        flow_regime_store=flow_regime_store,
        deployment_config=deployment_config,
        clock=clock,
    )
