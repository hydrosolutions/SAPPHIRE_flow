from __future__ import annotations

import re

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "detail": None},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": None},
        headers={"Cache-Control": "no-store"}
        if (
            request.url.path
            in {"/api/v1/forecast-lab/snapshot", "/api/v1/observations/latest"}
            or re.fullmatch(
                r"/api/v1/stations/[^/]+/evaluation-forecasts", request.url.path
            )
        )
        else None,
    )
