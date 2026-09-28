"""Plan 404 T3 — `GET /api/v1/stations/{id}/rejected-forecasts`: the REVIEW
route the flow map reads to show what forecast QC rejected. Registered
OUTSIDE Plan 402's `require_reviewer` router (its own route-level
dependency, `require_reviewer_or_human` — `api/review_auth.py`), since this
route also admits a named human with a current station `review` grant
(Plan 341), which `require_reviewer` does not.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from sapphire_flow.api.deps import get_stores
from sapphire_flow.api.human_auth import require_human_station_permission
from sapphire_flow.api.review_auth import require_reviewer_or_human
from sapphire_flow.api.routes.api_review import (
    _parse_station_id,  # pyright: ignore[reportPrivateUsage]
)
from sapphire_flow.api.schemas import (
    NonFiniteValue,
    PaginatedResponse,
    QcFlagResponse,
    RejectedForecastResponse,
    RejectedForecastValuePoint,
)
from sapphire_flow.api.security import Principal, ensure_station_in_scope
from sapphire_flow.services.publication_gate import publication_gate_active
from sapphire_flow.types.datetime import UtcDatetime
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.human_auth import HumanPermission
from sapphire_flow.types.ids import ModelId, StationId

if TYPE_CHECKING:
    from sapphire_flow.types.human_auth import HumanPrincipal
    from sapphire_flow.types.rejected_forecast import PersistedRejectedForecast

router = APIRouter(prefix="/api/v1", tags=["api-rejected-forecasts"])

# Plan 404's route contract: each item carries a full ensemble, so this
# route's own ceiling is tighter than the 200 other list routes allow.
_MAX_LIMIT = 50
_DEFAULT_LIMIT = 20
_DEFAULT_WINDOW_DAYS = 7


def _parse_datetime(value: str, field_name: str) -> UtcDatetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid datetime for {field_name}: {value}"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return UtcDatetime(dt)


def _to_response(
    row: PersistedRejectedForecast, *, withheld: bool
) -> RejectedForecastResponse:
    values: dict[str, list[RejectedForecastValuePoint]] | None = None
    if not withheld:
        values = {
            key: [
                RejectedForecastValuePoint(
                    valid_time=valid_time,
                    value=(
                        value
                        if math.isfinite(value)
                        else NonFiniteValue(nonfinite=repr(value))  # type: ignore[arg-type]
                    ),
                )
                for valid_time, value in points
            ]
            for key, points in row.values.items()
        }
    return RejectedForecastResponse(
        id=str(row.id),
        attempt_id=str(row.attempt_id),
        recorded_at=row.recorded_at,
        station_id=str(row.station_id),
        model_id=str(row.model_id),
        model_artifact_id=(
            str(row.model_artifact_id) if row.model_artifact_id is not None else None
        ),
        group_id=str(row.group_id) if row.group_id is not None else None,
        issued_at=row.issued_at,
        parameter=row.parameter,
        representation=row.representation.value,  # type: ignore[arg-type]
        units=row.units,
        time_step_seconds=row.time_step_seconds,
        qc_status=row.qc_status.value,  # type: ignore[arg-type]
        qc_flags=[
            QcFlagResponse(
                rule_id=f.rule_id,
                rule_version=f.rule_version,
                status=f.status.value,  # type: ignore[arg-type]
                detail=None if withheld else f.detail,
            )
            for f in row.qc_flags
        ],
        values=values,
        withheld=withheld,
    )


@router.get(
    "/stations/{station_id}/rejected-forecasts",
    response_model=PaginatedResponse[RejectedForecastResponse],
)
def get_rejected_forecasts(
    station_id: str,
    model_id: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    stores: dict[str, Any] = Depends(get_stores),
    principal: Principal | HumanPrincipal = Depends(require_reviewer_or_human),
) -> PaginatedResponse[RejectedForecastResponse]:
    sid = _parse_station_id(station_id)

    if isinstance(principal, Principal):
        ensure_station_in_scope(principal, sid)
    else:
        require_human_station_permission(principal, sid, HumanPermission.REVIEW)

    station = stores["station_store"].fetch_station(sid)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")

    withheld = (
        isinstance(principal, Principal)
        and principal.role is AccessTokenRole.REVIEWER
        and publication_gate_active(station.tenant_id)
    )

    now = UtcDatetime(datetime.now(UTC))
    start_dt = (
        _parse_datetime(start, "start")
        if start is not None
        else UtcDatetime(now - timedelta(days=_DEFAULT_WINDOW_DAYS))
    )
    end_dt = _parse_datetime(end, "end") if end is not None else now
    mid: ModelId | None = ModelId(model_id) if model_id is not None else None

    rows, total = stores["rejected_forecast_store"].fetch_rejected_forecasts(
        StationId(sid),
        start_dt,
        end_dt,
        model_id=mid,
        limit=limit,
        offset=offset,
    )

    return PaginatedResponse[RejectedForecastResponse](
        items=[_to_response(r, withheld=withheld) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
