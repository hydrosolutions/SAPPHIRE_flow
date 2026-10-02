from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query

from sapphire_flow.api.deps import get_connection, get_stores
from sapphire_flow.api.forecast_read import (
    require_standard_results,
    require_standard_store,
)
from sapphire_flow.api.model_visibility import (
    model_tier_for_model_id,
    station_has_active_floor,
)
from sapphire_flow.api.publication_gate import (
    PublicationGate,
    get_publication_gate,
    require_publication_store,
    station_tenant_id,
)
from sapphire_flow.api.publication_views import publication_metadata
from sapphire_flow.api.routes.api_forecasts import to_forecast_detail
from sapphire_flow.api.schemas import (
    ForecastSummary,
    GeoCoordResponse,
    ModelAssignmentResponse,
    ObservationResponse,
    PaginatedResponse,
    QcFlagResponse,
    StationDetail,
    StationSummary,
    ThresholdResponse,
    WeatherSourceResponse,
)
from sapphire_flow.api.security import (
    Principal,
    ensure_station_in_scope,
    require_principal,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.enums import QcStatus, StationKind, StationStatus
from sapphire_flow.types.ids import ModelId, StationId

router = APIRouter(prefix="/api/v1", tags=["api-stations"])


def _to_station_summary(s: Any) -> StationSummary:
    return StationSummary(
        id=str(s.id),
        code=s.code,
        name=s.name,
        location=GeoCoordResponse(
            lon=s.location.lon,
            lat=s.location.lat,
            altitude_masl=s.location.altitude_masl,
        ),
        station_kind=s.station_kind.value,
        station_status=s.station_status.value,
        network=s.network,
        ownership=s.ownership.value,
        measured_parameters=sorted(s.measured_parameters),
    )


def _to_threshold_response(t: Any) -> ThresholdResponse:
    return ThresholdResponse(
        danger_level=t.danger_level,
        parameter=t.parameter,
        value=t.value,
        source=t.source.value,
    )


def _to_model_assignment_response(a: Any) -> ModelAssignmentResponse:
    return ModelAssignmentResponse(
        model_id=str(a.model_id),
        model_tier=model_tier_for_model_id(a.model_id).value,
        time_step_hours=a.time_step.total_seconds() / 3600,
        status=a.status.value,
        priority=a.priority,
    )


def _to_weather_source_response(ws: Any) -> WeatherSourceResponse:
    return WeatherSourceResponse(
        nwp_source=ws.nwp_source,
        extraction_type=ws.extraction_type.value,
        status=ws.status.value,
    )


def _to_qc_flag_response(f: Any) -> QcFlagResponse:
    return QcFlagResponse(
        rule_id=f.rule_id,
        rule_version=f.rule_version,
        status=f.status.value,
        detail=f.detail,
    )


def _to_observation_response(o: Any) -> ObservationResponse:
    return ObservationResponse(
        id=str(o.id),
        station_id=str(o.station_id),
        timestamp=o.timestamp,
        parameter=o.parameter,
        value=o.value,
        source=o.source.value,
        qc_status=o.qc_status.value,
        qc_flags=[_to_qc_flag_response(f) for f in o.qc_flags],
        qc_rule_version=o.qc_rule_version,
    )


def _to_forecast_summary(row: Any) -> ForecastSummary:
    require_standard_results((row,))
    return ForecastSummary(
        id=str(row.id),
        station_id=str(row.station_id),
        model_id=str(row.model_id),
        model_tier=model_tier_for_model_id(row.model_id).value,
        issued_at=row.issued_at,
        parameter=row.parameter,
        representation=row.representation.value,
        status=row.status.value,
        qc_status=row.qc_status.value,
        nwp_cycle_source=row.nwp_cycle_source.value,
        created_at=row.created_at,
        input_quality=row.input_quality.value if row.input_quality else None,
        input_quality_flags=[
            {
                "category": f.category.value,
                "level": f.level.value,
                "detail": f.detail,
            }
            for f in row.input_quality_flags
        ]
        if row.input_quality is not None
        else None,
        qc_flags=[_to_qc_flag_response(f) for f in row.qc_flags],
    )


def parse_api_datetime(value: str, field_name: str) -> UtcDatetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid datetime for {field_name}: {value}"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return ensure_utc(dt)


def _parse_enum(value: str, enum_cls: type[Enum], field_name: str) -> Any:
    try:
        return enum_cls(value)
    except ValueError as exc:
        valid = [e.value for e in enum_cls]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}: {value!r}. Valid values: {valid}",
        ) from exc


