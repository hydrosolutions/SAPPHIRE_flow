from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sapphire_flow.api.deps import get_connection, get_stores
from sapphire_flow.api.model_visibility import (
    model_tier_for_model_id,
    station_has_active_floor,
)
from sapphire_flow.api.routes.tables import get_reflected
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import ModelId, StationId

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import HistoricalForcingStore, StationStore

router = APIRouter(tags=["stations"])


@router.get("/observations/", response_class=HTMLResponse)
def observation_coverage(
    request: Request,
    conn: sa.Connection = Depends(get_connection),
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = get_reflected(conn)
    obs_table = reflected.tables.get("observations")
    stations_table = reflected.tables.get("stations")

    if obs_table is None or stations_table is None:
        return templates.TemplateResponse(
            request,
            "observations/coverage.html",
            {"rows": [], "parameters": [], "active_nav": "coverage"},
        )

    # Get coverage: station_id, parameter, count, min/max timestamp
    cov_rows = (
        conn.execute(
            sa.select(
                obs_table.c.station_id,
                obs_table.c.parameter,
                sa.func.count().label("cnt"),
                sa.func.min(obs_table.c.timestamp).label("min_ts"),
                sa.func.max(obs_table.c.timestamp).label("max_ts"),
            ).group_by(obs_table.c.station_id, obs_table.c.parameter)
        )
        .mappings()
        .all()
    )

    # Get station info
    stn_rows = (
        conn.execute(
            sa.select(
                stations_table.c.id,
                stations_table.c.code,
                stations_table.c.name,
            ).order_by(stations_table.c.code)
        )
        .mappings()
        .all()
    )
    stn_info = {str(r["id"]): {"code": r["code"], "name": r["name"]} for r in stn_rows}

    # Build coverage matrix
    all_params: set[str] = set()
    coverage: dict[str, dict[str, dict[str, object]]] = {}
    for r in cov_rows:
        sid = str(r["station_id"])
        param = r["parameter"]
        all_params.add(param)
        if sid not in coverage:
            coverage[sid] = {}
        coverage[sid][param] = {
            "count": r["cnt"],
            "min_date": r["min_ts"].strftime("%Y-%m-%d") if r["min_ts"] else "",
            "max_date": r["max_ts"].strftime("%Y-%m-%d") if r["max_ts"] else "",
        }

    parameters = sorted(all_params)

    # Build rows sorted by station code
    rows = []
    for sid, info in sorted(stn_info.items(), key=lambda x: x[1]["code"]):
        if sid not in coverage:
            continue
        rows.append(
            {
                "id": sid,
                "code": info["code"],
                "name": info["name"],
                "cells": coverage[sid],
            }
        )

    return templates.TemplateResponse(
        request,
        "observations/coverage.html",
        {"rows": rows, "parameters": parameters, "active_nav": "coverage"},
    )


@router.get("/stations/", response_class=HTMLResponse)
def station_list(
    request: Request,
    conn: sa.Connection = Depends(get_connection),
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = get_reflected(conn)
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

    reflected = get_reflected(conn)
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
    station["no_floor"] = not station_has_active_floor(
        station_id=StationId(station["id"]),
        stores={},
        conn=conn,
    )

    # Basin info
    basin: dict[str, object] | None = None
    if station.get("basin_id"):
        basin_table = reflected.tables.get("basins")
        if basin_table is not None:
            basin_row = (
                conn.execute(
                    sa.select(
                        basin_table.c.code,
                        basin_table.c.name,
                        basin_table.c.area_km2,
                    ).where(basin_table.c.id == station["basin_id"])
                )
                .mappings()
                .one_or_none()
            )
            if basin_row:
                basin = dict(basin_row)

    # Weather sources
    weather_sources: list[dict[str, object]] = []
    ws_table = reflected.tables.get("station_weather_sources")
    if ws_table is not None:
        ws_rows = (
            conn.execute(sa.select(ws_table).where(ws_table.c.station_id == station_id))
            .mappings()
            .all()
        )
        weather_sources = [dict(r) for r in ws_rows]

    # Flow regime config (latest version)
    flow_regime: dict[str, object] | None = None
    frc_table = reflected.tables.get("flow_regime_configs")
    if frc_table is not None:
        frc_row = (
            conn.execute(
                sa.select(frc_table)
                .where(frc_table.c.station_id == station_id)
                .order_by(frc_table.c.version.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if frc_row:
            flow_regime = dict(frc_row)

    # Model assignments
    model_assignments: list[dict[str, object]] = []
    ma_table = reflected.tables.get("model_assignments")
    models_table = reflected.tables.get("models")
    if ma_table is not None:
        if models_table is not None:
            ma_rows = (
                conn.execute(
                    sa.select(
                        ma_table.c.model_id,
                        ma_table.c.priority,
                        ma_table.c.status,
                        ma_table.c.time_step,
                        models_table.c.display_name,
                    )
                    .join(models_table, ma_table.c.model_id == models_table.c.id)
                    .where(ma_table.c.station_id == station_id)
                    .order_by(ma_table.c.priority)
                )
                .mappings()
                .all()
            )
        else:
            ma_rows = (
                conn.execute(
                    sa.select(ma_table)
                    .where(ma_table.c.station_id == station_id)
                    .order_by(ma_table.c.priority)
                )
                .mappings()
                .all()
            )
        model_assignments = [dict(r) for r in ma_rows]
        for assignment in model_assignments:
            model_id = assignment.get("model_id")
            assignment["model_tier"] = model_tier_for_model_id(
                ModelId(str(model_id)) if model_id is not None else None
            ).value

    # Hindcast summary
    hindcast_summary: dict[str, object] | None = None
    hf_table = reflected.tables.get("hindcast_forecasts")
    if hf_table is not None:
        hs_row = (
            conn.execute(
                sa.select(
                    sa.func.count().label("count"),
                    sa.func.min(hf_table.c.hindcast_step).label("min_step"),
                    sa.func.max(hf_table.c.hindcast_step).label("max_step"),
                ).where(hf_table.c.station_id == station_id)
            )
            .mappings()
            .one()
        )
        if hs_row["count"] > 0:
            mn = hs_row["min_step"]
            mx = hs_row["max_step"]
            hindcast_summary = {
                "count": hs_row["count"],
                "min_step": mn.strftime("%Y-%m-%d") if mn else None,
                "max_step": mx.strftime("%Y-%m-%d") if mx else None,
            }

    # Skill summary (current freshness, grouped by metric)
    skill_summary: list[dict[str, object]] = []
    sk_table = reflected.tables.get("skill_scores")
    if sk_table is not None:
        sk_rows = (
            conn.execute(
                sa.select(
                    sk_table.c.metric,
                    sa.func.avg(sk_table.c.score).label("avg_score"),
                    sa.func.count().label("cnt"),
                )
                .where(
                    sa.and_(
                        sk_table.c.station_id == station_id,
                        sk_table.c.freshness == "current",
                    )
                )
                .group_by(sk_table.c.metric)
            )
            .mappings()
            .all()
        )
        skill_summary = [
            {
                "metric": r["metric"],
                "avg_score": round(float(r["avg_score"]), 4),
                "count": r["cnt"],
            }
            for r in sk_rows
        ]

    return templates.TemplateResponse(
        request,
        "stations/detail.html",
        {
            "station": station,
            "thresholds": thresholds,
            "basin": basin,
            "weather_sources": weather_sources,
            "flow_regime": flow_regime,
            "model_assignments": model_assignments,
            "hindcast_summary": hindcast_summary,
            "skill_summary": skill_summary,
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
    reflected = get_reflected(conn)
    obs = reflected.tables.get("observations")
    if obs is None:
        return JSONResponse({"timestamps": [], "values": [], "qc_statuses": []})

    try:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
        end_dt = datetime.fromisoformat(end).replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid datetime format") from exc
    if (end_dt - start_dt).days > 25 * 366:
        raise HTTPException(
            status_code=400, detail="Date range exceeds 25-year maximum"
        )

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
    stores: dict[str, Any] = Depends(get_stores),
) -> JSONResponse:
    # Plan 115b4 §6D: HYBRID-RESOLVED, not the raw un-prioritized merge of
    # every provenance stream — this is exactly what a forecast used, with
    # the winning `source` tag per point so an operator can spot a
    # stuck/preliminary tail.
    from sapphire_flow.adapters.hybrid_reanalysis_factories import (
        DEFAULT_PARAMETERS,
        select_reanalysis_source,
    )

    try:
        start_dt = ensure_utc(datetime.fromisoformat(start).replace(tzinfo=UTC))
        end_dt = ensure_utc(datetime.fromisoformat(end).replace(tzinfo=UTC))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid datetime format") from exc
    if (end_dt - start_dt).days > 25 * 366:
        raise HTTPException(
            status_code=400, detail="Date range exceeds 25-year maximum"
        )

    station_store = cast("StationStore", stores["station_store"])
    forcing_store = cast("HistoricalForcingStore", stores["forcing_store"])

    bindings = station_store.fetch_reanalysis_bindings(StationId(UUID(station_id)))
    if not bindings:
        return JSONResponse({"series": {}})

    reanalysis_source = select_reanalysis_source(
        forcing_store=forcing_store, mode="hybrid"
    )
    rows = reanalysis_source.fetch_reanalysis(
        station_configs=bindings,
        start=start_dt,
        end=end_dt,
        parameters=list(DEFAULT_PARAMETERS),
    )

    # Group by parameter, carrying the winning source tag per point.
    series: dict[str, dict[str, list[object]]] = {}
    for r in sorted(rows, key=lambda r: r.valid_time):
        param = r.parameter
        if param not in series:
            series[param] = {"timestamps": [], "values": [], "sources": []}
        series[param]["timestamps"].append(r.valid_time.isoformat())
        series[param]["values"].append(r.value)
        series[param]["sources"].append(r.source)

    return JSONResponse({"series": series})


@router.get("/api/v1/stations/{station_id}/baselines.json")
def station_baselines_json(
    station_id: str,
    parameter: str = Query(...),
    conn: sa.Connection = Depends(get_connection),
) -> JSONResponse:
    reflected = get_reflected(conn)
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


@router.get("/api/v1/stations/{station_id}/hindcasts.json")
def station_hindcasts_json(
    station_id: str,
    parameter: str = Query(...),
    start: str = Query(..., description="ISO datetime"),
    end: str = Query(..., description="ISO datetime"),
    conn: sa.Connection = Depends(get_connection),
) -> JSONResponse:
    reflected = get_reflected(conn)
    hf = reflected.tables.get("hindcast_forecasts")
    hv = reflected.tables.get("hindcast_values")
    obs = reflected.tables.get("observations")

    empty: dict[str, object] = {
        "observed": {"timestamps": [], "values": []},
        "hindcast_steps": [],
    }
    if hf is None or hv is None:
        return JSONResponse(empty)

    try:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
        end_dt = datetime.fromisoformat(end).replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid datetime format") from exc
    if (end_dt - start_dt).days > 25 * 366:
        raise HTTPException(
            status_code=400, detail="Date range exceeds 25-year maximum"
        )

    # Get hindcast forecasts for this station/parameter/range
    hf_rows = (
        conn.execute(
            sa.select(hf.c.id, hf.c.hindcast_step)
            .where(
                sa.and_(
                    hf.c.station_id == station_id,
                    hf.c.parameter == parameter,
                    hf.c.hindcast_step >= start_dt,
                    hf.c.hindcast_step < end_dt,
                )
            )
            .order_by(hf.c.hindcast_step)
        )
        .mappings()
        .all()
    )

    if not hf_rows:
        return JSONResponse(empty)

    hf_ids = [r["id"] for r in hf_rows]

    # Get hindcast values for those forecasts
    val_rows = (
        conn.execute(
            sa.select(
                hv.c.hindcast_forecast_id,
                hv.c.valid_time,
                hv.c.lead_time_hours,
                hv.c.value,
            )
            .where(hv.c.hindcast_forecast_id.in_(hf_ids))
            .order_by(hv.c.hindcast_forecast_id, hv.c.lead_time_hours)
        )
        .mappings()
        .all()
    )

    # Group values by hindcast_forecast_id, then by lead_time_hours
    steps_data: dict[str, dict[int, list[float]]] = {}
    valid_times_by_step: dict[str, dict[int, str]] = {}
    for v in val_rows:
        fid = str(v["hindcast_forecast_id"])
        lt = v["lead_time_hours"]
        if fid not in steps_data:
            steps_data[fid] = {}
            valid_times_by_step[fid] = {}
        if lt not in steps_data[fid]:
            steps_data[fid][lt] = []
            valid_times_by_step[fid][lt] = v["valid_time"].isoformat()
        steps_data[fid][lt].append(v["value"])

    # Build response with quantiles
    hindcast_steps = []
    for hf_row in hf_rows:
        fid = str(hf_row["id"])
        if fid not in steps_data:
            continue
        lead_times = sorted(steps_data[fid].keys())
        median_vals = []
        p10_vals = []
        p90_vals = []
        vt_list = []
        for lt in lead_times:
            vals = sorted(steps_data[fid][lt])
            n = len(vals)
            if n == 1:
                median_vals.append(vals[0])
                p10_vals.append(vals[0])
                p90_vals.append(vals[0])
            else:
                median_vals.append(statistics.median(vals))
                p10_vals.append(vals[max(0, int(n * 0.1))])
                p90_vals.append(vals[min(n - 1, int(n * 0.9))])
            vt_list.append(valid_times_by_step[fid][lt])

        hindcast_steps.append(
            {
                "step": hf_row["hindcast_step"].isoformat(),
                "lead_times": lead_times,
                "valid_times": vt_list,
                "median": median_vals,
                "p10": p10_vals,
                "p90": p90_vals,
            }
        )

    # Get observations for the same period
    observed: dict[str, list[object]] = {"timestamps": [], "values": []}
    if obs is not None:
        obs_rows = (
            conn.execute(
                sa.select(obs.c.timestamp, obs.c.value)
                .where(
                    sa.and_(
                        obs.c.station_id == station_id,
                        obs.c.parameter == parameter,
                        obs.c.timestamp >= start_dt,
                        obs.c.timestamp < end_dt,
                        obs.c.qc_status != "qc_failed",
                    )
                )
                .order_by(obs.c.timestamp)
            )
            .mappings()
            .all()
        )
        observed = {
            "timestamps": [r["timestamp"].isoformat() for r in obs_rows],
            "values": [r["value"] for r in obs_rows],
        }

    return JSONResponse(
        {
            "observed": observed,
            "hindcast_steps": hindcast_steps,
        }
    )
