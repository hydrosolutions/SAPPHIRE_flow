from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

import sqlalchemy as sa
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, JsonValue

from sapphire_flow.api.skill_options import SkillEvaluationOption
from sapphire_flow.db.metadata import skill_diagrams as diagrams
from sapphire_flow.db.metadata import skill_generations as generations
from sapphire_flow.db.metadata import skill_scores as scores
from sapphire_flow.services.forecast_lab.db_sources import (
    fetch_active_model_assignments,
)
from sapphire_flow.services.skill_read import evaluated_on
from sapphire_flow.store.skill_period_codec import (
    SKILL_PERIOD_FIELDS,
    SkillPeriodPayload,
    parse_evaluation_context,
)
from sapphire_flow.store.skill_store import latest_generation_predicate
from sapphire_flow.store.threshold_skill_codec import (
    ThresholdContextPayload,  # noqa: TC001 -- Pydantic
)
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from sapphire_flow.services.forecast_lab.db_sources import ForecastLabStores
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId

MAX_DIAGRAM_BYTES = 4 * 1024 * 1024
MAX_DIAGRAM_ROWS = 1024
DiagramKind = Literal["reliability", "roc"]


class StoredSkillDiagram(SkillEvaluationOption):
    model_config = ConfigDict(allow_inf_nan=False)
    diagram_id: str
    diagram_type: DiagramKind
    threshold_level: str
    lead_time_hours: int
    threshold_context: ThresholdContextPayload | None
    data: dict[str, JsonValue]


class MissingSkillDiagram(BaseModel):
    lead_time_hours: int
    threshold_level: str
    diagram_type: DiagramKind
    reason: Literal["no_matching_stored_diagram"] = "no_matching_stored_diagram"


class StationSkillDiagramsResponse(BaseModel):
    station_id: str
    model_id: str
    model_artifact_id: str
    parameter: Literal["discharge"] = "discharge"
    availability: Literal["available", "partial", "no_stored_diagrams"]
    training_period_start: datetime | None
    training_period_end: datetime | None
    diagrams: list[StoredSkillDiagram]
    missing: list[MissingSkillDiagram]


