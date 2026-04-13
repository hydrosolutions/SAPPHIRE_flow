from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from sapphire_flow.services.forecast_combination import (
    combine_ensembles_bma,
    combine_ensembles_pooled,
)
from sapphire_flow.services.skill.bma_weights import compute_bma_weights
from sapphire_flow.services.skill.service import compute_skill_for_station
from sapphire_flow.types.enums import ModelCombinationStrategy
from sapphire_flow.types.ids import BMA_MODEL_ID, POOLED_MODEL_ID, ModelId

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import SeasonDefinition, StationThreshold
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import ForcingType, SkillSource
    from sapphire_flow.types.forecast import HindcastForecast
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.skill import FlowRegimeConfig, SkillDiagram, SkillScore

log = structlog.get_logger(__name__)


def compute_combined_skill(
    station_id: StationId,
    parameter: str,
    strategy: ModelCombinationStrategy,
    hindcasts_by_model: dict[ModelId, list[HindcastForecast]],
    observations: list[Observation],
    thresholds: list[StationThreshold],
    flow_regime_config: FlowRegimeConfig | None,
    seasons: list[SeasonDefinition],
    skill_source: SkillSource,
    forcing_type: ForcingType | None,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
    weights: dict[ModelId, float] | None = None,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    from sapphire_flow.types.forecast import HindcastForecast

    if strategy == ModelCombinationStrategy.PRIMARY:
        return [], []

    if strategy == ModelCombinationStrategy.BMA and weights is None:
        raise ValueError("BMA strategy requires weights")

    is_bma = strategy == ModelCombinationStrategy.BMA
    combined_model_id = BMA_MODEL_ID if is_bma else POOLED_MODEL_ID

    # Compute intersection of hindcast steps across all models
    steps_by_model: dict[ModelId, set[UtcDatetime]] = {
        model_id: {hc.hindcast_step for hc in hindcasts}
        for model_id, hindcasts in hindcasts_by_model.items()
    }

    all_steps: set[UtcDatetime] = set()
    for steps in steps_by_model.values():
        all_steps.update(steps)

    intersection_steps: set[UtcDatetime] = all_steps.copy()
    for steps in steps_by_model.values():
        intersection_steps &= steps

    log.info(
        "combined_skill.coverage",
        total_steps=len(all_steps),
        intersection_steps=len(intersection_steps),
    )

    if not intersection_steps:
        log.warning("combined_skill.no_intersection", strategy=strategy.value)
        return [], []

    # Index hindcasts by step for each model
    hindcasts_by_step: dict[ModelId, dict[UtcDatetime, HindcastForecast]] = {
        model_id: {hc.hindcast_step: hc for hc in hindcasts}
        for model_id, hindcasts in hindcasts_by_model.items()
    }

    combined_hindcasts: list[HindcastForecast] = []
    for step in sorted(intersection_steps):
        ensembles_by_model: dict[ModelId, dict[str, ForecastEnsemble]] = {}
        ref_hindcast: HindcastForecast | None = None

        for model_id, step_lookup in hindcasts_by_step.items():
            hc = step_lookup.get(step)
            if hc is None:
                continue
            ensembles_by_model[model_id] = {hc.ensemble.parameter: hc.ensemble}
            if ref_hindcast is None:
                ref_hindcast = hc

        if ref_hindcast is None or not ensembles_by_model:
            continue

        if is_bma:
            combined = combine_ensembles_bma(ensembles_by_model, weights)  # type: ignore[arg-type]
        else:
            combined = combine_ensembles_pooled(ensembles_by_model)

        ensemble = combined.get(parameter)
        if ensemble is None:
            continue

        combined_hindcasts.append(
            HindcastForecast(
                id=ref_hindcast.id,
                station_id=ref_hindcast.station_id,
                model_id=combined_model_id,
                model_artifact_id=ref_hindcast.model_artifact_id,
                hindcast_step=step,
                forcing_type=ref_hindcast.forcing_type,
                representation=ensemble.representation,
                hindcast_run_id=ref_hindcast.hindcast_run_id,
                ensemble=ensemble,
                created_at=ref_hindcast.created_at,
            )
        )

    return compute_skill_for_station(
        station_id=station_id,
        model_id=combined_model_id,
        artifact_id=None,
        hindcasts=combined_hindcasts,
        observations=observations,
        thresholds=thresholds,
        flow_regime_config=flow_regime_config,
        seasons=seasons,
        skill_source=skill_source,
        forcing_type=forcing_type,
        clock=clock,
        uuid_factory=uuid_factory,
        parameter=parameter,
    )


def compute_bma_skill_cross_validated(
    station_id: StationId,
    parameter: str,
    hindcasts_by_model: dict[ModelId, list[HindcastForecast]],
    observations: list[Observation],
    thresholds: list[StationThreshold],
    flow_regime_config: FlowRegimeConfig | None,
    seasons: list[SeasonDefinition],
    skill_source: SkillSource,
    forcing_type: ForcingType | None,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
    skill_store: object,
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    # Compute intersection of steps across all models
    steps_by_model: dict[ModelId, set[UtcDatetime]] = {
        model_id: {hc.hindcast_step for hc in hindcasts}
        for model_id, hindcasts in hindcasts_by_model.items()
    }

    intersection_steps: set[UtcDatetime] = (
        set.intersection(*steps_by_model.values()) if steps_by_model else set()  # type: ignore[arg-type]
    )
    sorted_steps = sorted(intersection_steps)

    if len(sorted_steps) < 2:
        log.warning("bma_cv.insufficient_steps", n_steps=len(sorted_steps))
        return [], []

    mid = len(sorted_steps) // 2
    half_1 = set(sorted_steps[:mid])
    half_2 = set(sorted_steps[mid:])

    def _filter_hindcasts(
        hcasts_by_model: dict[ModelId, list[HindcastForecast]],
        step_set: set[UtcDatetime],
    ) -> dict[ModelId, list[HindcastForecast]]:
        return {
            mid: [hc for hc in hcs if hc.hindcast_step in step_set]
            for mid, hcs in hcasts_by_model.items()
        }

    def _obs_for_steps(
        step_set: set[UtcDatetime],
    ) -> list[Observation]:
        if not step_set:
            return observations
        min_step = min(step_set)
        max_step = max(step_set)
        return [o for o in observations if min_step <= o.timestamp <= max_step]

    def _scores_for_fold(
        train_hindcasts: dict[ModelId, list[HindcastForecast]],
        eval_hindcasts: dict[ModelId, list[HindcastForecast]],
        eval_obs: list[Observation],
    ) -> tuple[list[SkillScore], list[SkillDiagram]]:
        # Compute per-model skill on training fold to derive weights
        scores_by_model: dict[ModelId, list[SkillScore]] = {}
        for model_id, hcs in train_hindcasts.items():
            train_obs_set: set[UtcDatetime] = set()
            for hc in hcs:
                train_obs_set.add(hc.hindcast_step)
            train_obs = _obs_for_steps({hc.hindcast_step for hc in hcs})
            model_scores, _ = compute_skill_for_station(
                station_id=station_id,
                model_id=model_id,
                artifact_id=None,
                hindcasts=hcs,
                observations=train_obs,
                thresholds=thresholds,
                flow_regime_config=flow_regime_config,
                seasons=seasons,
                skill_source=skill_source,
                forcing_type=forcing_type,
                clock=clock,
                uuid_factory=uuid_factory,
                parameter=parameter,
            )
            scores_by_model[model_id] = model_scores

        fold_weights = compute_bma_weights(
            station_id=station_id,
            parameter=parameter,
            skill_scores_by_model=scores_by_model,
        )

        if not fold_weights:
            return [], []

        return compute_combined_skill(
            station_id=station_id,
            parameter=parameter,
            strategy=ModelCombinationStrategy.BMA,
            hindcasts_by_model=eval_hindcasts,
            observations=eval_obs,
            thresholds=thresholds,
            flow_regime_config=flow_regime_config,
            seasons=seasons,
            skill_source=skill_source,
            forcing_type=forcing_type,
            clock=clock,
            uuid_factory=uuid_factory,
            weights=fold_weights,
        )

    # Fold 1: train on half_1, evaluate on half_2
    fold1_scores, fold1_diagrams = _scores_for_fold(
        train_hindcasts=_filter_hindcasts(hindcasts_by_model, half_1),
        eval_hindcasts=_filter_hindcasts(hindcasts_by_model, half_2),
        eval_obs=_obs_for_steps(half_2),
    )

    # Fold 2: train on half_2, evaluate on half_1
    fold2_scores, fold2_diagrams = _scores_for_fold(
        train_hindcasts=_filter_hindcasts(hindcasts_by_model, half_2),
        eval_hindcasts=_filter_hindcasts(hindcasts_by_model, half_1),
        eval_obs=_obs_for_steps(half_1),
    )

    averaged_scores = _average_skill_scores(
        fold1_scores, fold2_scores, uuid_factory, clock
    )
    averaged_diagrams = fold1_diagrams + fold2_diagrams

    return averaged_scores, averaged_diagrams


def _average_skill_scores(
    scores_a: list[SkillScore],
    scores_b: list[SkillScore],
    uuid_factory: Callable[[], UUID],
    clock: Callable[[], UtcDatetime],
) -> list[SkillScore]:
    from dataclasses import replace

    key_a: dict[tuple[str, int, str | None, object], SkillScore] = {
        (s.metric, s.lead_time_hours, s.season, s.flow_regime): s for s in scores_a
    }
    key_b: dict[tuple[str, int, str | None, object], SkillScore] = {
        (s.metric, s.lead_time_hours, s.season, s.flow_regime): s for s in scores_b
    }

    averaged: list[SkillScore] = []
    all_keys: set[tuple[str, int, str | None, object]] = set(key_a) | set(key_b)
    now = clock()

    for key in all_keys:
        sa = key_a.get(key)
        sb = key_b.get(key)

        if sa is not None and sb is not None:
            avg_score = (sa.score + sb.score) / 2.0
            avg_sample = (sa.sample_size + sb.sample_size) // 2
            averaged.append(
                replace(
                    sa,
                    score=avg_score,
                    sample_size=avg_sample,
                    computed_at=now,
                )
            )
        elif sa is not None:
            averaged.append(replace(sa, computed_at=now))
        elif sb is not None:
            averaged.append(replace(sb, computed_at=now))

    return averaged
