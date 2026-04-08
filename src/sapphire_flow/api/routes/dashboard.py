from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.routes.tables import _get_reflected

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request, conn: sa.Connection = Depends(get_connection)
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = _get_reflected(conn)

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

    # Observation count
    obs_count = _count("observations")

    # Forecast count
    forecast_count = _count("forecasts")

    # Hindcast count
    hindcast_count = _count("hindcast_forecasts")

    # Model count
    model_count = _count("models")

    # Active artifact count
    artifacts = reflected.tables.get("model_artifacts")
    active_artifacts = 0
    if artifacts is not None:
        active_artifacts = _count("model_artifacts", artifacts.c.status == "active")

    # Alert count
    alerts = reflected.tables.get("alerts")
    active_alerts = 0
    if alerts is not None:
        active_alerts = _count("alerts", alerts.c.status == "raised")

    # Skill score count
    skill_count = _count("skill_scores")

    # Forcing count
    forcing_count = _count("historical_forcing")

    # Baseline count
    baseline_count = _count("clim_baselines")

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "station_count": station_count,
            "station_kinds": station_kinds,
            "obs_count": obs_count,
            "forecast_count": forecast_count,
            "hindcast_count": hindcast_count,
            "model_count": model_count,
            "active_artifacts": active_artifacts,
            "active_alerts": active_alerts,
            "skill_count": skill_count,
            "forcing_count": forcing_count,
            "baseline_count": baseline_count,
            "active_nav": "dashboard",
        },
    )
