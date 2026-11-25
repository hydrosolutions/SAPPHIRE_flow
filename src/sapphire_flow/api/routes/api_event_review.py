# ruff: noqa: TC001 -- FastAPI resolves dependency annotations at runtime.
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from sapphire_flow.api.deps import get_connection, get_evaluation_clock, get_stores
from sapphire_flow.api.event_review_schemas import (
    ReviewErrorDetail,
    ReviewErrorResponse,
    ReviewOptionsResponse,
    ReviewSeriesParameters,
    ReviewSeriesResponse,
)
from sapphire_flow.api.publication_gate import PublicationGate, get_publication_gate
from sapphire_flow.api.security import (
    Principal,
    ensure_station_in_scope,
    require_reviewer,
)
from sapphire_flow.protocols.stores import EventReviewStore
from sapphire_flow.services.event_review import read_review_options, read_review_series
from sapphire_flow.store.event_review import PgEventReviewStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.event_review import (
    MAX_REVIEW_BYTES,
    ReviewCatalogLimitError,
    ReviewErrorCode,
    ReviewOptionsQuery,
    ReviewPolicy,
    ReviewQueryTimeoutError,
    ReviewSeriesError,
    ReviewSeriesQuery,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId

router = APIRouter(prefix="/api/v1/stations", tags=["event-review"])
_QUERY_SCHEMA = ReviewSeriesParameters.model_json_schema(
    ref_template="#/components/schemas/{model}"
)


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


def _series_error(
    code: ReviewErrorCode, message: str, status: int = 422
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ReviewErrorResponse(
            detail=ReviewErrorDetail(code=code, message=message)
        ).model_dump(),
    )


@router.get(
    "/{station_id}/review-series",
    response_model=ReviewSeriesResponse,
    responses={
        422: {"model": ReviewErrorResponse},
        503: {"model": ReviewErrorResponse},
    },
    openapi_extra={
        "parameters": [
            {
                "name": name,
                "in": "query",
                "required": name in _QUERY_SCHEMA["required"],
                "schema": schema,
            }
            for name, schema in _QUERY_SCHEMA["properties"].items()
        ]
    },
)
def review_series(
    station_id: str,
    request: Request,
    stores: dict[str, Any] = Depends(get_stores),
    store: EventReviewStore = Depends(get_event_review_store),
    principal: Principal = Depends(require_reviewer),
    gate: PublicationGate = Depends(get_publication_gate),
    now: datetime = Depends(get_evaluation_clock),
) -> ReviewSeriesResponse | JSONResponse:
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
    try:
        if any(
            len(request.query_params.getlist(key)) != 1 for key in request.query_params
        ):
            raise ValueError("Repeated review parameter")
        params = ReviewSeriesParameters.model_validate(dict(request.query_params))
        query = ReviewSeriesQuery(
            scope=ReviewOptionsQuery(
                station_id=sid,
                tenant_id=station.tenant_id,
                policy=ReviewPolicy.PUBLISHED_ONLY
                if gate.active(station.tenant_id)
                else ReviewPolicy.ORDINARY,
            ),
            model_id=ModelId(params.model_id),
            lead_hours=params.lead_hours,
            start=ensure_utc(params.start),
            end=ensure_utc(params.end),
            requested_at=ensure_utc(now),
            source_mode=params.source_mode,
            hindcast_run_id=params.hindcast_run_id,
            hindcast_artifact_id=ArtifactId(params.hindcast_artifact_id)
            if params.hindcast_artifact_id
            else None,
            forcing_type=params.forcing_type,
            time_step_seconds=params.time_step_seconds,
        )
    except (ValidationError, ValueError, OverflowError):
        return _series_error(
            "invalid_review_query",
            "Invalid review parameters; use an aware half-open period of at most "
            "366 days, ending no later than now.",
        )
    try:
        response = ReviewSeriesResponse.from_domain(
            read_review_series(
                store, query, tuple(stores["station_store"].fetch_thresholds(sid))
            )
        )
        if len(response.model_dump_json().encode()) > MAX_REVIEW_BYTES:
            raise ReviewSeriesError(
                "review_series_limit_exceeded",
                "Response exceeds 2 MiB; narrow the period.",
            )
        return response
    except ReviewSeriesError as exc:
        if exc.code == "review_source_not_found":
            raise HTTPException(404, "Review source not found") from None
        return _series_error(exc.code, str(exc))
    except ReviewQueryTimeoutError:
        return _series_error(
            "review_query_timeout", "Review query timed out. Retry later.", 503
        )
