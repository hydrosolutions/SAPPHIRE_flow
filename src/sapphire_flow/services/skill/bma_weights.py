from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from sapphire_flow.types.enums import SkillFreshness

if TYPE_CHECKING:
    from sapphire_flow.types.ids import ModelId
    from sapphire_flow.types.skill import SkillScore

log = structlog.get_logger(__name__)

_EPSILON = 1e-10


def compute_bma_weights(
    station_id: object,
    parameter: str,
    skill_scores_by_model: dict[ModelId, list[SkillScore]],
    metric: str = "crps",
    lead_time_hours: int | None = None,
) -> dict[ModelId, float]:
    best_scores: dict[ModelId, SkillScore] = {}

    for model_id, scores in skill_scores_by_model.items():
        candidates = [
            s
            for s in scores
            if s.metric.lower() == metric.lower()
            and s.freshness == SkillFreshness.CURRENT
            and (lead_time_hours is None or s.lead_time_hours == lead_time_hours)
        ]
        if candidates:
            best_scores[model_id] = max(candidates, key=lambda s: s.computed_at)

    if not best_scores:
        return {}

    raw_weights: dict[ModelId, float] = {
        model_id: 1.0 / (s.score if s.score > 0 else _EPSILON)
        for model_id, s in best_scores.items()
    }

    total = sum(raw_weights.values())
    weights: dict[ModelId, float] = {
        model_id: w / total for model_id, w in raw_weights.items()
    }

    # Models with no matching score get 0.0
    for model_id in skill_scores_by_model:
        if model_id not in weights:
            weights[model_id] = 0.0

    log.info("bma_weights.computed", weights=weights)
    return weights
