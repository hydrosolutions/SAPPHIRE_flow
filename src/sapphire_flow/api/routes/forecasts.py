from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.routes.tables import PAGE_SIZE, get_reflected

router = APIRouter(tags=["forecasts"])


@router.get("/forecasts/", response_class=HTMLResponse)
def forecast_list(
    request: Request,
    station_id: str | None = Query(None),
    page: int = Query(0, ge=0),
    conn: sa.Connection = Depends(get_connection),
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = get_reflected(conn)
    forecasts = reflected.tables.get("forecasts")

    rows: list[dict[str, Any]] = []
    total = 0
    has_forecasts = forecasts is not None

    if has_forecasts:
        q = sa.select(forecasts).order_by(forecasts.c.issued_at.desc())
        count_q = sa.select(sa.func.count()).select_from(forecasts)

        if station_id:
            q = q.where(forecasts.c.station_id == station_id)
            count_q = count_q.where(forecasts.c.station_id == station_id)

        total = conn.execute(count_q).scalar_one()
        offset = page * PAGE_SIZE
        raw = conn.execute(q.limit(PAGE_SIZE).offset(offset)).mappings().all()
        rows = [dict(r) for r in raw]

    # Also get hindcast counts
    hindcasts = reflected.tables.get("hindcast_forecasts")
    hindcast_count = 0
    if hindcasts is not None:
        hindcast_count = conn.execute(
            sa.select(sa.func.count()).select_from(hindcasts)
        ).scalar_one()

    return templates.TemplateResponse(
        request,
        "forecasts/list.html",
        {
            "forecasts": rows,
            "total": total,
            "page": page,
            "has_next": (page + 1) * PAGE_SIZE < total,
            "station_id": station_id or "",
            "hindcast_count": hindcast_count,
            "has_forecasts": has_forecasts,
            "active_nav": "forecasts",
        },
    )


@router.get("/forecasts/{forecast_id}/", response_class=HTMLResponse)
def forecast_detail(
    request: Request,
    forecast_id: str,
    conn: sa.Connection = Depends(get_connection),
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = get_reflected(conn)
    forecasts = reflected.tables.get("forecasts")
    if forecasts is None:
        raise HTTPException(status_code=404, detail="Not found")

    row = (
        conn.execute(sa.select(forecasts).where(forecasts.c.id == forecast_id))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Forecast not found")

    forecast = dict(row)

    # Get forecast values
    fv = reflected.tables.get("forecast_values")
    values: list[dict[str, Any]] = []
    if fv is not None:
        raw = (
            conn.execute(
                sa.select(fv)
                .where(fv.c.forecast_id == forecast_id)
                .order_by(fv.c.lead_time_hours, fv.c.member_id)
            )
            .mappings()
            .all()
        )
        values = [dict(r) for r in raw]

    return templates.TemplateResponse(
        request,
        "forecasts/detail.html",
        {
            "forecast": forecast,
            "values": values,
            "active_nav": "forecasts",
        },
    )


@router.get("/api/v1/forecasts/{forecast_id}/data.json")
def forecast_data_json(
    forecast_id: str,
    conn: sa.Connection = Depends(get_connection),
) -> JSONResponse:
    reflected = get_reflected(conn)
    forecasts = reflected.tables.get("forecasts")
    fv = reflected.tables.get("forecast_values")
    if forecasts is None or fv is None:
        return JSONResponse({"lead_times": [], "members": {}})

    # Get forecast metadata
    forecast = (
        conn.execute(sa.select(forecasts).where(forecasts.c.id == forecast_id))
        .mappings()
        .one_or_none()
    )
    if forecast is None:
        return JSONResponse({"lead_times": [], "members": {}})

    rows = (
        conn.execute(
            sa.select(fv)
            .where(fv.c.forecast_id == forecast_id)
            .order_by(fv.c.lead_time_hours, fv.c.member_id)
        )
        .mappings()
        .all()
    )

    representation = forecast.get("representation", "members")

    units = forecast.get("units")
    issued_at = forecast["issued_at"].isoformat()

    if representation == "quantiles":
        # Group by quantile
        quantiles: dict[str, dict[str, list[Any]]] = {}
        for r in rows:
            q = str(r.get("quantile", r.get("member_id", "?")))
            if q not in quantiles:
                quantiles[q] = {"lead_times": [], "valid_times": [], "values": []}
            quantiles[q]["lead_times"].append(r["lead_time_hours"])
            quantiles[q]["valid_times"].append(r["valid_time"].isoformat())
            quantiles[q]["values"].append(r["value"])
        return JSONResponse(
            {
                "representation": "quantiles",
                "quantiles": quantiles,
                "units": units,
                "issued_at": issued_at,
            }
        )
    else:
        # Group by member
        members: dict[str, dict[str, list[Any]]] = {}
        for r in rows:
            m = str(r.get("member_id", "?"))
            if m not in members:
                members[m] = {"lead_times": [], "valid_times": [], "values": []}
            members[m]["lead_times"].append(r["lead_time_hours"])
            members[m]["valid_times"].append(r["valid_time"].isoformat())
            members[m]["values"].append(r["value"])
        return JSONResponse(
            {
                "representation": "members",
                "members": members,
                "units": units,
                "issued_at": issued_at,
            }
        )
