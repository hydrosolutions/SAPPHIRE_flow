from __future__ import annotations

from typing import TYPE_CHECKING, cast

import structlog

from sapphire_flow.services.forecast_combination import (
    combine_ensembles_bma,
    combine_ensembles_pooled,
)
from sapphire_flow.services.skill.bma_weights import compute_bma_weights
from sapphire_flow.services.skill.service import (
    compute_skill_for_station,
    observation_fetch_bounds,
)
from sapphire_flow.types.enums import ModelCombinationStrategy
from sapphire_flow.types.ids import BMA_MODEL_ID, POOLED_MODEL_ID, ModelId

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
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
    generation_id: UUID | None = None,
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
        generation_id=generation_id,
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
    generation_id: UUID | None = None,
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

    def _obs_for_hindcasts(
        hcs: list[HindcastForecast],
    ) -> list[Observation]:
        """Observations covering ``hcs``' OWN valid-time span (Plan 228
        review fixer round, blocker) — never
        ``[min(hindcast_step), max(hindcast_step)]``, which for a SINGLE
        hindcast collapses to an empty (often zero-observation) range. Both
        one-step folds used to silently score against no observations at
        all; ``observation_fetch_bounds`` derives the bounds from the
        ensembles' own ``valid_time`` and ``time_step`` instead, exactly as
        ``compute_skills_task`` already does for the non-CV path."""
        if not hcs:
            return observations
        start, end = observation_fetch_bounds(hcs)
        return [o for o in observations if start <= o.timestamp < end]

    def _scores_for_fold(
        train_hindcasts: dict[ModelId, list[HindcastForecast]],
        eval_hindcasts: dict[ModelId, list[HindcastForecast]],
        eval_obs: list[Observation],
    ) -> tuple[list[SkillScore], list[SkillDiagram]]:
        # Compute per-model skill on training fold to derive weights
        scores_by_model: dict[ModelId, list[SkillScore]] = {}
        for model_id, hcs in train_hindcasts.items():
            train_obs = _obs_for_hindcasts(hcs)
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
            generation_id=generation_id,
        )

    def _flatten(
        hcasts_by_model: dict[ModelId, list[HindcastForecast]],
    ) -> list[HindcastForecast]:
        return [hc for hcs in hcasts_by_model.values() for hc in hcs]

    eval_hindcasts_half_2 = _filter_hindcasts(hindcasts_by_model, half_2)
    eval_hindcasts_half_1 = _filter_hindcasts(hindcasts_by_model, half_1)

    # Fold 1: train on half_1, evaluate on half_2
    fold1_scores, fold1_diagrams = _scores_for_fold(
        train_hindcasts=_filter_hindcasts(hindcasts_by_model, half_1),
        eval_hindcasts=eval_hindcasts_half_2,
        eval_obs=_obs_for_hindcasts(_flatten(eval_hindcasts_half_2)),
    )

    # Fold 2: train on half_2, evaluate on half_1
    fold2_scores, fold2_diagrams = _scores_for_fold(
        train_hindcasts=_filter_hindcasts(hindcasts_by_model, half_2),
        eval_hindcasts=eval_hindcasts_half_1,
        eval_obs=_obs_for_hindcasts(_flatten(eval_hindcasts_half_1)),
    )

    # Independent-review fixer round (blocker): a two-fold cross-validation
    # average is only meaningful if BOTH folds actually produced output —
    # `_scores_for_fold` can legitimately return `([], [])` on its own (no
    # usable BMA weights from the training half, or too few evaluation
    # steps), and `_average_skill_scores`/`_merge_fold_diagrams` silently
    # fall back to whichever single fold survived, publishing ONE fold's
    # unvalidated result as if it were the two-fold average this function
    # promises. Explicit per-fold completeness status, checked BEFORE
    # combining, so a single-fold result never reaches the publication
    # gate as "combined" — the flow only counts a cohort once this
    # function returns non-empty output (`compute_skills.
    # compute_combined_skills_task`'s `cohorts_combined` increment).
    fold1_complete = bool(fold1_scores or fold1_diagrams)
    fold2_complete = bool(fold2_scores or fold2_diagrams)
    if not (fold1_complete and fold2_complete):
        log.warning(
            "bma_cv.incomplete_fold",
            fold1_complete=fold1_complete,
            fold2_complete=fold2_complete,
        )
        return [], []

    averaged_scores = _average_skill_scores(
        fold1_scores, fold2_scores, uuid_factory, clock
    )
    averaged_diagrams = _merge_fold_diagrams(fold1_diagrams, fold2_diagrams)

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


