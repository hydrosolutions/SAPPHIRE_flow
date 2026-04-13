from __future__ import annotations

from typing import TYPE_CHECKING

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
from sapphire_flow.types.ids import POOLED_MODEL_ID, ForecastId, ModelId, StationId

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sapphire_flow.services.run_station_forecast import MultiModelForecastResult
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import NwpCycleSource

log = structlog.get_logger()


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


def build_combined_forecasts(
    station_id: StationId,
    multi_result: MultiModelForecastResult,
    strategy: ModelCombinationStrategy,
    nwp_cycle_reference_time: UtcDatetime,
    nwp_cycle_source: NwpCycleSource,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
) -> list[OperationalForecast]:
    if strategy == ModelCombinationStrategy.PRIMARY:
        return []

    combinable_results = multi_result.combinable_results
    if len(combinable_results) < 2:
        return []

    if strategy == ModelCombinationStrategy.BMA:
        raise NotImplementedError("BMA combination not yet implemented")
    if strategy == ModelCombinationStrategy.CONSENSUS:
        raise NotImplementedError("Consensus combination not yet implemented")

    # POOLED
    ensembles_by_model: dict[ModelId, dict[str, ForecastEnsemble]] = {
        mid: result.ensembles for mid, result in combinable_results.items()
    }
    combined = combine_ensembles_pooled(ensembles_by_model)

    first_result = next(iter(combinable_results.values()))
    first_ensemble = next(iter(first_result.ensembles.values()), None)
    issued_at = first_ensemble.issued_at if first_ensemble else nwp_cycle_reference_time

    now = clock()
    source_model_ids = list(combinable_results.keys())

    forecasts: list[OperationalForecast] = []
    for _param, ensemble in combined.items():
        forecast = OperationalForecast(
            id=ForecastId(uuid_factory()),
            station_id=station_id,
            model_id=POOLED_MODEL_ID,
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
            combination_strategy="pooled",
            source_model_ids=source_model_ids,
        )
        forecasts.append(forecast)

    return forecasts
