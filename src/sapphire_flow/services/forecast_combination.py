from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import structlog

from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastStatus,
    InputQualityLevel,
    ModelCombinationStrategy,
    QcStatus,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import (
    BMA_MODEL_ID,
    POOLED_MODEL_ID,
    ForecastId,
    ModelId,
    StationId,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, timedelta
    from uuid import UUID

    from sapphire_flow.services.run_station_forecast import MultiModelForecastResult
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import NwpCycleSource

_MIN_POOLED_CONTRIBUTORS = 2
_MIN_PERSISTED_TIMESTAMPS = 2

log = structlog.get_logger()

_BMA_TARGET_MEMBERS = 100


def combine_ensembles_pooled(
    ensembles: dict[ModelId, dict[str, ForecastEnsemble]],
) -> dict[str, ForecastEnsemble]:
    """Plan 222 T3 (D2) — pools only where every contributor is present.

    Two model families can sit on different `valid_time` grids (the NWP
    pair's UTC-midnight buckets vs. `linear_regression_daily`'s wall-clock
    offsets, `docs/plans/222-pooled-grid-alignment.md`). Concatenating
    across the union of those grids — the pre-Plan-222 behaviour — makes
    `_ensemble_points()` summarise each timestamp over whichever rows
    happen to carry it, so the member count (and therefore the median)
    alternates timestamp to timestamp: the reported sawtooth.

    A parameter is published only when at least two models contribute a
    `MEMBERS` ensemble for it, and only at the `valid_time`s every one of
    those contributors carries — the intersection, not the union. Fewer
    than two contributors, or an empty intersection, drops the parameter
    entirely; it is never silently narrowed to a smaller pool.
    """
    from sapphire_flow.types.ensemble import ForecastEnsemble

    all_params: set[str] = set()
    for param_map in ensembles.values():
        all_params.update(param_map.keys())

    result: dict[str, ForecastEnsemble] = {}
    for param in all_params:
        eligible: list[tuple[ModelId, ForecastEnsemble]] = []
        for model_id, param_map in ensembles.items():
            ensemble = param_map.get(param)
            if ensemble is None:
                continue
            if ensemble.representation != EnsembleRepresentation.MEMBERS:
                log.warning(
                    "forecast_combination.skip_non_members",
                    model_id=str(model_id),
                    parameter=param,
                    representation=ensemble.representation.value,
                )
                continue
            eligible.append((model_id, ensemble))

        if len(eligible) < _MIN_POOLED_CONTRIBUTORS:
            log.warning(
                "forecast_combination.pooled_insufficient_contributors",
                parameter=param,
                contributor_count=len(eligible),
            )
            continue

        common_valid_times: set[datetime] | None = None
        for model_id, ensemble in eligible:
            n_members = ensemble.values["member_id"].n_unique()
            # D2 — completeness is counted BOTH ways: row count AND
            # `n_unique(member_id)` must each equal the contributor's total
            # member count. `n_unique` alone lets a duplicate
            # `(valid_time, member_id)` row pass (neither
            # `ForecastEnsemble.from_members` nor the database enforces
            # uniqueness of that pair), and `_ensemble_points()` then
            # summarises every row, overweighting the duplicated member.
            per_valid_time = ensemble.values.group_by("valid_time").agg(
                pl.len().alias("n_rows"),
                pl.col("member_id").n_unique().alias("n_members"),
            )
            complete_valid_times = set(
                per_valid_time.filter(
                    (pl.col("n_members") == n_members) & (pl.col("n_rows") == n_members)
                )["valid_time"].to_list()
            )
            ragged_count = per_valid_time.height - len(complete_valid_times)
            if ragged_count:
                log.warning(
                    "forecast_combination.pooled_ragged_contributor",
                    model_id=str(model_id),
                    parameter=param,
                    ragged_valid_time_count=ragged_count,
                    complete_valid_time_count=len(complete_valid_times),
                )
            common_valid_times = (
                complete_valid_times
                if common_valid_times is None
                else common_valid_times & complete_valid_times
            )

        if not common_valid_times:
            log.warning(
                "forecast_combination.pooled_empty_intersection",
                parameter=param,
                contributor_count=len(eligible),
            )
            continue

        member_dfs: list[pl.DataFrame] = []
        ref_ensemble: ForecastEnsemble | None = None
        offset = 0

        for _model_id, ensemble in eligible:
            n_members = ensemble.values["member_id"].n_unique()
            filtered = ensemble.values.filter(
                pl.col("valid_time").is_in(common_valid_times)
            )
            remapped = filtered.with_columns(
                (pl.col("member_id") + offset).alias("member_id")
            )
            member_dfs.append(remapped)
            offset += n_members
            ref_ensemble = ensemble

        if not member_dfs or ref_ensemble is None:
            # Unreachable: `eligible` has >= _MIN_POOLED_CONTRIBUTORS
            # entries by the guard above, so the loop always runs. Kept
            # for the same reason as `combine_ensembles_bma`'s identical
            # guard — an explicit narrowing pyright can follow, not a
            # live branch.
            continue

        merged = pl.concat(member_dfs)
        result[param] = ForecastEnsemble.from_members(
            station_id=ref_ensemble.station_id,
            issued_at=ref_ensemble.issued_at,
            parameter=param,
            units=ref_ensemble.units,
            time_step=ref_ensemble.time_step,
            values=merged,
            model_id=POOLED_MODEL_ID,
        )

    return result


def combine_ensembles_bma(
    ensembles: dict[ModelId, dict[str, ForecastEnsemble]],
    weights: dict[ModelId, float],
) -> dict[str, ForecastEnsemble]:
    from sapphire_flow.types.ensemble import ForecastEnsemble

    # Filter to models with weight > 0 and MEMBERS representation
    eligible: dict[ModelId, dict[str, ForecastEnsemble]] = {}
    for model_id, param_map in ensembles.items():
        w = weights.get(model_id, 0.0)
        if w <= 0.0:
            continue
        members_only = {
            p: ens
            for p, ens in param_map.items()
            if ens.representation == EnsembleRepresentation.MEMBERS
        }
        skipped_params = set(param_map) - set(members_only)
        for p in skipped_params:
            log.warning(
                "forecast_combination.bma_skip_non_members",
                model_id=str(model_id),
                parameter=p,
                representation=param_map[p].representation.value,
            )
        if members_only:
            eligible[model_id] = members_only

    if not eligible:
        return {}

    all_params: set[str] = set()
    for param_map in eligible.values():
        all_params.update(param_map.keys())

    # Compute per-model sample counts
    eligible_weights = {mid: weights[mid] for mid in eligible}
    total_weight = sum(eligible_weights.values())
    if total_weight == 0.0:
        return {}
    normalised = {mid: w / total_weight for mid, w in eligible_weights.items()}

    raw_counts = {mid: round(w * _BMA_TARGET_MEMBERS) for mid, w in normalised.items()}
    # Ensure at least 1 per model
    counts = {mid: max(1, c) for mid, c in raw_counts.items()}

    # Adjust total to exactly _BMA_TARGET_MEMBERS (add/remove from highest-weight model)
    total = sum(counts.values())
    if total != _BMA_TARGET_MEMBERS:
        heaviest = max(eligible_weights, key=lambda m: eligible_weights[m])
        counts[heaviest] += _BMA_TARGET_MEMBERS - total

    result: dict[str, ForecastEnsemble] = {}
    for param in all_params:
        member_dfs: list[pl.DataFrame] = []
        ref_ensemble: ForecastEnsemble | None = None
        global_offset = 0

        for model_id, param_map in eligible.items():
            ensemble = param_map.get(param)
            if ensemble is None:
                continue

            n_sample = counts[model_id]
            unique_members = ensemble.values["member_id"].unique().sort().to_list()
            seed = int(abs(hash(str(model_id)))) % (2**31)
            rng = np.random.default_rng(seed)
            chosen = rng.choice(unique_members, size=n_sample, replace=True)

            frames: list[pl.DataFrame] = []
            for new_id, orig_id in enumerate(chosen):
                member_rows = ensemble.values.filter(
                    pl.col("member_id") == orig_id
                ).with_columns(pl.lit(global_offset + new_id).alias("member_id"))
                frames.append(member_rows)

            member_dfs.extend(frames)
            global_offset += n_sample
            ref_ensemble = ensemble

        if not member_dfs or ref_ensemble is None:
            continue

        merged = pl.concat(member_dfs)
        result[param] = ForecastEnsemble.from_members(
            station_id=ref_ensemble.station_id,
            issued_at=ref_ensemble.issued_at,
            parameter=param,
            units=ref_ensemble.units,
            time_step=ref_ensemble.time_step,
            values=merged,
            model_id=BMA_MODEL_ID,
        )

    return result


def _derive_uniform_time_step(ensemble: ForecastEnsemble) -> timedelta | None:
    """D6 fixer round — the retained `valid_time`s must form an evenly
    spaced grid, AND the `time_step` persisted with them must be the delta
    that grid actually has. Counting timestamps (`_MIN_PERSISTED_TIMESTAMPS`)
    is not enough: an intersection that drops an interior timestamp instead
    of a trailing one still clears that floor while leaving unequal gaps.
    Nor is `ensemble.time_step` itself trustworthy here — it is the ref
    contributor's DECLARED step, carried through `combine_ensembles_pooled`
    unchanged, so a uniformly COARSENED intersection (e.g. an hourly
    contributor reduced to every other timestamp by the intersection) keeps
    the stale 1-hour label on an actually-2-hour grid. In-memory callers and
    a post-persistence reload (`store/forecast_store.py` derives
    `native_step_seconds` from the first two rows on readback) would then
    disagree about the same forecast's step.

    Returns the sole consecutive delta when the grid is uniform, else
    `None`."""
    valid_times = sorted(ensemble.values["valid_time"].unique().to_list())
    deltas = {b - a for a, b in zip(valid_times, valid_times[1:], strict=False)}
    if len(deltas) != 1:
        return None
    return next(iter(deltas))


def build_combined_forecasts(
    station_id: StationId,
    multi_result: MultiModelForecastResult,
    strategy: ModelCombinationStrategy,
    nwp_cycle_reference_time: UtcDatetime | None,
    nwp_cycle_source: NwpCycleSource,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
    weights: dict[ModelId, float] | None = None,
) -> list[OperationalForecast]:
    if strategy == ModelCombinationStrategy.PRIMARY:
        return []

    combinable_results = multi_result.combinable_results
    if len(combinable_results) < 2:
        return []

    if strategy == ModelCombinationStrategy.CONSENSUS:
        raise NotImplementedError("Consensus combination not yet implemented")

    ensembles_by_model: dict[ModelId, dict[str, ForecastEnsemble]] = {
        mid: result.ensembles for mid, result in combinable_results.items()
    }

    combination_strategy_label: str
    combined_model_id: ModelId

    match strategy:
        case ModelCombinationStrategy.BMA:
            if weights is None:
                raise ValueError("BMA strategy requires weights")
            combined = combine_ensembles_bma(ensembles_by_model, weights)
            combination_strategy_label = "bma"
            combined_model_id = BMA_MODEL_ID
        case _:
            # POOLED (default)
            combined = combine_ensembles_pooled(ensembles_by_model)
            combination_strategy_label = "pooled"
            combined_model_id = POOLED_MODEL_ID

    now = clock()
    first_result = next(iter(combinable_results.values()))
    first_ensemble = next(iter(first_result.ensembles.values()), None)
    # Combined forecasts always have contributing ensembles (>=2 combinable
    # results guaranteed above); ``now`` is a defensive fallback so issued_at
    # stays a concrete UtcDatetime even when reference_time is None
    # (runoff-only mode).
    issued_at = first_ensemble.issued_at if first_ensemble else now
    source_model_ids = list(combinable_results.keys())

    forecasts: list[OperationalForecast] = []
    for param, ensemble in combined.items():
        # D6 — the store fabricates a one-hour `time_step` for a
        # single-timestamp forecast (`store/forecast_store.py`), which
        # would publish `native_step_seconds: 3600`: a fresh instance of
        # the sawtooth defect this plan removes. A one-timestamp
        # intersection is legal for `combine_ensembles_pooled()` itself
        # (`services/skill/combined_skill.py` calls it too, and a single
        # hindcast step is a normal case there) — the floor belongs only
        # at this persistence boundary.
        if ensemble.forecast_horizon_steps < _MIN_PERSISTED_TIMESTAMPS:
            log.warning(
                "forecast_combination.pooled_single_timestamp_not_persisted",
                parameter=param,
                station_id=str(station_id),
                forecast_horizon_steps=ensemble.forecast_horizon_steps,
            )
            continue
        # D6 fixer round — counting timestamps is not enough: the
        # intersection can drop an INTERIOR timestamp (e.g. 07:00, 09:00,
        # 10:00 — deltas of 2h then 1h), which still clears the count floor
        # above. The store derives `native_step_seconds` from the first
        # pair on readback (`store/forecast_store.py`), so persisting a
        # non-uniform grid publishes one misleading step — the original
        # defect in a new costume.
        derived_time_step = _derive_uniform_time_step(ensemble)
        if derived_time_step is None:
            log.warning(
                "forecast_combination.pooled_non_uniform_spacing_not_persisted",
                parameter=param,
                station_id=str(station_id),
            )
            continue
        # D6 fixer round (review) — a uniformly COARSENED intersection
        # (every contributor loses the same interior timestamps, e.g. an
        # hourly grid reduced to every other step) passes the check above
        # but leaves `ensemble.time_step` at the ref contributor's stale
        # DECLARED step. Rebuild the ensemble with the step the surviving
        # grid actually has before persisting, so an in-memory read and a
        # post-persistence reload agree.
        if derived_time_step != ensemble.time_step:
            ensemble = replace(ensemble, time_step=derived_time_step)
        forecast = OperationalForecast(
            id=ForecastId(uuid_factory()),
            station_id=station_id,
            model_id=combined_model_id,
            model_artifact_id=None,
            issued_at=issued_at,
            nwp_cycle_reference_time=nwp_cycle_reference_time,
            nwp_cycle_source=nwp_cycle_source,
            representation=ensemble.representation,
            status=ForecastStatus.RAW,
            version=1,
            warm_up_source=None,
            warm_up_state_age_hours=None,
            observation_staleness_hours=None,
            ensemble=ensemble,
            created_at=now,
            updated_at=now,
            qc_status=QcStatus.RAW,
            qc_flags=(),
            input_quality=InputQualityLevel.FULL,
            input_quality_flags=(),
            combination_strategy=combination_strategy_label,
            source_model_ids=source_model_ids,
        )
        forecasts.append(forecast)

    return forecasts
