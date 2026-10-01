from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from sapphire_flow.api.deps import get_stores
from sapphire_flow.api.human_auth import (
    require_human_principal,
    require_human_station_permission,
)
from sapphire_flow.api.publication_gate import (
    PublicationGate,
    get_publication_gate,
    require_active_publication,
    require_publication_store,
    station_tenant_id,
)
from sapphire_flow.api.publication_views import (
    decision_response,
    make_cursor,
    parse_cursor,
    publication_metadata,
)
from sapphire_flow.api.routes.api_forecasts import to_forecast_detail
from sapphire_flow.api.routes.api_stations import parse_api_datetime
from sapphire_flow.api.schemas import (
    ForecastDetail,
    PaginatedResponse,
    PublicationChangeEvent,
    PublicationChangePage,
    PublicationDecisionResponse,
    PublicationHistoryItem,
    PublicationHistoryPage,
    PublishForecastRequest,
    ReviewForecast,
    WithdrawForecastRequest,
)
from sapphire_flow.api.security import (
    Principal,
    ensure_station_in_scope,
    require_principal,
)
from sapphire_flow.store.forecast_publication_store import (
    PublicationConflictError,
    PublicationForbiddenError,
    PublicationNotFoundError,
    PublicationUnavailableError,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.forecast_publication import (
    PublishRequest,
    WithdrawalReasonCode,
    WithdrawalState,
    WithdrawRequest,
)
from sapphire_flow.types.human_auth import HumanPermission, HumanPrincipal
from sapphire_flow.types.ids import ForecastId, PublicationDecisionId, StationId

router = APIRouter(prefix="/api/v1", tags=["forecast-publication"])


def publication_clock() -> Callable[[], UtcDatetime]:
    return lambda: ensure_utc(datetime.now(UTC))


def new_publication_decision_id() -> PublicationDecisionId:
    return PublicationDecisionId(uuid4())


def _human_forecast(
    forecast_id: ForecastId,
    stores: dict[str, Any],
    principal: HumanPrincipal,
    permission: HumanPermission,
) -> Any:
    forecast = stores["forecast_store"].fetch_forecast(forecast_id)
    if forecast is None or forecast.data_use is not ForecastDataUse.STANDARD:
        raise HTTPException(status_code=404, detail="Forecast not found")
    if not principal.allows(forecast.station_id, permission):
        raise HTTPException(status_code=404, detail="Forecast not found")
    tenant_id = station_tenant_id(stores, forecast.station_id)
    if tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="Forecast not found")
    return forecast


def _review_forecast(
    forecast: Any, stores: dict[str, Any], now: UtcDatetime
) -> ReviewForecast:
    tenant_id = station_tenant_id(stores, forecast.station_id)
    pub = require_publication_store(stores)
    metadata, decisions = publication_metadata(pub, forecast, tenant_id)
    assessment = pub.assess_candidate(forecast.id, now)
    reasons = list(assessment.remaining_reasons)
    if forecast.status.value == "superseded":
        reasons.append("superseded forecast is not a publication candidate")
    if forecast.qc_status.value == "qc_failed":
        reasons.append("QC-failed forecast cannot be published")
    if metadata["publication_state"] == "withdrawn":
        reasons.append("withdrawn forecast cannot be republished")
    if metadata["publication_state"] == "selected":
        reasons.append("forecast is already selected")
    detail = to_forecast_detail(forecast).model_copy(update=metadata)
    return ReviewForecast(
        **detail.model_dump(),
        capture_status=assessment.capture_status,
        effective_preservation_status=assessment.preservation_status,
        attestation_id=assessment.attestation_id,
        remaining_reasons=reasons,
        decisions=[decision_response(d) for d in decisions],
    )


