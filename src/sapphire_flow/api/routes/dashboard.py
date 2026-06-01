from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.routes.tables import get_reflected

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request, conn: sa.Connection = Depends(get_connection)
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = get_reflected(conn)

    def _count(table_name: str, where: sa.ColumnElement[bool] | None = None) -> int:
        table = reflected.tables.get(table_name)
        if table is None:
            return 0
        q = sa.select(sa.func.count()).select_from(table)
        if where is not None:
            q = q.where(where)
        return conn.execute(q).scalar_one()

    # Station counts
    stations = reflected.tables.get("stations")
    station_count = _count("stations")
    station_kinds: list[dict[str, object]] = []
    if stations is not None:
        rows = (
            conn.execute(
                sa.select(
                    stations.c.station_kind,
                    sa.func.count().label("cnt"),
                ).group_by(stations.c.station_kind)
            )
            .mappings()
            .all()
        )
        station_kinds = [{"kind": r["station_kind"], "count": r["cnt"]} for r in rows]

    # Observation count + date range + parameter breakdown
    obs_count = _count("observations")
    obs_date_range: dict[str, str | None] = {"min": None, "max": None}
    obs_params: list[dict[str, object]] = []
    obs_table = reflected.tables.get("observations")
    if obs_table is not None and obs_count > 0:
        dr = (
            conn.execute(
                sa.select(
                    sa.func.min(obs_table.c.timestamp).label("min_ts"),
                    sa.func.max(obs_table.c.timestamp).label("max_ts"),
                )
            )
            .mappings()
            .one()
        )
        obs_date_range = {
            "min": dr["min_ts"].strftime("%Y-%m-%d") if dr["min_ts"] else None,
            "max": dr["max_ts"].strftime("%Y-%m-%d") if dr["max_ts"] else None,
        }
        rows = (
            conn.execute(
                sa.select(obs_table.c.parameter, sa.func.count().label("cnt"))
                .group_by(obs_table.c.parameter)
                .order_by(sa.func.count().desc())
            )
            .mappings()
            .all()
        )
        obs_params = [{"parameter": r["parameter"], "count": r["cnt"]} for r in rows]

    # Forcing count + date range + parameter breakdown
    forcing_count = _count("historical_forcing")
    forcing_date_range: dict[str, str | None] = {"min": None, "max": None}
    forcing_params: list[dict[str, object]] = []
    forcing_table = reflected.tables.get("historical_forcing")
    if forcing_table is not None and forcing_count > 0:
        dr = (
            conn.execute(
                sa.select(
                    sa.func.min(forcing_table.c.valid_time).label("min_ts"),
                    sa.func.max(forcing_table.c.valid_time).label("max_ts"),
                )
            )
            .mappings()
            .one()
        )
        forcing_date_range = {
            "min": dr["min_ts"].strftime("%Y-%m-%d") if dr["min_ts"] else None,
            "max": dr["max_ts"].strftime("%Y-%m-%d") if dr["max_ts"] else None,
        }
        rows = (
            conn.execute(
                sa.select(forcing_table.c.parameter, sa.func.count().label("cnt"))
                .group_by(forcing_table.c.parameter)
                .order_by(sa.func.count().desc())
            )
            .mappings()
            .all()
        )
        forcing_params = [
            {"parameter": r["parameter"], "count": r["cnt"]} for r in rows
        ]

    # Baseline count + station count
    baseline_count = _count("clim_baselines")
    baseline_station_count = 0
    baseline_table = reflected.tables.get("clim_baselines")
    if baseline_table is not None and baseline_count > 0:
        baseline_station_count = conn.execute(
            sa.select(sa.func.count(sa.distinct(baseline_table.c.station_id)))
        ).scalar_one()

    # Forecast count + latest issued_at + status breakdown
    forecast_count = _count("forecasts")
    forecast_latest: str | None = None
    forecast_statuses: list[dict[str, object]] = []
    forecast_table = reflected.tables.get("forecasts")
    if forecast_table is not None and forecast_count > 0:
        latest = conn.execute(
            sa.select(sa.func.max(forecast_table.c.issued_at))
        ).scalar_one()
        forecast_latest = latest.strftime("%Y-%m-%d %H:%M") if latest else None
        rows = (
            conn.execute(
                sa.select(
                    forecast_table.c.status, sa.func.count().label("cnt")
                ).group_by(forecast_table.c.status)
            )
            .mappings()
            .all()
        )
        forecast_statuses = [{"status": r["status"], "count": r["cnt"]} for r in rows]

    # Hindcast count + latest step + station count
    hindcast_count = _count("hindcast_forecasts")
    hindcast_latest: str | None = None
    hindcast_station_count = 0
    hindcast_table = reflected.tables.get("hindcast_forecasts")
    if hindcast_table is not None and hindcast_count > 0:
        dr = (
            conn.execute(
                sa.select(
                    sa.func.max(hindcast_table.c.hindcast_step).label("max_step"),
                    sa.func.count(sa.distinct(hindcast_table.c.station_id)).label(
                        "stn_cnt"
                    ),
                )
            )
            .mappings()
            .one()
        )
        step = dr["max_step"]
        hindcast_latest = step.strftime("%Y-%m-%d") if step else None
        hindcast_station_count = dr["stn_cnt"]

    # Model count + scope breakdown
    model_count = _count("models")
    model_scopes: list[dict[str, object]] = []
    models_table = reflected.tables.get("models")
    if models_table is not None and model_count > 0:
        rows = (
            conn.execute(
                sa.select(
                    models_table.c.artifact_scope, sa.func.count().label("cnt")
                ).group_by(models_table.c.artifact_scope)
            )
            .mappings()
            .all()
        )
        model_scopes = [{"scope": r["artifact_scope"], "count": r["cnt"]} for r in rows]

    # Active artifact count
    artifacts = reflected.tables.get("model_artifacts")
    active_artifacts = 0
    if artifacts is not None:
        active_artifacts = _count("model_artifacts", artifacts.c.status == "active")

    # Alert count + level breakdown
    alerts_table = reflected.tables.get("alerts")
    active_alerts = 0
    alert_levels: list[dict[str, object]] = []
    if alerts_table is not None:
        active_alerts = _count("alerts", alerts_table.c.status == "raised")
        if active_alerts > 0:
            rows = (
                conn.execute(
                    sa.select(alerts_table.c.alert_level, sa.func.count().label("cnt"))
                    .where(alerts_table.c.status == "raised")
                    .group_by(alerts_table.c.alert_level)
                )
                .mappings()
                .all()
            )
            alert_levels = [
                {"level": r["alert_level"], "count": r["cnt"]} for r in rows
            ]

    # Skill score count + metric breakdown + freshness split
    skill_count = _count("skill_scores")
    skill_metrics: list[dict[str, object]] = []
    skill_freshness: list[dict[str, object]] = []
    skill_table = reflected.tables.get("skill_scores")
    if skill_table is not None and skill_count > 0:
        rows = (
            conn.execute(
                sa.select(skill_table.c.metric, sa.func.count().label("cnt"))
                .group_by(skill_table.c.metric)
                .order_by(sa.func.count().desc())
            )
            .mappings()
            .all()
        )
        skill_metrics = [{"metric": r["metric"], "count": r["cnt"]} for r in rows]
        rows = (
            conn.execute(
                sa.select(
                    skill_table.c.freshness, sa.func.count().label("cnt")
                ).group_by(skill_table.c.freshness)
            )
            .mappings()
            .all()
        )
        skill_freshness = [
            {"freshness": r["freshness"], "count": r["cnt"]} for r in rows
        ]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "station_count": station_count,
            "station_kinds": station_kinds,
            "obs_count": obs_count,
            "obs_date_range": obs_date_range,
            "obs_params": obs_params,
            "forcing_count": forcing_count,
            "forcing_date_range": forcing_date_range,
            "forcing_params": forcing_params,
            "baseline_count": baseline_count,
            "baseline_station_count": baseline_station_count,
            "forecast_count": forecast_count,
            "forecast_latest": forecast_latest,
            "forecast_statuses": forecast_statuses,
            "hindcast_count": hindcast_count,
            "hindcast_latest": hindcast_latest,
            "hindcast_station_count": hindcast_station_count,
            "model_count": model_count,
            "model_scopes": model_scopes,
            "active_artifacts": active_artifacts,
            "active_alerts": active_alerts,
            "alert_levels": alert_levels,
            "skill_count": skill_count,
            "skill_metrics": skill_metrics,
            "skill_freshness": skill_freshness,
            "active_nav": "dashboard",
        },
    )
