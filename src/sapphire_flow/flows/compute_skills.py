from __future__ import annotations

import os
from collections import defaultdict
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import structlog
from prefect import flow, task
from prefect.cache_policies import NO_CACHE

from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    # `clock` stays `object` at runtime (see the Plan 235 note below on why
    # `SkillStore` had to become a real import) — Prefect's pydantic
    # schema-builder would otherwise need to resolve `Callable[[],
    # UtcDatetime]` too. These two are used only inside a `cast(...)`
    # string annotation to type-check the one place this module calls
    # `clock()` directly.
    from collections.abc import Callable

    from sapphire_flow.types.datetime import UtcDatetime

# Plan 235: a REAL (not TYPE_CHECKING-only) import — these `@flow`/`@task`
# functions have their parameters validated by Prefect via pydantic at
# runtime, which needs to resolve every annotation, including this one.
# `SkillStore | None` under TYPE_CHECKING alone made `Compute_Skills_
# FlowModel`/`Compute_Combined_Skills_FlowModel` fail to build
# ("SkillStore" not fully defined) the moment the flow (not `.fn()`) was
# actually invoked — caught by `test_flow_wrapper_delegates_to_task`.
# Fixer round (major): `HindcastStore` needs the SAME real (not
# TYPE_CHECKING-only) import as `SkillStore` above — Prefect validates
# every `@task` annotation via pydantic at runtime, and typing
# `compute_combined_skills_task`'s `hindcast_store` as bare `object` meant
# a store that dropped or broke `fetch_hindcasts_by_station` (e.g. the
# `hindcast_run_ids={}` fixer-round bug) could not be caught by static
# checking at all.
from sapphire_flow.protocols.stores import HindcastStore, SkillStore  # noqa: TC001
from sapphire_flow.services.skill.combined_skill import (
    compute_bma_skill_cross_validated,
    compute_combined_skill,
)
from sapphire_flow.services.skill.service import (
    compute_skill_for_station,
    observation_fetch_bounds,
    partition_by_time_step_and_phase,
    rebind_generation_id,
    resolve_generation_id,
    store_skill_results_or_raise,
)
from sapphire_flow.types.enums import ForcingType, ModelCombinationStrategy, SkillSource
from sapphire_flow.types.ids import (  # noqa: TC001
    BMA_MODEL_ID,
    POOLED_MODEL_ID,
    ArtifactId,
    ModelId,
    StationId,
)
from sapphire_flow.types.skill import SkillDiagram, SkillScore  # noqa: TC001

log = structlog.get_logger(__name__)


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


