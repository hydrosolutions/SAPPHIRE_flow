from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

import sqlalchemy as sa
from fastapi import HTTPException
from pydantic import BaseModel

from sapphire_flow.db.metadata import model_artifacts as artifacts
from sapphire_flow.db.metadata import skill_scores as scores
from sapphire_flow.services.forecast_lab.db_sources import (
    fetch_active_model_assignments,
)
from sapphire_flow.services.skill_read import EvaluatedOn, evaluated_on
from sapphire_flow.store.skill_period_codec import (
    SKILL_PERIOD_FIELDS,
    SkillPeriodPayload,
    parse_evaluation_context,
)
from sapphire_flow.store.skill_store import latest_generation_predicate
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (  # noqa: TC001 -- Pydantic
    ForcingType,
    SkillSource,
)
from sapphire_flow.types.ids import ModelId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sapphire_flow.services.forecast_lab.db_sources import ForecastLabStores
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId

MAX_SKILL_EVALUATIONS = 256


class SkillEvaluationOption(BaseModel):
    generation_id: str | None
    skill_source: SkillSource
    forcing_type: ForcingType | None
    computation_version: int
    time_step_seconds: int
    phase_offset_seconds: int | None
    evaluation_context: SkillPeriodPayload | None = None
    eval_period_start: datetime
    eval_period_end: datetime
    evaluated_on: EvaluatedOn


class SkillArtifactOption(BaseModel):
    model_id: str
    model_artifact_id: str
    forecasting_status: Literal["active_at_station", "not_active_at_station"]
    training_period_start: datetime | None
    training_period_end: datetime | None
    evaluations: list[SkillEvaluationOption]


class StationSkillOptionsResponse(BaseModel):
    station_id: str
    parameter: Literal["discharge"] = "discharge"
    availability: Literal["available", "no_stored_skill"]
    artifacts: list[SkillArtifactOption]


def read_skill_options(
    conn: sa.Connection,
    stores: ForecastLabStores,
    *,
    station_id: StationId,
    now: UtcDatetime,
    model_id: ModelId | None,
) -> StationSkillOptionsResponse:
    model_ids = {
        assignment.model_id
        for assignment in fetch_active_model_assignments(stores, station_id)
        if model_id is None or assignment.model_id == model_id
    }
    columns = [
        scores.c.model_id,
        scores.c.model_artifact_id,
        scores.c.generation_id,
        scores.c.skill_source,
        scores.c.forcing_type,
        scores.c.computation_version,
        scores.c.time_step_seconds,
        scores.c.phase_offset_seconds,
        *(scores.c[name] for name in SKILL_PERIOD_FIELDS),
        scores.c.eval_period_start,
        scores.c.eval_period_end,
        artifacts.c.training_period_start,
        artifacts.c.training_period_end,
    ]
    # Same filters as PgSkillStore.fetch_latest_scores plus the scalar reader's
    # artifact/model and end-time checks. Project metadata, not score payloads.
    rows: Sequence[sa.RowMapping] = (
        conn.execute(
            sa.select(*columns)
            .select_from(
                scores.join(
                    artifacts,
                    sa.and_(
                        scores.c.model_artifact_id == artifacts.c.id,
                        scores.c.model_id == artifacts.c.model_id,
                    ),
                )
            )
            .where(
                scores.c.station_id == station_id,
                scores.c.model_id.in_(model_ids),
                scores.c.parameter == "discharge",
                scores.c.eval_period_end <= now,
                latest_generation_predicate(scores),
            )
            .distinct()
            .order_by(*columns)
            .limit(MAX_SKILL_EVALUATIONS + 1)
        )
        .mappings()
        .all()
        if model_ids
        else []
    )
    if len(rows) > MAX_SKILL_EVALUATIONS:
        raise HTTPException(
            status_code=503,
            detail="Stored skill catalogue exceeds 256 evaluations; select a model_id.",
        )
    active_ids = {
        mid: stores.artifact_store.fetch_active_artifact_id_for_station(station_id, mid)
        for mid in {ModelId(row["model_id"]) for row in rows}
    }
    options: dict[str, SkillArtifactOption] = {}
    for row in rows:
        aid = str(row["model_artifact_id"])
        if aid not in options:
            options[aid] = SkillArtifactOption(
                model_id=row["model_id"],
                model_artifact_id=aid,
                forecasting_status="active_at_station"
                if row["model_artifact_id"] == active_ids[ModelId(row["model_id"])]
                else "not_active_at_station",
                training_period_start=row["training_period_start"],
                training_period_end=row["training_period_end"],
                evaluations=[],
            )
        options[aid].evaluations.append(
            SkillEvaluationOption(
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
                    training_start=ensure_utc(row["training_period_start"])
                    if row["training_period_start"] is not None
                    else None,
                    training_end=ensure_utc(row["training_period_end"])
                    if row["training_period_end"] is not None
                    else None,
                ),
            )
        )
    return StationSkillOptionsResponse(
        station_id=str(station_id),
        availability="available" if options else "no_stored_skill",
        artifacts=list(options.values()),
    )
