# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import skill_diagrams, skill_scores
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.enums import (
    FlowRegime,
    ForcingType,
    SkillFreshness,
    SkillSource,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.skill import SkillDiagram, SkillScore

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

ss = skill_scores
sd = skill_diagrams


class PgSkillStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_skill_scores(self, scores: list[SkillScore]) -> None:
        if not scores:
            return
        rows = [_score_to_row(s) for s in scores]
        stmt = pg_insert(ss).on_conflict_do_nothing()
        self._conn.execute(stmt, rows)

    def store_skill_diagrams(self, diagrams: list[SkillDiagram]) -> None:
        if not diagrams:
            return
        rows = [_diagram_to_row(d) for d in diagrams]
        stmt = pg_insert(sd).on_conflict_do_nothing()
        self._conn.execute(stmt, rows)

    def fetch_latest_scores(
        self,
        station_id: StationId,
        model_id: ModelId,
        skill_source: SkillSource | None = None,
    ) -> list[SkillScore]:
        filters = [ss.c.station_id == station_id, ss.c.model_id == model_id]
        if skill_source is not None:
            filters.append(ss.c.skill_source == skill_source.value)

        max_ver = (
            sa.select(sa.func.max(ss.c.computation_version))
            .where(*filters)
            .scalar_subquery()
        )
        stmt = sa.select(ss).where(*filters, ss.c.computation_version == max_ver)
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_score(row) for row in rows]

    def fetch_latest_diagrams(
        self,
        station_id: StationId,
        model_id: ModelId,
        diagram_type: Literal["reliability", "roc", "rank_histogram"] | None = None,
    ) -> list[SkillDiagram]:
        filters = [sd.c.station_id == station_id, sd.c.model_id == model_id]
        if diagram_type is not None:
            filters.append(sd.c.diagram_type == diagram_type)

        max_ver = (
            sa.select(sa.func.max(sd.c.computation_version))
            .where(*filters)
            .scalar_subquery()
        )
        stmt = sa.select(sd).where(*filters, sd.c.computation_version == max_ver)
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_diagram(row) for row in rows]

    def fetch_scores_by_regime(
        self,
        station_id: StationId,
        model_id: ModelId,
        flow_regime: FlowRegime,
    ) -> list[SkillScore]:
        stmt = sa.select(ss).where(
            ss.c.station_id == station_id,
            ss.c.model_id == model_id,
            ss.c.flow_regime == flow_regime.value,
        )
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_score(row) for row in rows]

    def mark_stale(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> int:
        result = self._conn.execute(
            sa.update(ss)
            .where(
                ss.c.station_id == station_id,
                ss.c.freshness == SkillFreshness.CURRENT.value,
                ss.c.eval_period_start < end,
                ss.c.eval_period_end > start,
            )
            .values(freshness=SkillFreshness.STALE.value)
        )
        return result.rowcount


def _score_to_row(s: SkillScore) -> dict:  # type: ignore[type-arg]
    return {
        "id": s.id,
        "station_id": s.station_id,
        "model_id": s.model_id,
        "model_artifact_id": s.model_artifact_id,
        "skill_source": s.skill_source.value,
        "forcing_type": s.forcing_type.value if s.forcing_type is not None else None,
        "computation_version": s.computation_version,
        "computed_at": s.computed_at,
        "lead_time_hours": s.lead_time_hours,
        "season": s.season,
        "flow_regime": s.flow_regime.value if s.flow_regime is not None else None,
        "flow_regime_config_id": s.flow_regime_config_id,
        "metric": s.metric,
        "score": s.score,
        "sample_size": s.sample_size,
        "freshness": s.freshness.value,
        "eval_period_start": s.eval_period_start,
        "eval_period_end": s.eval_period_end,
        "created_at": s.created_at,
    }


def _diagram_to_row(d: SkillDiagram) -> dict:  # type: ignore[type-arg]
    return {
        "id": d.id,
        "station_id": d.station_id,
        "model_id": d.model_id,
        "model_artifact_id": d.model_artifact_id,
        "skill_source": d.skill_source.value,
        "computation_version": d.computation_version,
        "lead_time_hours": d.lead_time_hours,
        "season": d.season,
        "flow_regime": d.flow_regime.value if d.flow_regime is not None else None,
        "flow_regime_config_id": d.flow_regime_config_id,
        "diagram_type": d.diagram_type,
        "threshold_level": d.threshold_level,
        "data": d.data,
        "eval_period_start": d.eval_period_start,
        "eval_period_end": d.eval_period_end,
        "created_at": d.created_at,
    }


def _row_to_score(row: sa.engine.row.RowMapping) -> SkillScore:
    forcing_raw = row["forcing_type"]
    flow_regime_raw = row["flow_regime"]
    return SkillScore(
        id=row["id"],
        station_id=StationId(row["station_id"]),
        model_id=ModelId(row["model_id"]),
        model_artifact_id=ArtifactId(row["model_artifact_id"]),
        skill_source=SkillSource(row["skill_source"]),
        forcing_type=ForcingType(forcing_raw) if forcing_raw is not None else None,
        computation_version=row["computation_version"],
        computed_at=utc_from_row(row["computed_at"]),
        lead_time_hours=row["lead_time_hours"],
        season=row["season"],
        flow_regime=FlowRegime(flow_regime_raw)
        if flow_regime_raw is not None
        else None,
        flow_regime_config_id=row["flow_regime_config_id"],
        metric=row["metric"],
        score=row["score"],
        sample_size=row["sample_size"],
        freshness=SkillFreshness(row["freshness"]),
        eval_period_start=utc_from_row(row["eval_period_start"]),
        eval_period_end=utc_from_row(row["eval_period_end"]),
        created_at=utc_from_row(row["created_at"]),
    )


def _row_to_diagram(row: sa.engine.row.RowMapping) -> SkillDiagram:
    flow_regime_raw = row["flow_regime"]
    return SkillDiagram(
        id=row["id"],
        station_id=StationId(row["station_id"]),
        model_id=ModelId(row["model_id"]),
        model_artifact_id=ArtifactId(row["model_artifact_id"]),
        skill_source=SkillSource(row["skill_source"]),
        computation_version=row["computation_version"],
        lead_time_hours=row["lead_time_hours"],
        season=row["season"],
        flow_regime=FlowRegime(flow_regime_raw)
        if flow_regime_raw is not None
        else None,
        flow_regime_config_id=row["flow_regime_config_id"],
        diagram_type=row["diagram_type"],
        threshold_level=row["threshold_level"],
        data=dict(row["data"]),
        eval_period_start=utc_from_row(row["eval_period_start"]),
        eval_period_end=utc_from_row(row["eval_period_end"]),
        created_at=utc_from_row(row["created_at"]),
    )
