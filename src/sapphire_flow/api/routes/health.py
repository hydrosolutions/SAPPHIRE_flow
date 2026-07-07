from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from sapphire_flow.api.deps import get_connection, get_stores
from sapphire_flow.api.schemas import (
    HealthDetailResponse,
    HealthResponse,
    PipelineHealthRecordResponse,
)
from sapphire_flow.types.enums import PipelineCheckType

router = APIRouter(prefix="/api/v1", tags=["health"])
dashboard_router = APIRouter(tags=["health-dashboard"])

_PREFECT_API_URL = os.environ.get("PREFECT_API_URL", "http://localhost:4200/api")


@router.get("/health")
def health(conn: sa.Connection = Depends(get_connection)) -> HealthResponse:
    conn.execute(sa.text("SELECT 1"))

    prefect_status = "unknown"
    try:
        resp = httpx.get(f"{_PREFECT_API_URL}/health", timeout=3.0)
        prefect_status = "ok" if resp.status_code == 200 else "unhealthy"
    except httpx.HTTPError:
        prefect_status = "unreachable"

    return HealthResponse(
        status="ok",
        prefect_status=prefect_status,
        checked_at=datetime.now(UTC),
    )


def _to_pipeline_health_response(record: Any) -> PipelineHealthRecordResponse:
    return PipelineHealthRecordResponse(
        check_type=record.check_type.value,
        checked_at=record.checked_at,
        status=record.status.value,
        subject=record.subject,
        detail=record.detail,
        cycle_time=record.cycle_time,
        created_at=record.created_at,
    )


def _parse_check_type(value: str | None) -> PipelineCheckType | None:
    if value is None or value == "":
        return None
    try:
        return PipelineCheckType(value)
    except ValueError as exc:
        valid = [ct.value for ct in PipelineCheckType]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid check_type: {value!r}. Valid values: {valid}",
        ) from exc


@router.get("/health/detail", response_model=HealthDetailResponse)
def health_detail(
    check_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    stores: dict[str, Any] = Depends(get_stores),
) -> HealthDetailResponse:
    parsed_check_type = _parse_check_type(check_type)
    records = stores["pipeline_health_store"].fetch_recent(
        check_type=parsed_check_type, limit=limit
    )
    return HealthDetailResponse(
        items=[_to_pipeline_health_response(r) for r in records],
        total=len(records),
        limit=limit,
    )


@dashboard_router.get("/health/detail/", response_class=HTMLResponse)
def health_detail_page(
    request: Request,
    check_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    stores: dict[str, Any] = Depends(get_stores),
) -> HTMLResponse:
    from sapphire_flow.api import templates

    parsed_check_type = _parse_check_type(check_type)
    records = stores["pipeline_health_store"].fetch_recent(
        check_type=parsed_check_type, limit=limit
    )
    return templates.TemplateResponse(
        request,
        "health/detail.html",
        {
            "records": [_to_pipeline_health_response(r) for r in records],
            "check_type": check_type or "",
            "limit": limit,
            "check_types": [ct.value for ct in PipelineCheckType],
            "active_nav": "health",
        },
    )