def _diagram_key(d: SkillDiagram) -> tuple[object, ...]:
    """The diagram natural key (`uq_skill_diagrams_natural_key_generation`
    in `db/metadata.py`), minus `station_id`/`generation_id`/`data`, which
    are either constant across a single `compute_bma_skill_cross_validated`
    call or not part of identity."""
    return (
        d.model_id,
        d.model_artifact_id,
        d.parameter,
        d.skill_source,
        d.computation_version,
        d.lead_time_hours,
        d.season,
        d.flow_regime,
        d.diagram_type,
        d.threshold_level,
        d.time_step_seconds,
        d.phase_offset_seconds,
    )


def _weighted_mean_series(
    series_list: list[Sequence[float]], weights: list[float]
) -> list[float]:
    """Fixer round (major): a per-threshold weighted mean across folds,
    weighted by each fold's own denominator (event count for `hit_rate`,
    non-event count for `false_alarm_rate`) — an UNWEIGHTED mean across
    folds with very different event/non-event counts (e.g. one fold's
    evaluation window has 3 flood events, the other has 30) gives the
    small fold the SAME influence as the large one, which is not what a
    combined ROC curve should show. A fold whose rate is NaN at a given
    threshold (zero denominator for its whole curve, or excluded because
    its weight is 0) is excluded from that threshold's weighted mean.
    """
    merged: list[float] = []
    for vals in zip(*series_list, strict=True):
        pairs = [(v, w) for v, w in zip(vals, weights, strict=True) if w > 0 and v == v]
        total_weight = sum(w for _, w in pairs)
        merged.append(
            sum(v * w for v, w in pairs) / total_weight
            if total_weight > 0
            else float("nan")
        )
    return merged


_DiagramData = dict[str, "Sequence[float]"]


