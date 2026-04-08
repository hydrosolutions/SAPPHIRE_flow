from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from sapphire_flow.api.deps import get_connection

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health")
def health(conn: sa.Connection = Depends(get_connection)) -> dict[str, str]:
    conn.execute(sa.text("SELECT 1"))
    return {"status": "ok"}