@router.get("/review/forecasts", response_model=PaginatedResponse[ReviewForecast])
def list_review_forecasts(
    station_id: UUID,
    start: str,
    end: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    stores: dict[str, Any] = Depends(get_stores),
    principal: HumanPrincipal = Depends(require_human_principal),
    clock: Callable[[], UtcDatetime] = Depends(publication_clock),
) -> PaginatedResponse[ReviewForecast]:
    sid = StationId(station_id)
    require_human_station_permission(principal, sid, HumanPermission.REVIEW)
    if station_tenant_id(stores, sid) != principal.tenant_id:
        raise HTTPException(status_code=404, detail="Station not found")
    start_dt = parse_api_datetime(start, "start")
    end_dt = parse_api_datetime(end, "end")
    if end_dt <= start_dt or end_dt - start_dt > timedelta(days=31):
        raise HTTPException(status_code=400, detail="Review range must be 1-31 days")
    rows, total = stores["forecast_store"].fetch_forecast_summaries(
        sid, start_dt, end_dt, limit=limit, offset=offset
    )
    now = clock()
    forecasts = [stores["forecast_store"].fetch_forecast(row.id) for row in rows]
    return PaginatedResponse[ReviewForecast](
        items=[_review_forecast(f, stores, now) for f in forecasts if f is not None],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/review/forecasts/{forecast_id}", response_model=ReviewForecast)
def get_review_forecast(
    forecast_id: UUID,
    stores: dict[str, Any] = Depends(get_stores),
    principal: HumanPrincipal = Depends(require_human_principal),
    clock: Callable[[], UtcDatetime] = Depends(publication_clock),
) -> ReviewForecast:
    forecast = _human_forecast(
        ForecastId(forecast_id), stores, principal, HumanPermission.REVIEW
    )
    return _review_forecast(forecast, stores, clock())


def _decision_or_http(action: Callable[[], Any]) -> PublicationDecisionResponse:
    try:
        return decision_response(action())
    except PublicationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (PublicationNotFoundError, PublicationForbiddenError) as exc:
        raise HTTPException(status_code=404, detail="Forecast not found") from exc
    except PublicationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/review/forecasts/{forecast_id}/publish",
    response_model=PublicationDecisionResponse,
)
def publish_forecast(
    forecast_id: UUID,
    body: PublishForecastRequest,
    stores: dict[str, Any] = Depends(get_stores),
    principal: HumanPrincipal = Depends(require_human_principal),
    gate: PublicationGate = Depends(get_publication_gate),
    clock: Callable[[], UtcDatetime] = Depends(publication_clock),
    decision_id: PublicationDecisionId = Depends(new_publication_decision_id),
) -> PublicationDecisionResponse:
    forecast = _human_forecast(
        ForecastId(forecast_id), stores, principal, HumanPermission.PUBLISH
    )
    require_active_publication(station_tenant_id(stores, forecast.station_id), gate)
    request = PublishRequest(
        forecast_id=forecast.id,
        expected_forecast_version=body.expected_forecast_version,
        expected_selection_version=body.expected_selection_version,
        idempotency_key=body.idempotency_key,
    )
    return _decision_or_http(
        lambda: require_publication_store(stores).publish(
            request, principal, decision_id=decision_id, now=clock()
        )
    )


@router.post(
    "/review/forecasts/{forecast_id}/withdraw",
    response_model=PublicationDecisionResponse,
)
def withdraw_forecast(
    forecast_id: UUID,
    body: WithdrawForecastRequest,
    stores: dict[str, Any] = Depends(get_stores),
    principal: HumanPrincipal = Depends(require_human_principal),
    gate: PublicationGate = Depends(get_publication_gate),
    clock: Callable[[], UtcDatetime] = Depends(publication_clock),
    decision_id: PublicationDecisionId = Depends(new_publication_decision_id),
) -> PublicationDecisionResponse:
    forecast = _human_forecast(
        ForecastId(forecast_id), stores, principal, HumanPermission.PUBLISH
    )
    require_active_publication(station_tenant_id(stores, forecast.station_id), gate)
    request = WithdrawRequest(
        forecast_id=forecast.id,
        expected_selection_version=body.expected_selection_version,
        reason_code=WithdrawalReasonCode(body.reason_code),
        reason_text=body.reason_text,
        idempotency_key=body.idempotency_key,
    )
    return _decision_or_http(
        lambda: require_publication_store(stores).withdraw(
            request, principal, decision_id=decision_id, now=clock()
        )
    )


