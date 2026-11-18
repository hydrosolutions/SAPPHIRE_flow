# ruff: noqa: TC001 -- FastAPI resolves dependency annotations at runtime.
from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from sapphire_flow.api.deps import get_connection, get_stores
from sapphire_flow.api.event_review_schemas import (
    ReviewErrorDetail,
    ReviewErrorResponse,
    ReviewOptionsResponse,
)
from sapphire_flow.api.publication_gate import PublicationGate, get_publication_gate
from sapphire_flow.api.security import (
    Principal,
    ensure_station_in_scope,
    require_reviewer,
)
from sapphire_flow.protocols.stores import EventReviewStore
from sapphire_flow.services.event_review import read_review_options
from sapphire_flow.store.event_review import PgEventReviewStore
from sapphire_flow.types.event_review import (
    MAX_REVIEW_BYTES,
    ReviewCatalogLimitError,
    ReviewOptionsQuery,
    ReviewPolicy,
    ReviewQueryTimeoutError,
)
from sapphire_flow.types.ids import StationId

router = APIRouter(prefix="/api/v1/stations", tags=["event-review"])


def get_event_review_store(
    conn: sa.Connection = Depends(get_connection),
) -> EventReviewStore:
    return PgEventReviewStore(conn)


@router.get(
    "/{station_id}/review-options",
    response_model=ReviewOptionsResponse,
    responses={
        422: {"model": ReviewErrorResponse},
        503: {"model": ReviewErrorResponse},
    },
)
def review_options(
    station_id: str,
    request: Request,
    stores: dict[str, Any] = Depends(get_stores),
    store: EventReviewStore = Depends(get_event_review_store),
    principal: Principal = Depends(require_reviewer),
    gate: PublicationGate = Depends(get_publication_gate),
) -> ReviewOptionsResponse | JSONResponse:
    try:
        sid = StationId(UUID(station_id))
    except ValueError:
        raise HTTPException(400, "Malformed station_id") from None
    ensure_station_in_scope(principal, sid)
    station = stores["station_store"].fetch_station(sid)
    if station is None or (
        not principal.is_admin and station.tenant_id != principal.tenant_id
    ):
        raise HTTPException(404, "Station not found")
    if request.query_params:
        raise HTTPException(400, "Review options accepts no query parameters")
    query = ReviewOptionsQuery(
        station_id=sid,
        tenant_id=station.tenant_id,
        policy=ReviewPolicy.PUBLISHED_ONLY
        if gate.active(station.tenant_id)
        else ReviewPolicy.ORDINARY,
    )
    try:
        result = ReviewOptionsResponse.from_domain(read_review_options(store, query))
        if len(result.model_dump_json().encode()) > MAX_REVIEW_BYTES:
            raise ReviewCatalogLimitError("Review catalog exceeds its response limit")
        return result
    except ReviewCatalogLimitError:
        error = ReviewErrorResponse(
            detail=ReviewErrorDetail(
                code="review_catalog_limit_exceeded",
                message="Review catalog exceeds its bounded read limit.",
            )
        )
        return JSONResponse(status_code=422, content=error.model_dump())
    except ReviewQueryTimeoutError:
        error = ReviewErrorResponse(
            detail=ReviewErrorDetail(
                code="review_query_timeout",
                message="Review query timed out. Retry later.",
            )
        )
        return JSONResponse(status_code=503, content=error.model_dump())
