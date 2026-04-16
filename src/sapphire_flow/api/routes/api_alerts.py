from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query

from sapphire_flow.api.deps import get_connection_rw, get_stores
from sapphire_flow.api.schemas import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    AlertResponse,
    PaginatedResponse,
)
from sapphire_flow.store.alert_store import PgAlertStore
from sapphire_flow.types.enums import AlertSource, AlertStatus
from sapphire_flow.types.ids import AlertId, StationId

if TYPE_CHECKING:
    from sapphire_flow.types.alert import Alert

router = APIRouter(prefix="/api/v1", tags=["api-alerts"])


def _to_alert_response(a: Alert) -> AlertResponse:
    return AlertResponse(
        id=str(a.id),
        station_id=str(a.station_id) if a.station_id is not None else None,
        source=a.source.value,
        alert_level=a.alert_level,
        status=a.status.value,
        trigger_probability=a.trigger_probability,
        trigger_value=a.trigger_value,
        triggered_at=a.triggered_at,
        acknowledged_at=a.acknowledged_at,
        acknowledged_by=str(a.acknowledged_by)
        if a.acknowledged_by is not None
        else None,
        resolved_at=a.resolved_at,
        first_detected_at=a.first_detected_at,
        model_ids=[str(mid) for mid in a.model_ids],
        alert_model_strategy=a.alert_model_strategy.value
        if a.alert_model_strategy is not None
        else None,
    )


@router.get("/alerts")
def list_alerts(
    station_id: str | None = Query(None),
    source: str | None = Query(None),
    status: str | None = Query(None),
    level: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    stores: dict[str, Any] = Depends(get_stores),
) -> PaginatedResponse[AlertResponse]:
    parsed_source: AlertSource | None = None
    if source is not None:
        try:
            parsed_source = AlertSource(source)
        except ValueError:
            valid = ", ".join(s.value for s in AlertSource)
            raise HTTPException(
                status_code=400,
                detail=f"Invalid source '{source}'. Valid values: {valid}",
            ) from None

    parsed_status: AlertStatus | None = None
    if status is not None:
        try:
            parsed_status = AlertStatus(status)
        except ValueError:
            valid = ", ".join(s.value for s in AlertStatus)
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Valid values: {valid}",
            ) from None

    parsed_station_id: StationId | None = None
    if station_id is not None:
        try:
            parsed_station_id = StationId(UUID(station_id))
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid station_id '{station_id}'"
            ) from None

    items, total = stores["alert_store"].fetch_alerts(
        station_id=parsed_station_id,
        source=parsed_source,
        status=parsed_status,
        level=level,
        limit=limit,
        offset=offset,
    )

    return PaginatedResponse[AlertResponse](
        items=[_to_alert_response(a) for a in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: str,
    body: AcknowledgeRequest,
    stores: dict[str, Any] = Depends(get_stores),
    conn_rw: sa.Connection = Depends(get_connection_rw),
) -> AcknowledgeResponse:
    try:
        parsed_alert_id = AlertId(UUID(alert_id))
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid alert_id '{alert_id}'"
        ) from None

    try:
        parsed_acknowledged_by = UUID(body.acknowledged_by)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid acknowledged_by '{body.acknowledged_by}'",
        ) from None

    alert = stores["alert_store"].fetch_alert(parsed_alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    if alert.status == AlertStatus.RESOLVED:
        raise HTTPException(
            status_code=409, detail="Cannot acknowledge a resolved alert"
        )

    rw_store = PgAlertStore(conn_rw)
    rw_store.acknowledge_alert(parsed_alert_id, parsed_acknowledged_by)

    updated = rw_store.fetch_alert(parsed_alert_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Alert not found after update")

    return AcknowledgeResponse(
        id=str(updated.id),
        status=updated.status.value,
        acknowledged_at=updated.acknowledged_at,  # type: ignore[arg-type]
    )
