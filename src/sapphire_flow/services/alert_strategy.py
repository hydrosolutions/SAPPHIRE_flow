from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl
import structlog

from sapphire_flow.types.enums import AlertModelStrategy, EnsembleRepresentation

if TYPE_CHECKING:
    from sapphire_flow.types.domain import (
        DangerLevelDefinition,
        ExceedanceResult,
        ForecastParameter,
        StationThreshold,
    )
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.ids import ModelId, StationId

log = structlog.get_logger()


def _find_threshold(
    thresholds: list[StationThreshold],
    danger_level: DangerLevelDefinition,
) -> StationThreshold | None:
    for t in thresholds:
        if t.danger_level == danger_level.name:
            return t
    return None


def _compute_exceedance(
    ensemble: ForecastEnsemble,
    threshold_value: float,
) -> float:
    """Compute exceedance probability P(forecast > threshold).

    Returns the maximum exceedance probability across all valid_times
    (conservative: alert if any lead time exceeds).

    MEMBERS: per-timestep fraction of members exceeding threshold.
    QUANTILES: per-timestep CDF interpolation between adjacent quantile levels.
    """
    if ensemble.representation == EnsembleRepresentation.MEMBERS:
        per_time = (
            ensemble.values
            .group_by("valid_time")
            .agg(
                (pl.col("value") > threshold_value).mean().alias("exceedance")
            )
        )
        max_val = per_time["exceedance"].max()
        return float(max_val) if isinstance(max_val, (int, float)) else 0.0

    # QUANTILES: per-timestep CDF interpolation
    valid_times = ensemble.values["valid_time"].unique().sort()
    max_exceedance = 0.0

    for vt in valid_times:
        ts_df = (
            ensemble.values
            .filter(pl.col("valid_time") == vt)
            .group_by("quantile")
            .agg(pl.col("value").median().alias("median_value"))
            .sort("quantile")
        )
        quantiles = ts_df["quantile"].to_list()
        values = ts_df["median_value"].to_list()

        if not values:
            continue

        if threshold_value <= values[0]:
            exc = 1.0 - quantiles[0]
        elif threshold_value >= values[-1]:
            exc = 1.0 - quantiles[-1]
        else:
            exc = 0.0
            for i in range(len(values) - 1):
                if values[i] <= threshold_value <= values[i + 1]:
                    span = values[i + 1] - values[i]
                    frac = (threshold_value - values[i]) / span if span != 0.0 else 0.0
                    cdf_at_threshold = quantiles[i] + frac * (quantiles[i + 1] - quantiles[i])
                    exc = 1.0 - cdf_at_threshold
                    break

        if exc > max_exceedance:
            max_exceedance = exc

    return max_exceedance


def _pool_ensembles(
    model_ensembles: dict[ModelId, ForecastEnsemble],
) -> ForecastEnsemble:
    """Concatenate all models' members into a grand ensemble with renumbered member IDs."""
    from sapphire_flow.types.ensemble import ForecastEnsemble as FE

    frames: list[pl.DataFrame] = []
    member_offset = 0
    ref_ensemble: ForecastEnsemble | None = None

    for _model_id, ens in model_ensembles.items():
        if ref_ensemble is None:
            ref_ensemble = ens
        df = ens.values.with_columns(
            (pl.col("member_id") + member_offset).alias("member_id")
        )
        frames.append(df)
        member_offset += ens.member_count

    assert ref_ensemble is not None
    pooled_df = pl.concat(frames)

    return FE.from_members(
        station_id=ref_ensemble.station_id,
        issued_at=ref_ensemble.issued_at,
        parameter=ref_ensemble.parameter,
        units=ref_ensemble.units,
        time_step=ref_ensemble.time_step,
        values=pooled_df,
    )


class PrimaryModelStrategy:
    def evaluate(
        self,
        station_id: StationId,
        parameter: ForecastParameter,
        model_ensembles: dict[ModelId, ForecastEnsemble],
        thresholds: list[StationThreshold],
        danger_levels: list[DangerLevelDefinition],
        priorities: dict[ModelId, int],
    ) -> list[ExceedanceResult]:
        from sapphire_flow.types.domain import ExceedanceResult

        if not model_ensembles:
            return []
        if not priorities:
            log.warning(
                "alert.priorities_not_found",
                station_id=str(station_id),
                n_models=len(model_ensembles),
            )

        primary_model_id = min(
            model_ensembles.keys(),
            key=lambda mid: (priorities.get(mid, 999), str(mid)),
        )
        ensemble = model_ensembles[primary_model_id]
        param_thresholds = [t for t in thresholds if t.parameter == parameter]

        results: list[ExceedanceResult] = []
        for dl in danger_levels:
            threshold = _find_threshold(param_thresholds, dl)
            if threshold is None:
                continue
            exceedance = _compute_exceedance(ensemble, threshold.value)
            exceeded = exceedance >= dl.trigger_probability
            results.append(
                ExceedanceResult(
                    station_id=station_id,
                    danger_level=dl.name,
                    parameter=parameter,
                    threshold_value=threshold.value,
                    exceedance_probability=exceedance,
                    observed_value=None,
                    exceeded=exceeded,
                    model_ids=(primary_model_id,),
                    strategy=AlertModelStrategy.PRIMARY,
                )
            )
        return results


class PooledEnsembleStrategy:
    def evaluate(
        self,
        station_id: StationId,
        parameter: ForecastParameter,
        model_ensembles: dict[ModelId, ForecastEnsemble],
        thresholds: list[StationThreshold],
        danger_levels: list[DangerLevelDefinition],
        priorities: dict[ModelId, int],
    ) -> list[ExceedanceResult]:
        from sapphire_flow.types.domain import ExceedanceResult

        if not model_ensembles:
            return []

        if not all(
            e.representation == EnsembleRepresentation.MEMBERS
            for e in model_ensembles.values()
        ):
            raise ValueError(
                "PooledEnsembleStrategy requires homogeneous MEMBERS representation"
            )

        pooled = _pool_ensembles(model_ensembles)
        all_model_ids = tuple(sorted(model_ensembles.keys(), key=str))
        param_thresholds = [t for t in thresholds if t.parameter == parameter]

        results: list[ExceedanceResult] = []
        for dl in danger_levels:
            threshold = _find_threshold(param_thresholds, dl)
            if threshold is None:
                continue
            exceedance = _compute_exceedance(pooled, threshold.value)
            exceeded = exceedance >= dl.trigger_probability
            results.append(
                ExceedanceResult(
                    station_id=station_id,
                    danger_level=dl.name,
                    parameter=parameter,
                    threshold_value=threshold.value,
                    exceedance_probability=exceedance,
                    observed_value=None,
                    exceeded=exceeded,
                    model_ids=all_model_ids,
                    strategy=AlertModelStrategy.POOLED,
                )
            )
        return results