@router.get("/stations")
def list_stations(
    kind: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    stores: dict[str, Any] = Depends(get_stores),
    principal: Principal = Depends(require_principal),
) -> PaginatedResponse[StationSummary]:
    station_kind: StationKind | None = None
    if kind is not None:
        station_kind = _parse_enum(kind, StationKind, "kind")

    all_stations = stores["station_store"].fetch_all_stations(kind=station_kind)

    if status is not None:
        station_status = _parse_enum(status, StationStatus, "status")
        all_stations = [s for s in all_stations if s.station_status == station_status]

    if not principal.is_admin:
        all_stations = [s for s in all_stations if principal.station_in_scope(s.id)]

    total = len(all_stations)
    page = all_stations[offset : offset + limit]

    return PaginatedResponse[StationSummary](
        items=[_to_station_summary(s) for s in page],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stations/{station_id}")
def get_station(
    station_id: str,
    stores: dict[str, Any] = Depends(get_stores),
    conn: sa.Connection = Depends(get_connection),
    principal: Principal = Depends(require_principal),
) -> StationDetail:
    sid = StationId(UUID(station_id))
    ensure_station_in_scope(principal, sid)
    station = stores["station_store"].fetch_station(sid)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")

    thresholds = stores["station_store"].fetch_thresholds(sid)
    assignments = stores["station_store"].fetch_model_assignments(sid)
    weather_sources = stores["station_store"].fetch_weather_sources(sid)

    return StationDetail(
        id=str(station.id),
        code=station.code,
        name=station.name,
        location=GeoCoordResponse(
            lon=station.location.lon,
            lat=station.location.lat,
            altitude_masl=station.location.altitude_masl,
        ),
        station_kind=station.station_kind.value,
        station_status=station.station_status.value,
        network=station.network,
        ownership=station.ownership.value,
        measured_parameters=sorted(station.measured_parameters),
        basin_id=str(station.basin_id) if station.basin_id is not None else None,
        timezone=station.timezone,
        regulation_type=(
            station.regulation_type.value
            if station.regulation_type is not None
            else None
        ),
        forecast_targets=(
            sorted(station.forecast_targets)
            if station.forecast_targets is not None
            else None
        ),
        gauging_status=station.gauging_status.value,
        wigos_id=station.wigos_id,
        created_at=station.created_at,
        updated_at=station.updated_at,
        no_floor=not station_has_active_floor(station_id=sid, stores=stores, conn=conn),
        thresholds=[_to_threshold_response(t) for t in thresholds],
        model_assignments=[_to_model_assignment_response(a) for a in assignments],
        weather_sources=[_to_weather_source_response(ws) for ws in weather_sources],
    )


@router.get("/stations/{station_id}/observations")
def list_observations(
    station_id: str,
    parameter: str = Query(...),
    start: str = Query(...),
    end: str = Query(...),
    qc_status: str | None = Query(None),
    stores: dict[str, Any] = Depends(get_stores),
    principal: Principal = Depends(require_principal),
) -> list[ObservationResponse]:
    sid = StationId(UUID(station_id))
    ensure_station_in_scope(principal, sid)
    start_dt = parse_api_datetime(start, "start")
    end_dt = parse_api_datetime(end, "end")

    qc: QcStatus | None = None
    if qc_status is not None:
        qc = _parse_enum(qc_status, QcStatus, "qc_status")

    observations = stores["obs_store"].fetch_observations(
        sid, parameter, start_dt, end_dt, qc_status=qc
    )
    if not principal.is_admin:
        observations = [o for o in observations if o.delivery_id != DELIVERY_ID]
    return [_to_observation_response(o) for o in observations]


@router.get("/stations/{station_id}/forecasts")
def list_forecasts(
    station_id: str,
    model_id: str | None = Query(None),
    parameter: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    degraded_only: bool = Query(
        False,
        description=(
            "Return only forecasts assessed PARTIAL or DEGRADED input "
            "quality (Plan 253 OD-2). Forecasts with no assessment "
            "recorded (unknown) are never included."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    stores: dict[str, Any] = Depends(get_stores),
    principal: Principal = Depends(require_principal),
    gate: PublicationGate = Depends(get_publication_gate),
) -> PaginatedResponse[ForecastSummary]:
    sid = StationId(UUID(station_id))
    ensure_station_in_scope(principal, sid)
    now = UtcDatetime(datetime.now(UTC))

    start_dt = (
        parse_api_datetime(start, "start")
        if start is not None
        else UtcDatetime(now - timedelta(days=7))
    )
    end_dt = parse_api_datetime(end, "end") if end is not None else now

    mid: ModelId | None = ModelId(model_id) if model_id is not None else None

    if gate.active_tenant_ids:
        tenant_id = station_tenant_id(stores, sid)
        if gate.active(tenant_id):
            require_standard_store(stores["forecast_store"])
            pub = require_publication_store(stores)
            pub.lock_read_snapshot()
            selected_ids, total = pub.fetch_selected_ids(
                sid,
                start_dt,
                end_dt,
                model_id=model_id,
                parameter=parameter,
                degraded_only=degraded_only,
                limit=limit,
                offset=offset,
            )
            forecasts = [
                stores["forecast_store"].fetch_forecast(fid) for fid in selected_ids
            ]
            require_standard_results(f for f in forecasts if f is not None)
            return PaginatedResponse[ForecastSummary](
                items=[
                    ForecastSummary.model_validate(
                        to_forecast_detail(f)
                        .model_copy(update=publication_metadata(pub, f, tenant_id)[0])
                        .model_dump()
                    )
                    for f in forecasts
                    if f is not None
                ],
                total=total,
                limit=limit,
                offset=offset,
            )

    require_standard_store(stores["forecast_store"])
    rows, total = stores["forecast_store"].fetch_forecast_summaries(
        sid,
        start_dt,
        end_dt,
        model_id=mid,
        parameter=parameter,
        degraded_only=degraded_only,
        limit=limit,
        offset=offset,
    )

    require_standard_results(rows)
    return PaginatedResponse[ForecastSummary](
        items=[_to_forecast_summary(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
