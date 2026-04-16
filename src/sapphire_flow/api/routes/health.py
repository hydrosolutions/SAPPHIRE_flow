from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends

from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.schemas import HealthResponse

router = APIRouter(prefix="/api/v1", tags=["health"])

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
