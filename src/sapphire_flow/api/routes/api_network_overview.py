# ruff: noqa: TC001, TC003 -- FastAPI resolves boundary annotations at runtime.
from __future__ import annotations

import re
from datetime import datetime
from email.utils import format_datetime

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.api.deps import (
    get_connection,
    get_deployment_config,
    get_observation_clock,
)
from sapphire_flow.api.network_overview import NetworkOverviewReader
from sapphire_flow.api.network_overview_cache import (
    PRIVATE_OVERVIEW_CACHE,
    etag_matches,
    overview_etag,
)
from sapphire_flow.api.network_overview_schemas import NetworkOverviewResponse
from sapphire_flow.api.publication_gate import PublicationGate, get_publication_gate
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.exceptions import StoreError
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ForecastDataUse

router = APIRouter(prefix="/api/v1/networks", tags=["network-overview"])


@router.get(
    "/{network}/overview",
    response_model=NetworkOverviewResponse,
    responses={304: {"description": "Authorized representation unchanged"}},
)
def network_overview(
    network: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_principal),
    conn: sa.Connection = Depends(get_connection),
    config: DeploymentConfig | None = Depends(get_deployment_config),
    gate: PublicationGate = Depends(get_publication_gate),
    now: datetime = Depends(get_observation_clock),
) -> NetworkOverviewResponse | Response:
    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", network) is None:
        raise HTTPException(422, "Invalid network identity")
    if set(request.query_params) - {"data_use"}:
        raise HTTPException(422, "Unsupported overview query")
    raw = request.query_params.getlist("data_use") or ["standard"]
    if (
        len(raw) > 2
        or len(set(raw)) != len(raw)
        or any(value not in {"standard", "provisional_rating"} for value in raw)
    ):
        raise HTTPException(422, "Invalid overview data use")
    purposes = tuple(
        sorted((ForecastDataUse(value) for value in raw), key=lambda item: item.value)
    )
    if ForecastDataUse.PROVISIONAL_RATING in purposes and not principal.can_review:
        raise HTTPException(403, "reviewer role required")
    try:
        body = NetworkOverviewReader(conn, principal, config, gate).read(
            network, purposes, ensure_utc(now)
        )
        tag = overview_etag(body, principal, purposes)
    except (ValueError, StoreError, SQLAlchemyError) as exc:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        structlog.get_logger().error(
            "network_overview_unavailable",
            category="database"
            if isinstance(exc, SQLAlchemyError)
            else "evidence_or_admission",
            sqlstate=sqlstate
            if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate)
            else None,
        )
        raise HTTPException(503, "Network overview is unavailable") from None
    headers = {
        "ETag": tag,
        "Cache-Control": PRIVATE_OVERVIEW_CACHE,
        "Vary": "Authorization, Origin, Accept-Encoding",
        "Date": format_datetime(now, usegmt=True),
    }
    if etag_matches(request.headers.get("if-none-match"), tag):
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return body
