from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import polars as pl
from fastapi import APIRouter, Depends, HTTPException

from sapphire_flow.api.deps import get_stores
from sapphire_flow.api.model_visibility import model_tier_for_model_id
from sapphire_flow.api.schemas import EnsembleResponse, ForecastDetail
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.ids import ForecastId

if TYPE_CHECKING:
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast import OperationalForecast

router = APIRouter(prefix="/api/v1", tags=["api-forecasts"])


def _to_ensemble_response(e: ForecastEnsemble) -> EnsembleResponse:
    df = e.values
    valid_times = sorted(df["valid_time"].unique().to_list())

    series: dict[str, list[float]] = {}
    match e.representation:
        case EnsembleRepresentation.MEMBERS:
            for member_id in sorted(df["member_id"].unique().to_list()):
                member_df = df.filter(pl.col("member_id") == member_id).sort(
                    "valid_time"
                )
                series[str(member_id)] = member_df["value"].to_list()
        case EnsembleRepresentation.QUANTILES:
            for q in sorted(df["quantile"].unique().to_list()):
                q_df = df.filter(pl.col("quantile") == q).sort("valid_time")
                series[str(q)] = q_df["value"].to_list()

    return EnsembleResponse(
        representation=e.representation.value,
        parameter=e.parameter,
        units=e.units,
        forecast_horizon_steps=e.forecast_horizon_steps,
        time_step_seconds=int(e.time_step.total_seconds()),
        member_count=e.member_count,
        valid_times=valid_times,
        series=series,
    )


def _to_forecast_detail(f: OperationalForecast) -> ForecastDetail:
    return ForecastDetail(
        id=str(f.id),
        station_id=str(f.station_id),
        model_id=str(f.model_id),
        model_tier=model_tier_for_model_id(f.model_id).value,
        issued_at=f.issued_at,
        parameter=f.ensemble.parameter,
        representation=f.representation.value,
        status=f.status.value,
        qc_status=f.qc_status.value,
        nwp_cycle_source=f.nwp_cycle_source.value,
        created_at=f.created_at,
        model_artifact_id=str(f.model_artifact_id) if f.model_artifact_id else None,
        nwp_cycle_reference_time=f.nwp_cycle_reference_time,
        version=f.version,
        warm_up_source=f.warm_up_source.value if f.warm_up_source else None,
        observation_staleness_hours=f.observation_staleness_hours,
        combination_strategy=f.combination_strategy,
        source_model_ids=[str(mid) for mid in f.source_model_ids]
        if f.source_model_ids
        else None,
        updated_at=f.updated_at,
        ensemble=_to_ensemble_response(f.ensemble),
    )


@router.get("/forecasts/{forecast_id}", response_model=ForecastDetail)
def get_forecast(
    forecast_id: str,
    stores: dict[str, Any] = Depends(get_stores),
) -> ForecastDetail:
    forecast = stores["forecast_store"].fetch_forecast(ForecastId(UUID(forecast_id)))
    if forecast is None:
        raise HTTPException(status_code=404, detail="Forecast not found")
    return _to_forecast_detail(forecast)