def read_skill_diagrams(
    conn: sa.Connection,
    stores: ForecastLabStores,
    *,
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    lead_hours: tuple[int, ...],
    threshold_levels: tuple[str, ...],
    now: UtcDatetime,
) -> StationSkillDiagramsResponse:
    assigned = any(
        assignment.model_id == model_id
        for assignment in fetch_active_model_assignments(stores, station_id)
    )
    artifact = (
        stores.artifact_store.fetch_artifact_record(artifact_id) if assigned else None
    )
    if artifact is not None and artifact.model_id != model_id:
        artifact = None
    results: list[StoredSkillDiagram] = []
    if artifact is not None:
        ledger = generations.alias("diagram_generation")
        # A diagram's ledger has forcing; legacy diagrams do not. Require a
        # current scalar cohort too, so an old curve cannot outlive its scores.
        matching_score = (
            sa.select(sa.literal(1))
            .where(
                scores.c.station_id == diagrams.c.station_id,
                scores.c.model_id == diagrams.c.model_id,
                scores.c.model_artifact_id == diagrams.c.model_artifact_id,
                scores.c.parameter == diagrams.c.parameter,
                scores.c.skill_source == diagrams.c.skill_source,
                scores.c.generation_id.is_not_distinct_from(diagrams.c.generation_id),
                scores.c.computation_version == diagrams.c.computation_version,
                scores.c.time_step_seconds == diagrams.c.time_step_seconds,
                scores.c.phase_offset_seconds.is_not_distinct_from(
                    diagrams.c.phase_offset_seconds
                ),
                *(
                    scores.c[name].is_not_distinct_from(diagrams.c[name])
                    for name in SKILL_PERIOD_FIELDS
                ),
                scores.c.lead_time_hours == diagrams.c.lead_time_hours,
                scores.c.eval_period_start == diagrams.c.eval_period_start,
                scores.c.eval_period_end == diagrams.c.eval_period_end,
                scores.c.season.is_(None),
                scores.c.flow_regime.is_(None),
                sa.or_(
                    diagrams.c.generation_id.is_(None),
                    scores.c.forcing_type.is_not_distinct_from(ledger.c.forcing_type),
                ),
                latest_generation_predicate(scores),
            )
            .correlate(diagrams, ledger)
            .exists()
        )
        payload_bytes = sa.func.sum(
            sa.func.octet_length(sa.cast(diagrams.c.data, sa.Text))
            + sa.func.coalesce(
                sa.func.octet_length(sa.cast(diagrams.c.threshold_context, sa.Text)), 0
            )
        ).over()
        columns = [c for c in diagrams.c if c.name not in {"data", "threshold_context"}]
        rows = (
            conn.execute(
                sa.select(
                    *columns,
                    ledger.c.forcing_type,
                    payload_bytes.label("payload_bytes"),
                    sa.case(
                        (payload_bytes <= MAX_DIAGRAM_BYTES, diagrams.c.data)
                    ).label("data"),
                    sa.case(
                        (
                            payload_bytes <= MAX_DIAGRAM_BYTES,
                            diagrams.c.threshold_context,
                        )
                    ).label("threshold_context"),
                )
                .select_from(
                    diagrams.outerjoin(ledger, diagrams.c.generation_id == ledger.c.id)
                )
                .where(
                    diagrams.c.station_id == station_id,
                    diagrams.c.model_id == model_id,
                    diagrams.c.model_artifact_id == artifact_id,
                    diagrams.c.parameter == "discharge",
                    diagrams.c.lead_time_hours.in_(lead_hours),
                    diagrams.c.threshold_level.in_(threshold_levels),
                    diagrams.c.diagram_type.in_(("reliability", "roc")),
                    diagrams.c.season.is_(None),
                    diagrams.c.flow_regime.is_(None),
                    diagrams.c.eval_period_end <= now,
                    latest_generation_predicate(diagrams),
                    matching_score,
                )
                .order_by(
                    diagrams.c.lead_time_hours,
                    diagrams.c.threshold_level,
                    diagrams.c.diagram_type,
                    diagrams.c.id,
                )
                .limit(MAX_DIAGRAM_ROWS + 1)
            )
            .mappings()
            .all()
        )
        if len(rows) > MAX_DIAGRAM_ROWS or any(
            row["payload_bytes"] > MAX_DIAGRAM_BYTES for row in rows
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Stored diagrams exceed response limit; "
                    "select fewer leads or thresholds."
                ),
            )
        for row in rows:
            results.append(
                StoredSkillDiagram(
                    diagram_id=str(row["id"]),
                    diagram_type=row["diagram_type"],
                    threshold_level=row["threshold_level"],
                    lead_time_hours=row["lead_time_hours"],
                    generation_id=str(row["generation_id"])
                    if row["generation_id"] is not None
                    else None,
                    skill_source=row["skill_source"],
                    forcing_type=row["forcing_type"],
                    computation_version=row["computation_version"],
                    time_step_seconds=row["time_step_seconds"],
                    phase_offset_seconds=row["phase_offset_seconds"],
                    evaluation_context=SkillPeriodPayload.from_domain(
                        parse_evaluation_context(
                            {name: row[name] for name in SKILL_PERIOD_FIELDS}
                        )
                    ),
                    eval_period_start=row["eval_period_start"],
                    eval_period_end=row["eval_period_end"],
                    evaluated_on=evaluated_on(
                        eval_start=ensure_utc(row["eval_period_start"]),
                        eval_end=ensure_utc(row["eval_period_end"]),
                        training_start=artifact.training_period_start,
                        training_end=artifact.training_period_end,
                    ),
                    threshold_context=row["threshold_context"],
                    data=row["data"],
                )
            )
    present = {(d.lead_time_hours, d.threshold_level, d.diagram_type) for d in results}
    missing = [
        MissingSkillDiagram(
            lead_time_hours=lead, threshold_level=level, diagram_type=kind
        )
        for lead in lead_hours
        for level in threshold_levels
        for kind in ("reliability", "roc")
        if (lead, level, kind) not in present
    ]
    return StationSkillDiagramsResponse(
        station_id=str(station_id),
        model_id=model_id,
        model_artifact_id=str(artifact_id),
        availability="no_stored_diagrams"
        if not results
        else "partial"
        if missing
        else "available",
        training_period_start=artifact.training_period_start
        if artifact is not None and results
        else None,
        training_period_end=artifact.training_period_end
        if artifact is not None and results
        else None,
        diagrams=results,
        missing=missing,
    )


def encode_diagram_response(result: StationSkillDiagramsResponse) -> bytes:
    content = result.model_dump_json().encode("utf-8")
    if len(content) > MAX_DIAGRAM_BYTES:
        raise HTTPException(
            status_code=422,
            detail="Stored diagrams exceed 4 MiB; select fewer leads or thresholds.",
        )
    return content
