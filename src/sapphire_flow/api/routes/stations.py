from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.routes.tables import _get_reflected

router = APIRouter(tags=["stations"])


@router.get("/stations/", response_class=HTMLResponse)
def station_list(
    request: Request,
    conn: sa.Connection = Depends(get_connection),
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = _get_reflected(conn)
    stations = reflected.tables["stations"]

    # Build columns — handle geometry specially
    select_cols = []
    for col in stations.columns:
        type_str = str(col.type).upper()
        if "GEOMETRY" in type_str:
            select_cols.append(sa.func.ST_X(col).label("lon"))
            select_cols.append(sa.func.ST_Y(col).label("lat"))
        else:
            select_cols.append(col)

    rows = (
        conn.execute(sa.select(*select_cols).order_by(stations.c.code)).mappings().all()
    )

    # Count observations per station
    obs_table = reflected.tables.get("observations")
    obs_counts: dict[str, int] = {}
    if obs_table is not None:
        obs_rows = (
            conn.execute(
                sa.select(
                    obs_table.c.station_id,
                    sa.func.count().label("cnt"),
                ).group_by(obs_table.c.station_id)
            )
            .mappings()
            .all()
        )
        obs_counts = {str(r["station_id"]): r["cnt"] for r in obs_rows}

    station_list_data = []
    for r in rows:
        sid = str(r["id"])
        station_list_data.append(
            {
                "id": sid,
                "code": r["code"],
                "name": r["name"],
                "kind": r.get("station_kind", ""),
                "status": r.get("station_status", ""),
                "network": r.get("network", ""),
                "lon": round(r.get("lon", 0), 4) if r.get("lon") else "",
                "lat": round(r.get("lat", 0), 4) if r.get("lat") else "",
                "obs_count": obs_counts.get(sid, 0),
            }
        )

    return templates.TemplateResponse(
        request,
        "stations/list.html",
        {"stations": station_list_data, "active_nav": "stations"},
    )


@router.get("/stations/{station_id}/", response_class=HTMLResponse)
def station_detail(
    request: Request,
    station_id: str,
    conn: sa.Connection = Depends(get_connection),
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = _get_reflected(conn)
    stations = reflected.tables["stations"]

    select_cols = []
    for col in stations.columns:
        type_str = str(col.type).upper()
        if "GEOMETRY" in type_str:
            select_cols.append(sa.func.ST_X(col).label("lon"))
            select_cols.append(sa.func.ST_Y(col).label("lat"))
        else:
            select_cols.append(col)

    row = (
        conn.execute(sa.select(*select_cols).where(stations.c.id == station_id))
        .mappings()
        .one_or_none()
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Station not found")

    # Get available parameters from observations
    obs_table = reflected.tables.get("observations")
    parameters: list[str] = []
    if obs_table is not None:
        param_rows = (
            conn.execute(
                sa.select(sa.distinct(obs_table.c.parameter)).where(
                    obs_table.c.station_id == station_id
                )
            )
            .scalars()
            .all()
        )
        parameters = sorted(param_rows)

    # Also check for measured_parameters column
    measured = row.get("measured_parameters")
    if measured and not parameters:
        if isinstance(measured, (list, set, frozenset)):
            parameters = sorted(measured)
        else:
            parameters = []

    # Get thresholds if table exists
    thresholds: list[dict[str, object]] = []
    thresh_table = reflected.tables.get("station_thresholds")
    if thresh_table is not None:
        thresh_rows = (
            conn.execute(
                sa.select(thresh_table)
                .where(thresh_table.c.station_id == station_id)
                .order_by(thresh_table.c.parameter, thresh_table.c.danger_level)
            )
            .mappings()
            .all()
        )
        thresholds = [dict(r) for r in thresh_rows]

    station = dict(row)
    station["parameters"] = parameters

    return templates.TemplateResponse(
        request,
        "stations/detail.html",
        {
            "station": station,
            "thresholds": thresholds,
            "active_nav": "stations",
        },
    )


@router.get("/api/v1/stations/{station_id}/observations.json")
def station_observations_json(
    station_id: str,
    parameter: str = Query(...),
    start: str = Query(..., description="ISO datetime"),
    end: str = Query(..., description="ISO datetime"),
    conn: sa.Connection = Depends(get_connection),
) -> JSONResponse:
    reflected = _get_reflected(conn)
    obs = reflected.tables.get("observations")
    if obs is None:
        return JSONResponse({"timestamps": [], "values": [], "qc_statuses": []})

    start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=UTC)

    rows = (
        conn.execute(
            sa.select(obs.c.timestamp, obs.c.value, obs.c.qc_status)
            .where(
                sa.and_(
                    obs.c.station_id == station_id,
                    obs.c.parameter == parameter,
                    obs.c.timestamp >= start_dt,
                    obs.c.timestamp < end_dt,
                )
            )
            .order_by(obs.c.timestamp)
        )
        .mappings()
        .all()
    )

    return JSONResponse(
        {
            "timestamps": [r["timestamp"].isoformat() for r in rows],
            "values": [r["value"] for r in rows],
            "qc_statuses": [r["qc_status"] for r in rows],
        }
    )


@router.get("/api/v1/stations/{station_id}/forcing.json")
def station_forcing_json(
    station_id: str,
    start: str = Query(..., description="ISO datetime"),
    end: str = Query(..., description="ISO datetime"),
    conn: sa.Connection = Depends(get_connection),
) -> JSONResponse:
    reflected = _get_reflected(conn)
    forcing = reflected.tables.get("historical_forcing")
    if forcing is None:
        return JSONResponse({"timestamps": [], "series": {}})

    start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=UTC)

    rows = (
        conn.execute(
            sa.select(forcing.c.valid_time, forcing.c.parameter, forcing.c.value)
            .where(
                sa.and_(
                    forcing.c.station_id == station_id,
                    forcing.c.valid_time >= start_dt,
                    forcing.c.valid_time < end_dt,
                )
            )
            .order_by(forcing.c.valid_time)
        )
        .mappings()
        .all()
    )

    # Group by parameter
    series: dict[str, dict[str, list[object]]] = {}
    for r in rows:
        param = r["parameter"]
        if param not in series:
            series[param] = {"timestamps": [], "values": []}
        series[param]["timestamps"].append(r["valid_time"].isoformat())
        series[param]["values"].append(r["value"])

    return JSONResponse({"series": series})


@router.get("/api/v1/stations/{station_id}/baselines.json")
def station_baselines_json(
    station_id: str,
    parameter: str = Query(...),
    conn: sa.Connection = Depends(get_connection),
) -> JSONResponse:
    reflected = _get_reflected(conn)
    baselines = reflected.tables.get("clim_baselines")
    if baselines is None:
        return JSONResponse({"day_of_year": [], "rolling_mean": [], "rolling_std": []})

    rows = (
        conn.execute(
            sa.select(
                baselines.c.day_of_year,
                baselines.c.rolling_mean,
                baselines.c.rolling_std,
                baselines.c.sample_count,
            )
            .where(
                sa.and_(
                    baselines.c.station_id == station_id,
                    baselines.c.parameter == parameter,
                )
            )
            .order_by(baselines.c.day_of_year)
        )
        .mappings()
        .all()
    )

    return JSONResponse(
        {
            "day_of_year": [r["day_of_year"] for r in rows],
            "rolling_mean": [float(r["rolling_mean"]) for r in rows],
            "rolling_std": [float(r["rolling_std"]) for r in rows],
            "sample_count": [r["sample_count"] for r in rows],
        }
    )
