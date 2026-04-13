from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from sapphire_flow.services.forecast_combination import combine_ensembles_pooled
from sapphire_flow.services.skill.service import compute_skill_for_station
from sapphire_flow.types.enums import ModelCombinationStrategy
from sapphire_flow.types.ids import POOLED_MODEL_ID, ModelId

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
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    from sapphire_flow.types.forecast import HindcastForecast

    if strategy == ModelCombinationStrategy.PRIMARY:
        return [], []

    if strategy == ModelCombinationStrategy.BMA:
        raise NotImplementedError("BMA combination not yet implemented")

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
        # Build ensembles_by_model for this step: model_id -> {parameter -> ensemble}
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

        combined = combine_ensembles_pooled(ensembles_by_model)
        ensemble = combined.get(parameter)
        if ensemble is None:
            continue

        combined_hindcasts.append(
            HindcastForecast(
                id=ref_hindcast.id,
                station_id=ref_hindcast.station_id,
                model_id=POOLED_MODEL_ID,
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
        model_id=POOLED_MODEL_ID,
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
