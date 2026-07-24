from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from sapphire_flow.api.deps import get_stores
from sapphire_flow.api.schemas import AlertResponse, PaginatedResponse
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.types.enums import AlertSource, AlertStatus
from sapphire_flow.types.ids import StationId

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
    principal: Principal = Depends(require_principal),
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
        # A consumer asking for an out-of-scope station's alerts explicitly
        # sees nothing (fail-closed), not an error.
        if not principal.station_in_scope(parsed_station_id):
            return PaginatedResponse[AlertResponse](
                items=[], total=0, limit=limit, offset=offset
            )

    items, total = stores["alert_store"].fetch_alerts(
        station_id=parsed_station_id,
        source=parsed_source,
        status=parsed_status,
        level=level,
        limit=limit,
        offset=offset,
    )

    if not principal.is_admin:
        # F7 LOCKED: a consumer sees ONLY alerts whose station_id is in its
        # scope; stationless (null-station) alerts are excluded (fail-closed
        # — `station_in_scope(None)` is False for a consumer).
        items = [a for a in items if principal.station_in_scope(a.station_id)]
        total = len(items)

    return PaginatedResponse[AlertResponse](
        items=[_to_alert_response(a) for a in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str) -> None:
    """G4 LOCKED: the sole HTTP mutation is removed from the v1.0 surface.
    Access tokens are strictly GET-only (`security.md:31`); acknowledgement
    is a state change that needs a human/dashboard session token, which
    defers to v1.x with Flow 3. Kept mounted (returning 501) rather than
    unmounted so a caller gets an explicit "not implemented yet" instead of
    an ambiguous 404."""
    raise HTTPException(
        status_code=501,
        detail=(
            "Alert acknowledgement is deferred to v1.x (session auth + "
            "Flow 3 dashboard) — not available on the v1.0 access-token "
            "surface."
        ),
    )