@router.get(
    "/stations/{station_id}/forecasts/latest-published", response_model=ForecastDetail
)
def latest_published_forecast(
    station_id: UUID,
    parameter: str,
    stores: dict[str, Any] = Depends(get_stores),
    principal: Principal = Depends(require_principal),
    gate: PublicationGate = Depends(get_publication_gate),
) -> ForecastDetail:
    sid = StationId(station_id)
    ensure_station_in_scope(principal, sid)
    tenant_id = station_tenant_id(stores, sid)
    require_active_publication(tenant_id, gate)
    pub = require_publication_store(stores)
    pub.lock_read_snapshot()
    forecast_id = pub.fetch_latest_selected_id(sid, parameter)
    forecast = (
        stores["forecast_store"].fetch_forecast(forecast_id)
        if forecast_id is not None
        else None
    )
    if forecast is None:
        raise HTTPException(status_code=404, detail="Forecast not found")
    metadata, _ = publication_metadata(pub, forecast, tenant_id)
    return to_forecast_detail(forecast).model_copy(update=metadata)


@router.get(
    "/stations/{station_id}/forecast-publications",
    response_model=PublicationHistoryPage,
)
def publication_history(
    station_id: UUID,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    stores: dict[str, Any] = Depends(get_stores),
    principal: Principal = Depends(require_principal),
    gate: PublicationGate = Depends(get_publication_gate),
) -> PublicationHistoryPage:
    sid = StationId(station_id)
    ensure_station_in_scope(principal, sid)
    tenant_id = station_tenant_id(stores, sid)
    require_active_publication(tenant_id, gate)
    after = parse_cursor(cursor)
    pub = require_publication_store(stores)
    pub.lock_read_snapshot()
    events = pub.fetch_events(
        after_sequence=after,
        limit=limit,
        tenant_ids=frozenset({tenant_id}),
        station_ids=frozenset({sid}),
    )
    items: list[PublicationHistoryItem] = []
    for event in events:
        decision = event.decision
        forecast = (
            stores["forecast_store"].fetch_forecast(decision.forecast_id)
            if event.withdrawal_state is WithdrawalState.NOT_WITHDRAWN
            else None
        )
        metadata: dict[str, Any] = {}
        if forecast is not None:
            metadata, _ = publication_metadata(pub, forecast, tenant_id)
        state = (
            "withdrawn"
            if event.withdrawal_state is WithdrawalState.WITHDRAWN
            else metadata["publication_state"]
        )
        items.append(
            PublicationHistoryItem(
                sequence=event.sequence,
                event_type=event.event_type.value,
                decision=decision_response(decision),
                forecast=(
                    to_forecast_detail(forecast).model_copy(update=metadata)
                    if forecast is not None
                    else None
                ),
                publication_state=state,
            )
        )
    return PublicationHistoryPage(
        items=items,
        next_cursor=make_cursor(events[-1].sequence if events else after),
    )


@router.get("/forecast-publications", response_model=PublicationChangePage)
def publication_changes(
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    stores: dict[str, Any] = Depends(get_stores),
    principal: Principal = Depends(require_principal),
    gate: PublicationGate = Depends(get_publication_gate),
) -> PublicationChangePage:
    after = parse_cursor(cursor)
    pub = require_publication_store(stores)
    pub.lock_read_snapshot()
    events = pub.fetch_events(
        after_sequence=after,
        limit=limit,
        tenant_ids=gate.active_tenant_ids,
        station_ids=None if principal.is_admin else principal.station_ids,
    )
    return PublicationChangePage(
        items=[
            PublicationChangeEvent(
                sequence=e.sequence,
                event_type=e.event_type.value,
                decision_id=str(e.decision.id),
                station_id=str(e.decision.key.station_id),
                forecast_id=str(e.decision.forecast_id),
                replaced_forecast_id=(
                    str(e.decision.replaced_forecast_id)
                    if e.decision.replaced_forecast_id
                    else None
                ),
                created_at=e.decision.created_at,
                withdrawn=e.withdrawal_state is WithdrawalState.WITHDRAWN,
            )
            for e in events
        ],
        next_cursor=make_cursor(events[-1].sequence if events else after),
    )
