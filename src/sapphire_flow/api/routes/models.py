from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.routes.tables import _get_reflected

router = APIRouter(tags=["models"])


@router.get("/models/", response_class=HTMLResponse)
def model_list(
    request: Request, conn: sa.Connection = Depends(get_connection)
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = _get_reflected(conn)
    models_table = reflected.tables.get("models")

    models: list[dict[str, Any]] = []
    if models_table is not None:
        rows = (
            conn.execute(sa.select(models_table).order_by(models_table.c.display_name))
            .mappings()
            .all()
        )
        models = [dict(r) for r in rows]

    # Count artifacts per model
    artifacts_table = reflected.tables.get("model_artifacts")
    artifact_counts: dict[str, int] = {}
    if artifacts_table is not None:
        rows = (
            conn.execute(
                sa.select(
                    artifacts_table.c.model_id,
                    sa.func.count().label("cnt"),
                ).group_by(artifacts_table.c.model_id)
            )
            .mappings()
            .all()
        )
        artifact_counts = {str(r["model_id"]): r["cnt"] for r in rows}

    for m in models:
        m["artifact_count"] = artifact_counts.get(str(m["id"]), 0)

    return templates.TemplateResponse(
        request,
        "models/list.html",
        {"models": models, "active_nav": "models"},
    )


@router.get("/models/{model_id}/", response_class=HTMLResponse)
def model_detail(
    request: Request,
    model_id: str,
    conn: sa.Connection = Depends(get_connection),
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = _get_reflected(conn)
    models_table = reflected.tables.get("models")
    if models_table is None:
        raise HTTPException(status_code=404, detail="Models table not found")

    row = (
        conn.execute(sa.select(models_table).where(models_table.c.id == model_id))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Model not found")

    model = dict(row)

    # Get artifacts
    artifacts_table = reflected.tables.get("model_artifacts")
    artifacts: list[dict[str, Any]] = []
    if artifacts_table is not None:
        raw = (
            conn.execute(
                sa.select(artifacts_table)
                .where(artifacts_table.c.model_id == model_id)
                .order_by(artifacts_table.c.created_at.desc())
            )
            .mappings()
            .all()
        )
        artifacts = [dict(r) for r in raw]

    # Get assignments
    assignments_table = reflected.tables.get("model_assignments")
    assignments: list[dict[str, Any]] = []
    if assignments_table is not None:
        raw = (
            conn.execute(
                sa.select(assignments_table)
                .where(assignments_table.c.model_id == model_id)
                .order_by(assignments_table.c.priority)
            )
            .mappings()
            .all()
        )
        assignments = [dict(r) for r in raw]

    # Get skill scores for active artifact
    active_artifact_id = None
    for a in artifacts:
        if a.get("status") == "active":
            active_artifact_id = a["id"]
            break

    skill_scores: list[dict[str, Any]] = []
    skill_table = reflected.tables.get("skill_scores")
    if skill_table is not None and active_artifact_id:
        raw = (
            conn.execute(
                sa.select(skill_table)
                .where(skill_table.c.model_artifact_id == active_artifact_id)
                .order_by(skill_table.c.lead_time_hours, skill_table.c.metric)
            )
            .mappings()
            .all()
        )
        skill_scores = [dict(r) for r in raw]

    # Get skill diagrams
    skill_diagrams: list[dict[str, Any]] = []
    diagram_table = reflected.tables.get("skill_diagrams")
    if diagram_table is not None and active_artifact_id:
        raw = (
            conn.execute(
                sa.select(diagram_table).where(
                    diagram_table.c.model_artifact_id == active_artifact_id
                )
            )
            .mappings()
            .all()
        )
        skill_diagrams = [dict(r) for r in raw]

    return templates.TemplateResponse(
        request,
        "models/detail.html",
        {
            "model": model,
            "artifacts": artifacts,
            "assignments": assignments,
            "skill_scores": skill_scores,
            "skill_diagrams": skill_diagrams,
            "active_nav": "models",
        },
    )
