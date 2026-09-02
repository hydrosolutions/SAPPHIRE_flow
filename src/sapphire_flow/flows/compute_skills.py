from __future__ import annotations

import os
from collections import defaultdict
from uuid import UUID, uuid4

import structlog
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.skill.combined_skill import (
    compute_bma_skill_cross_validated,
    compute_combined_skill,
)
from sapphire_flow.services.skill.service import (
    compute_skill_for_station,
    observation_fetch_bounds,
    partition_by_time_step_and_phase,
)
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


@task(
    name="compute-skills-task",
    log_prints=False,
    task_run_name="compute-skills-{model_id}-{station_id}-{parameter}",
    cache_policy=NO_CACHE,
)
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

    thresholds = station_store.fetch_thresholds(station_id) if station_store else []
    flow_regime_config = (
        flow_regime_store.fetch_latest(station_id, parameter)
        if flow_regime_store
        else None
    )

    seasons = []
    if deployment_config is not None:
        seasons = deployment_config.get_season_definitions()

    # Plan 228 review fixer round (major): `hindcasts` above is a
    # station/model's ENTIRE unpartitioned history (no `hindcast_run_id`
    # filter, 1970-2100 bounds). `observation_fetch_bounds` /
    # `compute_skill_for_station` hard-raise `ConfigurationError` on any
    # mixed `time_step` or `valid_time` phase within it (Plan 228 D4) — a
    # single differently configured hindcast run (a retraining, a future
    # per-cycle anchoring change) would otherwise take down skill scoring
    # for this station/model permanently, since every future run refetches
    # the same mixed history and raises again. Partition into homogeneous
    # cohorts FIRST and compute skill once per cohort, so a mismatch
    # degrades to "fewer cohorts scored this run", not "none, forever".
    all_scores: list[SkillScore] = []
    all_diagrams: list[SkillDiagram] = []
    for partition in partition_by_time_step_and_phase(hindcasts).values():
        period_start, period_end = observation_fetch_bounds(partition)
        observations = _fetch_observations(
            obs_store, station_id, period_start, period_end, parameter=parameter
        )

        scores, diagrams = compute_skill_for_station(
            station_id=station_id,
            model_id=model_id,
            artifact_id=artifact_id,
            hindcasts=partition,
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
        all_scores.extend(scores)
        all_diagrams.extend(diagrams)

    _store_skill_results(skill_store, all_scores, all_diagrams)

    return all_scores, all_diagrams


@task(
    name="compute-combined-skills-task",
    log_prints=False,
    task_run_name="compute-combined-skills-{station_id}-{parameter}-{strategy.value}",
    cache_policy=NO_CACHE,
)
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

    thresholds = station_store.fetch_thresholds(station_id) if station_store else []
    flow_regime_config = (
        flow_regime_store.fetch_latest(station_id, parameter)
        if flow_regime_store
        else None
    )

    seasons = []
    if deployment_config is not None:
        seasons = deployment_config.get_season_definitions()

    # Plan 228 review fixer round (major): `hindcasts_by_model` above is
    # EVERY combined model's entire unpartitioned history. This is the
    # sharper case of the finding in `compute_skills_task` — it UNIONS
    # hindcasts across every combined model before validating, so a single
    # model with a differently configured hindcast run poisons the
    # observation-bounds fetch for the whole combination, every time it
    # runs. Partition each model's history into homogeneous
    # `(time_step, phase)` cohorts first, then only combine models that
    # share a cohort (still >= 2 of them) — a mismatch degrades to "fewer
    # cohorts combined this run", not "none, forever".
    partitioned_by_key: dict[tuple[object, object], dict[ModelId, list]] = defaultdict(
        dict
    )
    for model_id, hcs in hindcasts_by_model.items():
        for key, partition in partition_by_time_step_and_phase(hcs).items():
            partitioned_by_key[key][model_id] = partition

    all_scores: list[SkillScore] = []
    all_diagrams: list[SkillDiagram] = []
    for per_model_hindcasts in partitioned_by_key.values():
        if len(per_model_hindcasts) < 2:
            continue

        cohort_hindcasts = [hc for hcs in per_model_hindcasts.values() for hc in hcs]
        period_start, period_end = observation_fetch_bounds(cohort_hindcasts)
        observations = _fetch_observations(
            obs_store, station_id, period_start, period_end, parameter=parameter
        )

        if strategy == ModelCombinationStrategy.BMA:
            scores, diagrams = compute_bma_skill_cross_validated(
                station_id=station_id,
                parameter=parameter,
                hindcasts_by_model=per_model_hindcasts,
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
                hindcasts_by_model=per_model_hindcasts,
                observations=observations,
                thresholds=thresholds,
                flow_regime_config=flow_regime_config,
                seasons=seasons,
                skill_source=SkillSource.HINDCAST_REANALYSIS,
                forcing_type=ForcingType.REANALYSIS,
                clock=clock,
                uuid_factory=uuid4,
            )
        all_scores.extend(scores)
        all_diagrams.extend(diagrams)

    _store_skill_results(skill_store, all_scores, all_diagrams)

    return all_scores, all_diagrams


@flow(
    name="compute-combined-skills",
    log_prints=False,
    flow_run_name="compute-combined-skills-{station_id}-{parameter}-{strategy.value}",
)
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
    # --- Production setup ---
    _conn: object = None  # noqa: F841 — GC anchor for bootstrapped DB connection
    if station_store is None:
        from sapphire_flow.flows._db import setup_production_stores

        database_url = os.environ["DATABASE_URL"]
        _conn, stores = setup_production_stores(database_url)
        station_store = stores["station_store"]
        hindcast_store = stores["hindcast_store"]
        obs_store = stores["obs_store"]
        skill_store = stores["skill_store"]
        flow_regime_store = stores["flow_regime_store"]

    if deployment_config is None:
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.deployment import load_config

            deployment_config = load_config(config_path)
        else:
            from sapphire_flow.config.deployment import DeploymentConfig

            deployment_config = DeploymentConfig(max_retention_days=600)

    if hindcast_store is None:
        raise ConfigurationError("hindcast_store is required but was not provided")
    if obs_store is None:
        raise ConfigurationError("obs_store is required but was not provided")
    if skill_store is None:
        raise ConfigurationError("skill_store is required but was not provided")
    if flow_regime_store is None:
        raise ConfigurationError("flow_regime_store is required but was not provided")

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


@flow(
    name="compute-skills",
    log_prints=False,
    flow_run_name="compute-skills-{model_id}-{station_id}-{parameter}",
)
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
    # --- Production setup ---
    _conn: object = None  # noqa: F841 — GC anchor for bootstrapped DB connection
    if station_store is None:
        from sapphire_flow.flows._db import setup_production_stores

        database_url = os.environ["DATABASE_URL"]
        _conn, stores = setup_production_stores(database_url)
        station_store = stores["station_store"]
        hindcast_store = stores["hindcast_store"]
        obs_store = stores["obs_store"]
        skill_store = stores["skill_store"]
        flow_regime_store = stores["flow_regime_store"]

    if deployment_config is None:
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.deployment import load_config

            deployment_config = load_config(config_path)
        else:
            from sapphire_flow.config.deployment import DeploymentConfig

            deployment_config = DeploymentConfig(max_retention_days=600)

    if hindcast_store is None:
        raise ConfigurationError("hindcast_store is required but was not provided")
    if obs_store is None:
        raise ConfigurationError("obs_store is required but was not provided")
    if skill_store is None:
        raise ConfigurationError("skill_store is required but was not provided")
    if flow_regime_store is None:
        raise ConfigurationError("flow_regime_store is required but was not provided")

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
