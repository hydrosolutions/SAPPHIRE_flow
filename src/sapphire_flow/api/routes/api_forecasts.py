from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from sapphire_flow.api.deps import get_stores
from sapphire_flow.api.forecast_read import (
    require_standard_detail,
    require_standard_store,
)
from sapphire_flow.api.model_visibility import model_tier_for_model_id
from sapphire_flow.api.provisional_forecasts import (
    ProvisionalForecastReader,
    get_provisional_reader,
    require_provisional_role,
)
from sapphire_flow.api.publication_gate import (
    PublicationGate,
    get_publication_gate,
    require_publication_store,
    station_tenant_id,
)
from sapphire_flow.api.publication_views import publication_metadata
from sapphire_flow.api.schemas import (
    EnsembleResponse,
    ForecastDetail,
    ProvisionalForecastDetail,
    PublicationTombstone,
    QcFlagResponse,
)
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.types.enums import EnsembleRepresentation
from sapphire_flow.types.ids import ForecastId

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import ForecastStore
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


def _to_qc_flag_response(flag: Any) -> QcFlagResponse:
    # `flag: Any` (not `QcFlag`) deliberately, matching
    # `api_stations.py::_to_qc_flag_response`: `QcFlag.__post_init__`
    # already forbids `raw`/`missing` at construction, a domain invariant
    # pyright cannot see through `QcStatus.value`'s full Literal.
    return QcFlagResponse(
        rule_id=flag.rule_id,
        rule_version=flag.rule_version,
        status=flag.status.value,
        detail=flag.detail,
    )


def _forecast_detail_fields(f: OperationalForecast) -> dict[str, Any]:
    return dict(
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
        input_quality=f.input_quality.value if f.input_quality else None,
        input_quality_flags=[
            {
                "category": flag.category.value,
                "level": flag.level.value,
                "detail": flag.detail,
            }
            for flag in f.input_quality_flags
        ]
        if f.input_quality is not None
        else None,
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
        qc_flags=[_to_qc_flag_response(flag) for flag in f.qc_flags],
    )


def to_forecast_detail(f: OperationalForecast) -> ForecastDetail:
    require_standard_detail(f)
    return ForecastDetail(**_forecast_detail_fields(f))


def _detail_with_weather(
    forecast: OperationalForecast, store: ForecastStore
) -> ForecastDetail:
    from sapphire_flow.api.weather_evidence import read_weather_summary

    summary = read_weather_summary(
        version=forecast.weather_evidence_version,
        forecast_id=forecast.id,
        station_id=forecast.station_id,
        model_id=forecast.model_id,
        issue_time=forecast.issued_at,
        data_use=forecast.data_use,
        store=store,
    )
    return to_forecast_detail(forecast).model_copy(update={"weather_use": summary})


@router.get(
    "/forecasts/{forecast_id}",
    response_model=ForecastDetail | ProvisionalForecastDetail,
)
def get_forecast(
    forecast_id: str,
    stores: dict[str, Any] = Depends(get_stores),
    principal: Principal = Depends(require_principal),
    gate: PublicationGate = Depends(get_publication_gate),
    *,
    data_use: Literal["standard", "provisional_rating"] = Query("standard"),
    provisional: ProvisionalForecastReader = Depends(get_provisional_reader),
) -> ForecastDetail | JSONResponse:
    if data_use == "provisional_rating":
        require_provisional_role(principal)
        forecast, metadata = provisional.detail(ForecastId(UUID(forecast_id)))
        from sapphire_flow.api.weather_evidence import read_weather_summary

        weather = read_weather_summary(
            version=forecast.weather_evidence_version,
            forecast_id=forecast.id,
            station_id=forecast.station_id,
            model_id=forecast.model_id,
            issue_time=forecast.issued_at,
            data_use=forecast.data_use,
            store=provisional.store,
        )
        response = ProvisionalForecastDetail(
            **_forecast_detail_fields(forecast),
            **metadata,
            weather_use=weather,
        )
        return JSONResponse(content=response.model_dump(mode="json"))
    fid = ForecastId(UUID(forecast_id))
    forecast_store = stores["forecast_store"]
    require_standard_store(forecast_store)
    forecast = forecast_store.fetch_forecast(fid)
    require_standard_detail(forecast)
    # Plan 147 Slice C R2: scope-check AFTER fetch (need station_id off the
    # row) but BEFORE returning — an out-of-scope forecast is a 404, not a
    # 200 (`042:100-103`). Plan 401 T2: with the SAME body as an absent one —
    # "Station not found" confirmed that another scope's forecast exists.
    if forecast is None or not principal.station_in_scope(forecast.station_id):
        raise HTTPException(status_code=404, detail="Forecast not found")
    if not gate.active_tenant_ids:
        return _detail_with_weather(forecast, forecast_store)
    tenant_id = station_tenant_id(stores, forecast.station_id)
    if not gate.active(tenant_id):
        return _detail_with_weather(forecast, forecast_store)
    pub = require_publication_store(stores)
    pub.lock_read_snapshot()
    metadata, decisions = publication_metadata(pub, forecast, tenant_id)
    if not decisions:
        raise HTTPException(status_code=404, detail="Forecast not found")
    if metadata["publication_state"] == "withdrawn":
        last = decisions[-1]
        tombstone = PublicationTombstone(
            forecast_id=str(forecast.id),
            station_id=str(forecast.station_id),
            parameter=forecast.ensemble.parameter,
            issued_at=forecast.issued_at,
            decision_id=str(last.id),
            withdrawn_at=last.created_at,
        )
        return JSONResponse(status_code=410, content=tombstone.model_dump(mode="json"))
    return _detail_with_weather(forecast, forecast_store).model_copy(update=metadata)