def _merge_diagram_data(
    diagram_type: str, data_list: list[_DiagramData]
) -> _DiagramData:
    """Combine two CV folds' diagnostic data for the SAME natural key into
    one diagram covering the full evaluated period — fold 1 evaluates
    half_2 with weights fit on half_1, fold 2 evaluates half_1 with weights
    fit on half_2, so together they cover every step exactly once. Falls
    back to the first fold's data (rather than raising) if the two folds'
    bin/threshold grids are not directly comparable — that should not
    happen given both folds share the same ensemble member count and fixed
    threshold grids, but a diagram is diagnostic, not authoritative, and
    should never take down a recompute.
    """
    if len(data_list) == 1:
        return data_list[0]

    if diagram_type == "rank_histogram":
        counts_lists: list[Sequence[float]] = [d["counts"] for d in data_list]
        if len({len(c) for c in counts_lists}) != 1:
            return data_list[0]
        return {
            "ranks": data_list[0]["ranks"],
            "counts": [sum(vals) for vals in zip(*counts_lists, strict=True)],
        }

    if diagram_type == "reliability":
        bins_lists: list[Sequence[float]] = [d["bins"] for d in data_list]
        if len({len(b) for b in bins_lists}) != 1:
            return data_list[0]
        rel_counts_lists: list[Sequence[float]] = [
            d["sample_counts"] for d in data_list
        ]
        freq_lists: list[Sequence[float]] = [d["observed_freq"] for d in data_list]
        total_counts = [sum(vals) for vals in zip(*rel_counts_lists, strict=True)]
        merged_freq: list[float] = []
        for per_bin_counts, per_bin_freqs in zip(
            zip(*rel_counts_lists, strict=True),
            zip(*freq_lists, strict=True),
            strict=True,
        ):
            weight = sum(
                c for c, f in zip(per_bin_counts, per_bin_freqs, strict=True) if f == f
            )
            if weight == 0:
                merged_freq.append(float("nan"))
                continue
            weighted = sum(
                c * f
                for c, f in zip(per_bin_counts, per_bin_freqs, strict=True)
                if f == f
            )
            merged_freq.append(weighted / weight)
        return {
            "bins": data_list[0]["bins"],
            "forecast_freq": data_list[0]["forecast_freq"],
            "observed_freq": merged_freq,
            "sample_counts": total_counts,
        }

    if diagram_type == "roc":
        hit_lists: list[Sequence[float]] = [d["hit_rate"] for d in data_list]
        far_lists: list[Sequence[float]] = [d["false_alarm_rate"] for d in data_list]
        if len({len(h) for h in hit_lists}) != 1:
            return data_list[0]
        # Fixer round (major): weight each fold's `hit_rate`/
        # `false_alarm_rate` by its OWN `n_events`/`n_non_events`
        # (`services.skill.diagrams.compute_roc_curve`) rather than
        # averaging the two folds' rates unweighted — an unweighted mean
        # gives a fold with 3 events the same say as one with 300.
        # `.get(..., 0)` is defensive only: every ROC dict this module
        # produces carries both keys.
        event_weights = [float(d.get("n_events", 0)) for d in data_list]  # type: ignore[arg-type]
        non_event_weights = [float(d.get("n_non_events", 0)) for d in data_list]  # type: ignore[arg-type]
        # `_DiagramData` is `dict[str, Sequence[float]]` — `n_events`/
        # `n_non_events` are scalars, not series, so the literal dict below
        # is built as `dict[str, object]` and cast back. The scalars are
        # diagnostic only (mirroring `compute_roc_curve`'s own shape); no
        # reader treats `SkillDiagram.data` as more than a JSONB blob.
        merged_roc: dict[str, object] = {
            "thresholds": data_list[0]["thresholds"],
            "hit_rate": _weighted_mean_series(hit_lists, event_weights),
            "false_alarm_rate": _weighted_mean_series(far_lists, non_event_weights),
            "n_events": sum(event_weights),
            "n_non_events": sum(non_event_weights),
        }
        return cast("_DiagramData", merged_roc)

    return data_list[0]


def _merge_fold_diagrams(
    fold1_diagrams: list[SkillDiagram], fold2_diagrams: list[SkillDiagram]
) -> list[SkillDiagram]:
    """Fixer round (major): the previous `fold1_diagrams + fold2_diagrams`
    concatenation left both folds' diagrams sharing the identical natural
    key (same station/model/parameter/.../diagram_type/threshold_level,
    same `generation_id`) — `store_skill_diagrams`'s `ON CONFLICT DO
    NOTHING` silently dropped one of every pair while `diagram_count`
    still counted both as published. Merge same-key diagrams into one
    BEFORE storing, so what is stored is exactly what is counted.
    """
    from dataclasses import replace

    by_key: dict[tuple[object, ...], list[SkillDiagram]] = {}
    for d in fold1_diagrams + fold2_diagrams:
        by_key.setdefault(_diagram_key(d), []).append(d)

    merged: list[SkillDiagram] = []
    for group in by_key.values():
        first = group[0]
        if len(group) == 1:
            merged.append(first)
            continue
        merged_data = _merge_diagram_data(
            first.diagram_type,
            [cast("_DiagramData", g.data) for g in group],  # type: ignore[reportUnknownMemberType]
        )
        # Fixer round (major): `replace(first, data=...)` kept ONLY
        # `first`'s (fold 1's) `eval_period_start`/`eval_period_end`, even
        # though the merged data spans BOTH folds' evaluation windows
        # (fold 1 evaluates half_2, fold 2 evaluates half_1) — a temporally
        # incorrect bound on an otherwise correctly merged diagram. Union
        # across the whole group instead.
        merged.append(
            replace(
                first,
                data=merged_data,
                eval_period_start=min(g.eval_period_start for g in group),
                eval_period_end=max(g.eval_period_end for g in group),
            )
        )
    return merged