# Plan 235 per-run scope: the store-and-reconcile logic (D1 retry
# stability) is now shared with `services.onboarding` — see
# `services.skill.service.store_skill_results_or_raise` for the full
# rationale. Kept as a thin local alias so this module's call sites (and
# their existing tests, which patch/inspect via this module's namespace)
# do not need to change shape.
_store_skill_results = store_skill_results_or_raise


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
    hindcast_run_id: UUID,
    hindcast_store: object = None,
    obs_store: object = None,
    skill_store: SkillStore | None = None,
    station_store: object = None,
    flow_regime_store: object = None,
    deployment_config: object = None,
    clock: object = None,
    generation_id: UUID | None = None,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    from datetime import UTC, datetime

    from sapphire_flow.types.datetime import ensure_utc

    structlog.contextvars.bind_contextvars(
        station_id=str(station_id),
        parameter=parameter,
    )

    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    # Plan 235 D1: one generation identity per task invocation, shared by
    # every cohort this call scores. Fixer round (major): `generation_id`
    # must be minted OUTSIDE this function body to be retry-stable —
    # minting it here on a `None` default means every Prefect retry
    # re-executes this same branch from scratch and mints a DIFFERENT
    # uuid4() each time, since retries re-run the function body, not just
    # resume it. `compute_skills_flow` (and the `.map()` fan-outs in
    # `flows/train_models.py`/`flows/onboard_model.py`) now always mint one
    # BEFORE calling this task and pass it explicitly, so a retry of the
    # task replays with the identical bound argument. This fallback only
    # fires for a caller that invokes the task directly without going
    # through the flow (e.g. `.fn()` in tests) — a brand new invocation is
    # still free to mint a new one, per D2d ("no write-time rejection" —
    # overlap is expected and resolved by readers, not by detecting
    # whether inputs actually changed).
    if generation_id is None:
        generation_id = uuid4()
    # Plan 235 per-run scope (blocker #3): what the flow minted is an
    # INVOCATION id, not the final generation id — see `resolve_generation_
    # id` below. Kept under the parameter's original name so nothing
    # upstream of this task (the flow, `.map()` fan-outs, direct `.fn()`
    # callers) needs to change.
    invocation_id = generation_id

    # Plan 235 per-run scope (blocker #4, two-release rollout), tightened
    # by the independent-review fixer round: while
    # `deployment_config.enable_skill_generations` is off, this task stays
    # on the pre-235 shape entirely — baseline rows (`generation_id=NULL`),
    # no `skill_generations` publish. That is what makes a ROLLBACK safe
    # mid-rollout: the schema and every generation-AWARE reader (T2) ship
    # first while writes stay generation-blind; only once the fleet is
    # confirmed on an image that already understands generations does a
    # second release flip this flag and start tagging writes. A rolled-back
    # image reading rows this task wrote is then reading exactly what it
    # always has, never a mix it cannot interpret.
    # Defaults to DISABLED (matching `DeploymentConfig.enable_skill_
    # generations`'s own default) — a bare `deployment_config=None` (every
    # caller that omits it entirely, including a direct `.fn()` test) gets
    # the Release-A baseline shape. An operator opts INTO generation-tagged
    # writes only for Release B, by explicitly setting the field `true` in
    # their deployment config once every rollback image is
    # generation-aware — never the other way around.
    generations_enabled = bool(
        getattr(deployment_config, "enable_skill_generations", False)
    )

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
    # Plan 235 per-run scope (blocker #1): `rejected_hindcasts` counts
    # hindcasts silently dropped DURING partitioning (an internally
    # mixed-phase hindcast) — never reaching a cohort at all. Folded into
    # `cohorts_complete` below alongside `cohorts_missing`, which only ever
    # saw cohorts that survived partitioning.
    cohorts, rejected_hindcasts = partition_by_time_step_and_phase(hindcasts)
    observations_fetched = 0
    # Fixer round (blocker, D3 completeness): counts cohorts that produced
    # NOTHING despite having hindcasts to score — a malformed hindcast, a
    # cohort with zero overlapping observations, or any other silent
    # per-cohort failure. `cohorts_complete` gates publication below: a run
    # that scored every partitioned cohort publishes; a run that silently
    # dropped even one does not, so it can never supersede-and-hide a
    # previously COMPLETE generation for this scope with a partial one.
    cohorts_missing = 0
    for partition in cohorts.values():
        period_start, period_end = observation_fetch_bounds(partition)
        observations = _fetch_observations(
            obs_store, station_id, period_start, period_end, parameter=parameter
        )
        observations_fetched += len(observations)

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
            generation_id=invocation_id if generations_enabled else None,
        )
        if not scores and not diagrams:
            cohorts_missing += 1
            log.warning(
                "skill.compute_skills_task.cohort_produced_nothing",
                cohort_hindcasts=len(partition),
                observations=len(observations),
            )
        all_scores.extend(scores)
        all_diagrams.extend(diagrams)

    cohorts_complete = cohorts_missing == 0 and rejected_hindcasts == 0

    if not generations_enabled:
        # Two-release rollout, Release A: legacy shape only — store
        # baseline rows and stop. No fingerprinting, no reconciliation
        # against a generation id (there is none), no publish.
        if (all_scores or all_diagrams) and skill_store is not None:
            skill_store.store_skill_scores(all_scores)
            skill_store.store_skill_diagrams(all_diagrams)
        log.info(
            "skill.compute_skills_task.summary",
            hindcasts_fetched=len(hindcasts),
            cohorts=len(cohorts),
            cohorts_missing=cohorts_missing,
            rejected_hindcasts=rejected_hindcasts,
            observations_fetched=observations_fetched,
            scores_stored=len(all_scores),
            diagrams_stored=len(all_diagrams),
            generations_enabled=False,
            published=False,
        )
        return all_scores, all_diagrams

    # Plan 235 per-run scope (blocker #3): the FINAL generation id is
    # derived from the invocation id plus a content fingerprint of what was
    # actually computed — never the raw `invocation_id` — so a retry whose
    # recomputed values differ from an earlier, orphaned attempt (e.g.
    # observations were corrected in between) writes under a DIFFERENT
    # generation instead of losing to `ON CONFLICT DO NOTHING` against the
    # stale one. An unchanged-input retry derives the SAME id, so it stays
    # a safe no-op collision. Scores/diagrams were built above against
    # `invocation_id` as a placeholder (the fingerprint needs their
    # computed VALUES, which do not exist until now) and are rebound here.
    if all_scores or all_diagrams:
        generation_id = resolve_generation_id(invocation_id, all_scores, all_diagrams)
        all_scores, all_diagrams = rebind_generation_id(
            all_scores, all_diagrams, generation_id
        )
    else:
        generation_id = invocation_id

    if all_scores or all_diagrams:
        _store_skill_results(skill_store, generation_id, all_scores, all_diagrams)

    # Plan 235 D3: publish ONLY after every score/diagram write above
    # already succeeded (`_store_skill_results` raises otherwise) AND every
    # expected cohort actually produced output (fixer round, blocker) —
    # this INSERT is what makes `generation_id` current
    # (`PgSkillStore.publish_generation`). Skipped when there is nothing to
    # publish (no cohort scored) and skipped when any cohort came back
    # empty or any hindcast was rejected during partitioning (blocker #1)
    # — either way, a data-free or degraded/partial run never supersedes a
    # previously published, complete generation for this scope.
    if cohorts_complete and (all_scores or all_diagrams) and skill_store is not None:
        # `computation_version` is a real algorithm-version value read off
        # a produced row rather than importing the private
        # `_COMPUTATION_VERSION` again here. `published_at`, by contrast,
        # must be the actual publication instant (fixer round, major) — it
        # used to reuse `all_scores[0].computed_at`, the FIRST cohort's
        # compute time, which can predate later cohorts finishing and so
        # can misorder D2b#1 precedence against a concurrent publication.
        if all_scores:
            computation_version = all_scores[0].computation_version
        else:
            computation_version = all_diagrams[0].computation_version
        published_at = cast("Callable[[], UtcDatetime]", clock)()
        skill_store.publish_generation(
            generation_id=generation_id,
            station_id=station_id,
            model_id=model_id,
            model_artifact_id=artifact_id,
            parameter=parameter,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            computation_version=computation_version,
            published_at=published_at,
            score_count=len(all_scores),
            diagram_count=len(all_diagrams),
        )

    # Plan 228 fixer round (major): a task-level summary so a silent
    # zero-rows-stored run is visible without cross-referencing per-cohort
    # warnings from `compute_skill_for_station`.
    log.info(
        "skill.compute_skills_task.summary",
        hindcasts_fetched=len(hindcasts),
        cohorts=len(cohorts),
        cohorts_missing=cohorts_missing,
        rejected_hindcasts=rejected_hindcasts,
        observations_fetched=observations_fetched,
        scores_stored=len(all_scores),
        diagrams_stored=len(all_diagrams),
        generation_id=str(generation_id),
        generations_enabled=True,
        published=cohorts_complete and bool(all_scores or all_diagrams),
    )

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
    hindcast_run_ids: dict[ModelId, UUID],
    hindcast_store: HindcastStore | None = None,
    obs_store: object = None,
    skill_store: SkillStore | None = None,
    station_store: object = None,
    flow_regime_store: object = None,
    deployment_config: object = None,
    clock: object = None,
    generation_id: UUID | None = None,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    from datetime import UTC, datetime
    from uuid import uuid4

    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.enums import SkillSource

    if strategy == ModelCombinationStrategy.PRIMARY:
        return [], []

    # Fixer round (blocker): `hindcast_run_ids` is required (no default) so
    # a caller cannot OMIT it — but nothing stopped a caller from passing an
    # explicitly EMPTY mapping, which named the SAME `PgHindcastStore`
    # fallback-to-unscoped bug fixed in `fetch_hindcasts_by_station`. Reject
    # it here too, at the task boundary, rather than relying solely on the
    # store to treat `{}` as "no matches" (defense in depth — a combination
    # strategy fundamentally needs at least 2 models' runs to combine).
    if not hindcast_run_ids:
        raise ConfigurationError(
            "hindcast_run_ids must not be empty for POOLED/BMA combination "
            "— an empty mapping names zero models to combine"
        )

    structlog.contextvars.bind_contextvars(
        station_id=str(station_id),
        parameter=parameter,
        strategy=strategy.value,
    )

    if clock is None:
        clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    # Plan 235 D1 — fixer round: see `compute_skills_task`'s identical note
    # on why this is only a fallback for a direct `.fn()` caller; the flow
    # mints and threads the real, retry-stable id.
    if generation_id is None:
        generation_id = uuid4()
    # Plan 235 per-run scope (blocker #3): see `compute_skills_task`'s
    # identical note — this is an INVOCATION id, not the final generation
    # id, which is derived below from a content fingerprint.
    invocation_id = generation_id

    # Plan 235 per-run scope (blocker #4): see `compute_skills_task`'s
    # identical note — default DISABLED, matching Release A.
    generations_enabled = bool(
        getattr(deployment_config, "enable_skill_generations", False)
    )

    broad_start = ensure_utc(datetime(1970, 1, 1, tzinfo=UTC))
    broad_end = ensure_utc(datetime(2100, 1, 1, tzinfo=UTC))

    assert hindcast_store is not None
    hindcasts_by_model = hindcast_store.fetch_hindcasts_by_station(
        station_id=station_id,
        parameter=parameter,
        period_start=broad_start,
        period_end=broad_end,
        hindcast_run_ids=hindcast_run_ids,
    )

    # Fixer round (blocker, D3 completeness): `fetch_hindcasts_by_station`
    # simply OMITS a requested model's key when it has no matching hindcast
    # rows (an orphaned header, a run id that never landed) — it never
    # returns an empty list for it. A caller that asked for N models but
    # got fewer back is a DEGRADED input, not "combine whatever showed up
    # and publish it as complete" — that would silently supersede-and-hide
    # a previous, genuinely complete combination.
    missing_requested_models = set(hindcast_run_ids) - set(hindcasts_by_model)
    if missing_requested_models:
        log.warning(
            "skill.compute_combined_skills_task.missing_requested_models",
            missing_models=sorted(str(m) for m in missing_requested_models),
            requested=sorted(str(m) for m in hindcast_run_ids),
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
    # Plan 235 per-run scope (blocker #1): `rejected_hindcasts` accumulates
    # hindcasts dropped DURING partitioning (an internally mixed-phase
    # hindcast) across every combined model — folded into `cohorts_complete`
    # below, same as `compute_skills_task`.
    partitioned_by_key: dict[tuple[object, object], dict[ModelId, list]] = defaultdict(
        dict
    )
    rejected_hindcasts = 0
    for model_id, hcs in hindcasts_by_model.items():
        model_cohorts, rejected = partition_by_time_step_and_phase(hcs)
        rejected_hindcasts += rejected
        for key, partition in model_cohorts.items():
            partitioned_by_key[key][model_id] = partition

    # Plan 235 per-run scope (blocker #2): the set of models THIS
    # combination was actually asked for. A cohort must carry EVERY one of
    # them, not merely >= 2 — "any cohort with two models where three were
    # requested" (A+B and B+C both 'satisfying' A/B/C) is exactly the
    # defect this closes.
    requested_models = set(hindcast_run_ids)

    all_scores: list[SkillScore] = []
    all_diagrams: list[SkillDiagram] = []
    observations_fetched = 0
    # Plan 235 per-run scope (blocker #2): incremented only AFTER a cohort
    # carrying the full requested model set produces NON-EMPTY output —
    # never merely on being attempted. `pooled`/`bma` can legitimately
    # return `([], [])` (e.g. no fold produced usable weights); that must
    # not count as a combined cohort either.
    cohorts_combined = 0
    for per_model_hindcasts in partitioned_by_key.values():
        if set(per_model_hindcasts) != requested_models:
            continue

        cohort_hindcasts = [hc for hcs in per_model_hindcasts.values() for hc in hcs]
        period_start, period_end = observation_fetch_bounds(cohort_hindcasts)
        observations = _fetch_observations(
            obs_store, station_id, period_start, period_end, parameter=parameter
        )
        observations_fetched += len(observations)

        combine_generation_id = invocation_id if generations_enabled else None
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
                generation_id=combine_generation_id,
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
                generation_id=combine_generation_id,
            )
        if not scores and not diagrams:
            log.warning(
                "skill.compute_combined_skills_task.cohort_produced_nothing",
                cohort_models=sorted(str(m) for m in per_model_hindcasts),
                observations=len(observations),
            )
            continue
        cohorts_combined += 1
        all_scores.extend(scores)
        all_diagrams.extend(diagrams)

    # Fixer round (blocker, D3 completeness), per-run-scope blockers #1/#2:
    # every requested model must have been present, no hindcast may have
    # been rejected during partitioning, and every candidate (time_step,
    # phase) bucket must have carried the FULL requested model set AND
    # produced non-empty output for this to count as a complete
    # combination — otherwise a mismatch degrades to "publish nothing this
    # run" (the previous, complete generation stays visible), not "publish
    # whatever subset happened to line up".
    cohorts_complete = (
        not missing_requested_models
        and rejected_hindcasts == 0
        and cohorts_combined == len(partitioned_by_key)
    )

    if not generations_enabled:
        # Two-release rollout, Release A: see `compute_skills_task`'s
        # identical branch.
        if (all_scores or all_diagrams) and skill_store is not None:
            skill_store.store_skill_scores(all_scores)
            skill_store.store_skill_diagrams(all_diagrams)
        log.info(
            "skill.compute_combined_skills_task.summary",
            candidate_cohorts=len(partitioned_by_key),
            cohorts_combined=cohorts_combined,
            rejected_hindcasts=rejected_hindcasts,
            missing_requested_models=sorted(str(m) for m in missing_requested_models),
            observations_fetched=observations_fetched,
            scores_stored=len(all_scores),
            diagrams_stored=len(all_diagrams),
            generations_enabled=False,
            published=False,
        )
        return all_scores, all_diagrams

    # Plan 235 per-run scope (blocker #3): see `compute_skills_task`'s
    # identical note — derive the FINAL generation id from a content
    # fingerprint of what was actually computed, and rebind every
    # score/diagram (built above against `invocation_id`) to it.
    if all_scores or all_diagrams:
        generation_id = resolve_generation_id(invocation_id, all_scores, all_diagrams)
        all_scores, all_diagrams = rebind_generation_id(
            all_scores, all_diagrams, generation_id
        )
    else:
        generation_id = invocation_id

    if all_scores or all_diagrams:
        _store_skill_results(skill_store, generation_id, all_scores, all_diagrams)

    # Plan 235 D3: see `compute_skills_task` — publish only after every
    # write above already succeeded (`_store_skill_results` raises
    # otherwise) AND the combination was complete (fixer round, blocker).
    # `combined_model_id` mirrors `combined_skill.compute_combined_skill`'s
    # own strategy → model_id mapping (BMA gets its own sentinel;
    # everything else here is POOLED); combined scores/diagrams always
    # carry `model_artifact_id=None` (`services/skill/combined_skill.py`).
    if cohorts_complete and (all_scores or all_diagrams) and skill_store is not None:
        combined_model_id = (
            BMA_MODEL_ID
            if strategy == ModelCombinationStrategy.BMA
            else POOLED_MODEL_ID
        )
        # See compute_skills_task's identical note on why `published_at`
        # is the actual publication instant (`clock()`), not a reused
        # per-cohort `computed_at`/`created_at`, while `computation_version`
        # stays a real value read off a produced row.
        if all_scores:
            computation_version = all_scores[0].computation_version
        else:
            computation_version = all_diagrams[0].computation_version
        published_at = cast("Callable[[], UtcDatetime]", clock)()
        skill_store.publish_generation(
            generation_id=generation_id,
            station_id=station_id,
            model_id=combined_model_id,
            model_artifact_id=None,
            parameter=parameter,
            skill_source=SkillSource.HINDCAST_REANALYSIS,
            forcing_type=ForcingType.REANALYSIS,
            computation_version=computation_version,
            published_at=published_at,
            score_count=len(all_scores),
            diagram_count=len(all_diagrams),
        )

    # Plan 228 fixer round (major): task-level summary — see
    # `compute_skills_task.summary` above.
    log.info(
        "skill.compute_combined_skills_task.summary",
        candidate_cohorts=len(partitioned_by_key),
        cohorts_combined=cohorts_combined,
        rejected_hindcasts=rejected_hindcasts,
        missing_requested_models=sorted(str(m) for m in missing_requested_models),
        observations_fetched=observations_fetched,
        scores_stored=len(all_scores),
        diagrams_stored=len(all_diagrams),
        generation_id=str(generation_id),
        generations_enabled=True,
        published=cohorts_complete and bool(all_scores or all_diagrams),
    )

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
    hindcast_run_ids: dict[ModelId, UUID],
    hindcast_store: object = None,
    obs_store: object = None,
    skill_store: SkillStore | None = None,
    station_store: object = None,
    flow_regime_store: object = None,
    deployment_config: object = None,
    clock: object = None,
    generation_id: UUID | None = None,
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
        skill_store = cast("SkillStore", stores["skill_store"])
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

    # Fixer round (major, D1 retry stability): mint the generation id HERE,
    # in the flow, rather than leaving it to the task's own `None`-default
    # fallback — a flow invocation happens once, so this mint happens once;
    # a Prefect retry of `compute_combined_skills_task` below replays with
    # this SAME already-bound argument instead of re-entering the task
    # body's `if generation_id is None: generation_id = uuid4()` branch and
    # minting a new one on every retry.
    if generation_id is None:
        generation_id = uuid4()

    return compute_combined_skills_task(
        station_id=station_id,
        parameter=parameter,
        strategy=strategy,
        hindcast_run_ids=hindcast_run_ids,
        hindcast_store=cast("HindcastStore", hindcast_store),
        obs_store=obs_store,
        skill_store=skill_store,
        station_store=station_store,
        flow_regime_store=flow_regime_store,
        deployment_config=deployment_config,
        clock=clock,
        generation_id=generation_id,
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
    hindcast_run_id: UUID,
    hindcast_store: object = None,
    obs_store: object = None,
    skill_store: SkillStore | None = None,
    station_store: object = None,
    flow_regime_store: object = None,
    deployment_config: object = None,
    clock: object = None,
    generation_id: UUID | None = None,
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
        skill_store = cast("SkillStore", stores["skill_store"])
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

    # Fixer round (major, D1 retry stability): see the identical note in
    # `compute_combined_skills_flow` — mint once, here, so a Prefect retry
    # of the task below replays with this same bound argument.
    if generation_id is None:
        generation_id = uuid4()

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
        generation_id=generation_id,
    )
