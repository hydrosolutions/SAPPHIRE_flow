from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query

from sapphire_flow.api.deps import get_connection, get_stores
from sapphire_flow.api.model_visibility import (
    model_tier_for_model_id,
    station_has_active_floor,
)
from sapphire_flow.api.schemas import (
    ForecastSummary,
    GeoCoordResponse,
    ModelAssignmentResponse,
    ObservationResponse,
    PaginatedResponse,
    StationDetail,
    StationSummary,
    ThresholdResponse,
    WeatherSourceResponse,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
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


def _to_observation_response(o: Any) -> ObservationResponse:
    return ObservationResponse(
        id=str(o.id),
        station_id=str(o.station_id),
        timestamp=o.timestamp,
        parameter=o.parameter,
        value=o.value,
        source=o.source.value,
        qc_status=o.qc_status.value,
        qc_flags=[
            {
                "rule_id": f.rule_id,
                "rule_version": f.rule_version,
                "status": f.status.value,
                "detail": f.detail,
            }
            for f in o.qc_flags
        ],
    )


def _to_forecast_summary(row: Any) -> ForecastSummary:
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
    )


def _parse_datetime(value: str, field_name: str) -> UtcDatetime:
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
) -> PaginatedResponse[StationSummary]:
    station_kind: StationKind | None = None
    if kind is not None:
        station_kind = _parse_enum(kind, StationKind, "kind")

    all_stations = stores["station_store"].fetch_all_stations(kind=station_kind)

    if status is not None:
        station_status = _parse_enum(status, StationStatus, "status")
        all_stations = [s for s in all_stations if s.station_status == station_status]

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
) -> StationDetail:
    sid = StationId(UUID(station_id))
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
) -> list[ObservationResponse]:
    sid = StationId(UUID(station_id))
    start_dt = _parse_datetime(start, "start")
    end_dt = _parse_datetime(end, "end")

    qc: QcStatus | None = None
    if qc_status is not None:
        qc = _parse_enum(qc_status, QcStatus, "qc_status")

    observations = stores["obs_store"].fetch_observations(
        sid, parameter, start_dt, end_dt, qc_status=qc
    )
    return [_to_observation_response(o) for o in observations]


@router.get("/stations/{station_id}/forecasts")
def list_forecasts(
    station_id: str,
    model_id: str | None = Query(None),
    parameter: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    stores: dict[str, Any] = Depends(get_stores),
) -> PaginatedResponse[ForecastSummary]:
    sid = StationId(UUID(station_id))
    now = UtcDatetime(datetime.now(UTC))

    start_dt = (
        _parse_datetime(start, "start")
        if start is not None
        else UtcDatetime(now - timedelta(days=7))
    )
    end_dt = _parse_datetime(end, "end") if end is not None else now

    mid: ModelId | None = ModelId(model_id) if model_id is not None else None

    rows, total = stores["forecast_store"].fetch_forecast_summaries(
        sid,
        start_dt,
        end_dt,
        model_id=mid,
        parameter=parameter,
        limit=limit,
        offset=offset,
    )

    return PaginatedResponse[ForecastSummary](
        items=[_to_forecast_summary(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
