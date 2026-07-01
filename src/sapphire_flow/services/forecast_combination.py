from __future__ import annotations

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
    from uuid import UUID

    from sapphire_flow.services.run_station_forecast import MultiModelForecastResult
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import NwpCycleSource

log = structlog.get_logger()

_BMA_TARGET_MEMBERS = 100


def combine_ensembles_pooled(
    ensembles: dict[ModelId, dict[str, ForecastEnsemble]],
) -> dict[str, ForecastEnsemble]:
    from sapphire_flow.types.ensemble import ForecastEnsemble

    all_params: set[str] = set()
    for param_map in ensembles.values():
        all_params.update(param_map.keys())

    result: dict[str, ForecastEnsemble] = {}
    for param in all_params:
        member_dfs: list[pl.DataFrame] = []
        ref_ensemble: ForecastEnsemble | None = None
        offset = 0

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

            n_members = ensemble.values["member_id"].n_unique()
            remapped = ensemble.values.with_columns(
                (pl.col("member_id") + offset).alias("member_id")
            )
            member_dfs.append(remapped)
            offset += n_members
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
    for _param, ensemble in combined.items():
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
