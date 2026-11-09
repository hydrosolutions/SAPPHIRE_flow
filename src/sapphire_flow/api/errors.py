from __future__ import annotations

import re

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from sapphire_flow.api.publication_cache import shared_forecast_read_path


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
            shared_forecast_read_path(request.url.path)
            or re.fullmatch(
                r"/api/v1/forecasts/[^/]+/rating-input-history/?", request.url.path
            )
            or request.url.path
            in {
                "/api/v1/forecast-lab/snapshot",
                "/api/v1/observations/latest",
                "/api/v1/swiss/overview",
                "/api/v1/swiss/observation-history",
            }
            or re.fullmatch(
                r"/api/v1/stations/[^/]+/evaluation-forecasts", request.url.path
            )
        )
        else None,
    )
