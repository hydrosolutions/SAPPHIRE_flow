# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import skill_diagrams, skill_generations, skill_scores
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
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

ss = skill_scores
sd = skill_diagrams
sg = skill_generations


def latest_generation_predicate(
    data_table: sa.Table, generations_table: sa.Table | None = None
) -> sa.ColumnElement[bool]:
    """Plan 235 D2/D2b — the ONE rule every reader of `skill_scores`/
    `skill_diagrams` must apply to see only the newest generation, unifying
    the three incompatible "what is current" rules the plan found
    (`max(computation_version)`, no filter at all, `freshness == "current"`).

    Composable as an extra `.where(...)` clause on top of an EXISTING
    query — this is what lets it slot into the API's raw reflected-table
    reads (`api/routes/models.py`, `api/routes/stations.py`) with the exact
    same logic the store uses, rather than a second, divergent
    reimplementation. `data_table` may be the real metadata `Table` or a
    reflected one — only matching column names matter; pass the matching
    (also possibly reflected) `skill_generations` table as
    `generations_table` in that case so both sides of the join come from
    the same `MetaData` (defaults to this module's own `skill_generations`).

    A row is current iff:

    * it carries a `generation_id`, no OTHER generation for the same
      (station, model, artifact, parameter, skill_source, forcing_type)
      scope outranks it (D2b#1: `computation_version` first, then
      `published_at`, then `id` as a final deterministic tiebreak on an
      exact tie), AND no baseline row for that same scope sits at a
      STRICTLY HIGHER `computation_version` (D2b#1 applies to baselines
      too — version is read first, generation-vs-generation ranking only
      decides ties WITHIN the highest eligible version); or
    * it carries NO `generation_id` (a pre-Plan-235 baseline row), no OTHER
      baseline row for that scope outranks it by `computation_version`, and
      no generation at that scope sits at a computation_version >= this
      row's own — a generation only ever displaces a baseline at the SAME
      or a HIGHER version, never a lower one (fixer round, blocker: the
      previous rule hid EVERY baseline the instant ANY generation existed
      for the scope, regardless of version, letting a v1 generation stay
      "current" over a required v2 baseline).
    """
    generations = (
        generations_table if generations_table is not None else skill_generations
    )
    g_self = generations.alias("g_self")
    g_other = generations.alias("g_other")

    def _forcing_type_expr(t: sa.FromClause) -> sa.ColumnElement[str]:
        if "forcing_type" in t.c:
            return sa.func.coalesce(t.c.forcing_type, "")
        return sa.literal("")

    def _artifact_id_expr(t: sa.FromClause) -> sa.ColumnElement[str]:
        if "model_artifact_id" in t.c:
            return sa.func.coalesce(sa.cast(t.c.model_artifact_id, sa.Text), "")
        return sa.literal("")

    # Fixer round (blocker): `skill_diagrams` carries no `forcing_type`
    # column at all — diagrams are never forcing-scoped
    # (`services.skill.service._compute_diagrams` takes no `forcing_type`
    # argument). An earlier version compared `_forcing_type_expr` on BOTH
    # sides, including `generations` (which always has a real value like
    # `"reanalysis"`) against a diagram's constant `""` fallback — a real,
    # non-null forcing type on the published generation can never equal
    # that constant, so `_scope_match` never matched and a
    # `generation_id IS NULL` baseline diagram stayed "undisplaced" forever.
    #
    # Per-run-scope fixer round (major): the fix above OVER-corrected by
    # deciding "compare forcing type at all" from `data_table` ALONE (a
    # single flag computed once, outside `_scope_match`) — so when
    # `data_table` is `skill_diagrams`, EVERY `_scope_match` call skipped
    # forcing type, including `_scope_match(g_other, g_self)` in
    # `outranks`/`superseded` below, where BOTH sides are `generations`
    # rows that always carry a real forcing type. Two diagram-only
    # generations differing ONLY by forcing type then ranked as the SAME
    # scope and competed, so one silently superseded the other. Forcing
    # type must be decided PER CALL from whether BOTH operands passed to
    # `_scope_match` actually have the column — never from the outer
    # `data_table` — so a diagram row (no forcing type of its own) still
    # ignores it when compared against another data/baseline row, while two
    # `generations` rows (which always have one) still compare it, even
    # when the diagram they are ranked on behalf of cannot.
    def _scope_match(a: sa.FromClause, b: sa.FromClause) -> sa.ColumnElement[bool]:
        conditions = [
            a.c.station_id == b.c.station_id,
            a.c.model_id == b.c.model_id,
            # Fixer round (blocker): `model_artifact_id` must be part of
            # scope identity — otherwise a generation minted for one
            # artifact (e.g. a candidate under retraining evaluation)
            # outranks and hides the STILL-ACTIVE artifact's generation for
            # the same model, since `model_id` alone does not distinguish
            # artifacts. NULL-safe (POOLED/BMA combinations carry NULL on
            # both scores/diagrams and their generation) via the same
            # `COALESCE(...::text, '')` pattern the natural-key indexes use.
            _artifact_id_expr(a) == _artifact_id_expr(b),
            a.c.parameter == b.c.parameter,
            a.c.skill_source == b.c.skill_source,
        ]
        if "forcing_type" in a.c and "forcing_type" in b.c:
            conditions.append(_forcing_type_expr(a) == _forcing_type_expr(b))
        return sa.and_(*conditions)

    outranks = sa.or_(
        g_other.c.computation_version > g_self.c.computation_version,
        sa.and_(
            g_other.c.computation_version == g_self.c.computation_version,
            sa.or_(
                g_other.c.published_at > g_self.c.published_at,
                sa.and_(
                    g_other.c.published_at == g_self.c.published_at,
                    g_other.c.id > g_self.c.id,
                ),
            ),
        ),
    )
    superseded = (
        sa.select(sa.literal(1))
        .select_from(g_other)
        .where(_scope_match(g_other, g_self), outranks)
        .exists()
    )

    # T1: baseline rows (no generation, ever) still need SOME precedence
    # rule among themselves — pre-Plan-235 data can legitimately contain
    # baseline rows at more than one `computation_version` (T1: "including
    # any v2 rows written between Plan 228 and this plan"). This restores
    # exactly `fetch_latest_scores`'s old `max(computation_version)`
    # behaviour for that set, so a scope untouched by any generation-aware
    # recompute reads exactly as it did before this plan.
    other_baseline = data_table.alias("other_baseline")
    outranked_among_baselines = (
        sa.select(sa.literal(1))
        .select_from(other_baseline)
        .where(
            _scope_match(other_baseline, data_table),
            other_baseline.c.generation_id.is_(None),
            other_baseline.c.computation_version > data_table.c.computation_version,
        )
        .exists()
    )

    # Fixer round (blocker, D2b#1 "version first"): a generation is only
    # displaced by a baseline at a STRICTLY HIGHER computation_version —
    # never at an equal or lower one, which stays governed by `outranks`/
    # `superseded` among generations. Correlates against `g_self` (the
    # generation under test), not `data_table` directly, so this applies
    # uniformly regardless of which row of that generation is being
    # evaluated.
    baseline_outranks_generation = (
        sa.select(sa.literal(1))
        .select_from(other_baseline)
        .where(
            _scope_match(other_baseline, data_table),
            other_baseline.c.generation_id.is_(None),
            other_baseline.c.computation_version > g_self.c.computation_version,
        )
        .exists()
    )
    is_current_generation = sa.and_(
        data_table.c.generation_id.isnot(None),
        sa.select(sa.literal(1))
        .select_from(g_self)
        .where(
            g_self.c.id == data_table.c.generation_id,
            sa.not_(superseded),
            sa.not_(baseline_outranks_generation),
        )
        .exists(),
    )

    # Fixer round (blocker): a generation only ever displaces a baseline at
    # the SAME or a HIGHER computation_version — never a lower one. The
    # previous rule ("no generation has EVER been published for this
    # scope, regardless of version") let a v1 generation stay current over
    # a required v2 baseline the instant that v1 generation existed,
    # inverting D2b#1's "version first" precedence.
    no_generation_at_or_above_version_for_scope = (
        sa.select(sa.literal(1))
        .select_from(generations)
        .where(
            _scope_match(generations, data_table),
            generations.c.computation_version >= data_table.c.computation_version,
        )
        .exists()
    )
    is_undisplaced_baseline = sa.and_(
        data_table.c.generation_id.is_(None),
        sa.not_(no_generation_at_or_above_version_for_scope),
        sa.not_(outranked_among_baselines),
    )

    return sa.or_(is_current_generation, is_undisplaced_baseline)


class PgSkillStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_skill_scores(self, scores: list[SkillScore]) -> int:
        """Returns the number of rows ACTUALLY inserted (fixer round,
        major) — `ON CONFLICT DO NOTHING` can silently drop rows whose
        natural key collides (e.g. two BMA cross-validation folds computing
        the same diagnostic under one generation). Counts via `RETURNING`
        rather than `cursor.rowcount`: measured live against this
        project's psycopg3 driver, `rowcount` on an `INSERT ... ON
        CONFLICT DO NOTHING` (and even a plain `INSERT`) comes back `-1`
        ("not supported/determined" per PEP 249) — `RETURNING` reports the
        actual affected rows regardless. Callers use this to reconcile
        actual persisted counts against what they expected to write
        BEFORE publishing — see `flows.compute_skills`.
        """
        if not scores:
            return 0
        rows = [_score_to_row(s) for s in scores]
        stmt = pg_insert(ss).values(rows).on_conflict_do_nothing().returning(ss.c.id)
        result = self._conn.execute(stmt)
        return len(result.fetchall())

    def store_skill_diagrams(self, diagrams: list[SkillDiagram]) -> int:
        """See `store_skill_scores` — same accurate-rowcount contract."""
        if not diagrams:
            return 0
        rows = [_diagram_to_row(d) for d in diagrams]
        stmt = pg_insert(sd).values(rows).on_conflict_do_nothing().returning(sd.c.id)
        result = self._conn.execute(stmt)
        return len(result.fetchall())

    def publish_generation(
        self,
        *,
        generation_id: UUID,
        station_id: StationId,
        model_id: ModelId,
        model_artifact_id: ArtifactId | None,
        parameter: str,
        skill_source: SkillSource,
        forcing_type: ForcingType | None,
        computation_version: int,
        published_at: UtcDatetime,
        score_count: int,
        diagram_count: int,
    ) -> None:
        """Plan 235 D3/D2c — the ONE atomic operation that makes a
        generation's already-inserted `skill_scores`/`skill_diagrams` rows
        current. A single `INSERT` — there is no partial state a reader can
        observe: either this row exists (and only ever gets inserted here
        AFTER the caller has confirmed every expected score/diagram write
        for `generation_id` already succeeded — the completeness gate lives
        in the caller, `flows.compute_skills`) or it does not, in which case
        readers keep seeing whatever was last published for this scope.

        INSERT-only (D2c): also the sanctioned replacement for `mark_stale`
        — publish with `score_count=diagram_count=0` for a scope to
        supersede its previous generation with an empty one (a tombstone),
        without ever issuing an `UPDATE`.

        D2d: no write-time rejection — a second, overlapping call for the
        same scope also just inserts; `latest_generation_predicate` decides
        which one readers see. `sapphire_worker` needs only `INSERT` on
        `skill_generations` (`docker/bootstrap-roles.sql`), matching its
        existing `skill_scores`/`skill_diagrams` grants.

        Fixer round (major, D1 retry stability): `generation_id` is now
        minted ONCE outside any retrying task and threaded in as a required
        argument, so a Prefect retry of the same task run replays this call
        with the IDENTICAL id. That replay must succeed, not crash on a
        primary-key violation — `ON CONFLICT (id) DO NOTHING` makes it a
        no-op when the id already exists. If it already exists under
        DIFFERENT scope-identity metadata, that is not a replay — it is an
        id collision across two different recomputes — and is rejected.
        `published_at`/`score_count`/`diagram_count` are allowed to differ
        across a replay (a retry recomputes its own clock reading and may
        legitimately re-derive the same counts by a different path); the
        scope identity fields (now including `model_artifact_id`, fixer
        round blocker) may not.
        """
        stmt = (
            pg_insert(sg)
            .values(
                id=generation_id,
                station_id=station_id,
                model_id=model_id,
                model_artifact_id=model_artifact_id,
                parameter=parameter,
                skill_source=skill_source.value,
                forcing_type=forcing_type.value if forcing_type is not None else None,
                computation_version=computation_version,
                published_at=published_at,
                score_count=score_count,
                diagram_count=diagram_count,
            )
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(sg.c.id)
        )
        inserted = self._conn.execute(stmt).first()
        if inserted is None:
            existing = (
                self._conn.execute(sa.select(sg).where(sg.c.id == generation_id))
                .mappings()
                .first()
            )
            expected_identity = {
                "station_id": station_id,
                "model_id": model_id,
                "model_artifact_id": model_artifact_id,
                "parameter": parameter,
                "skill_source": skill_source.value,
                "forcing_type": (
                    forcing_type.value if forcing_type is not None else None
                ),
                "computation_version": computation_version,
            }
            mismatched = {
                field: (existing[field], expected)  # type: ignore[index]
                for field, expected in expected_identity.items()
                if existing is not None and existing[field] != expected  # type: ignore[index]
            }
            if existing is None or mismatched:
                raise ValueError(
                    f"publish_generation replay for {generation_id} conflicts with "
                    f"already-published metadata under that id: {mismatched}"
                )
            log.info(
                "skill.publish_generation.replay",
                generation_id=str(generation_id),
                station_id=str(station_id),
                model_id=str(model_id),
                parameter=parameter,
                published_at=str(published_at),
            )
            return
        log.info(
            "skill.publish_generation",
            generation_id=str(generation_id),
            station_id=str(station_id),
            model_id=str(model_id),
            parameter=parameter,
            skill_source=skill_source.value,
            computation_version=computation_version,
            published_at=str(published_at),
            score_count=score_count,
            diagram_count=diagram_count,
        )

    def count_generation_rows(self, generation_id: UUID) -> tuple[int, int]:
        """Returns (score_count, diagram_count) ACTUALLY persisted under
        `generation_id`, regardless of WHICH attempt wrote them (fixer
        round, blocker — D1 retry stability). `store_skill_scores`/
        `store_skill_diagrams`'s own returned count is "rows THIS call
        inserted" — a Prefect retry replaying with the same (retry-stable)
        `generation_id` re-submits rows whose natural key + generation_id
        already exist from the earlier attempt, so `ON CONFLICT DO
        NOTHING` reports 0 newly inserted even though the generation is, in
        fact, already fully persisted. A caller reconciles against THIS
        method's total instead of the per-call insert count to tell that
        apart from a genuine gap (a real bug, or a crash that landed only
        some of a generation's rows) — see `flows.compute_skills`.
        """
        score_count = self._conn.execute(
            sa.select(sa.func.count())
            .select_from(ss)
            .where(ss.c.generation_id == generation_id)
        ).scalar_one()
        diagram_count = self._conn.execute(
            sa.select(sa.func.count())
            .select_from(sd)
            .where(sd.c.generation_id == generation_id)
        ).scalar_one()
        return score_count, diagram_count

    def fetch_latest_scores(
        self,
        station_id: StationId,
        model_id: ModelId,
        skill_source: SkillSource | None = None,
        parameter: str | None = None,
    ) -> list[SkillScore]:
        filters = [ss.c.station_id == station_id, ss.c.model_id == model_id]
        if skill_source is not None:
            filters.append(ss.c.skill_source == skill_source.value)
        if parameter is not None:
            filters.append(ss.c.parameter == parameter)

        stmt = sa.select(ss).where(*filters, latest_generation_predicate(ss))
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_score(row) for row in rows]

    def fetch_latest_diagrams(
        self,
        station_id: StationId,
        model_id: ModelId,
        diagram_type: Literal["reliability", "roc", "rank_histogram"] | None = None,
        parameter: str | None = None,
    ) -> list[SkillDiagram]:
        filters = [sd.c.station_id == station_id, sd.c.model_id == model_id]
        if diagram_type is not None:
            filters.append(sd.c.diagram_type == diagram_type)
        if parameter is not None:
            filters.append(sd.c.parameter == parameter)

        stmt = sa.select(sd).where(*filters, latest_generation_predicate(sd))
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_diagram(row) for row in rows]

    def fetch_scores_by_regime(
        self,
        station_id: StationId,
        model_id: ModelId,
        flow_regime: FlowRegime,
        parameter: str | None = None,
    ) -> list[SkillScore]:
        stmt = sa.select(ss).where(
            ss.c.station_id == station_id,
            ss.c.model_id == model_id,
            ss.c.flow_regime == flow_regime.value,
            latest_generation_predicate(ss),
        )
        if parameter is not None:
            stmt = stmt.where(ss.c.parameter == parameter)
        rows = self._conn.execute(stmt).mappings().all()
        return [_row_to_score(row) for row in rows]

    def fetch_skill_scores(
        self,
        model_id: ModelId,
        model_artifact_id: ArtifactId,
        parameter: str | None = None,
    ) -> tuple[SkillScore, ...]:
        filters = [
            ss.c.model_id == model_id,
            ss.c.model_artifact_id == model_artifact_id,
        ]
        if parameter is not None:
            filters.append(ss.c.parameter == parameter)
        stmt = sa.select(ss).where(*filters, latest_generation_predicate(ss))
        rows = self._conn.execute(stmt).mappings().all()
        return tuple(_row_to_score(row) for row in rows)

    def mark_stale(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        parameter: str | None = None,
    ) -> int:
        """⚠️ Plan 235 D2c — CANNOT run in production. This is a real
        `UPDATE`, and `sapphire_worker` holds `INSERT` only on
        `skill_scores` (`docker/bootstrap-roles.sql`) — deliberately not
        widened (see D2c). Kept only for the tests that lock this
        constraint (`tests/integration/db/test_role_bootstrap.py`); a
        production "mark stale" MUST go through `publish_generation` with
        `score_count=diagram_count=0` instead, which needs no `UPDATE`
        grant at all.
        """
        filters = [
            ss.c.station_id == station_id,
            ss.c.freshness == SkillFreshness.CURRENT.value,
            ss.c.eval_period_start < end,
            ss.c.eval_period_end > start,
        ]
        if parameter is not None:
            filters.append(ss.c.parameter == parameter)
        result = self._conn.execute(
            sa.update(ss).where(*filters).values(freshness=SkillFreshness.STALE.value)
        )
        return result.rowcount


def _score_to_row(s: SkillScore) -> dict:  # type: ignore[type-arg]
    return {
        "id": s.id,
        "station_id": s.station_id,
        "model_id": s.model_id,
        "model_artifact_id": s.model_artifact_id,
        "parameter": s.parameter,
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
        "time_step_seconds": s.time_step_seconds,
        "phase_offset_seconds": s.phase_offset_seconds,
        "generation_id": s.generation_id,
    }


def _diagram_to_row(d: SkillDiagram) -> dict:  # type: ignore[type-arg]
    return {
        "id": d.id,
        "station_id": d.station_id,
        "model_id": d.model_id,
        "model_artifact_id": d.model_artifact_id,
        "parameter": d.parameter,
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
        "time_step_seconds": d.time_step_seconds,
        "phase_offset_seconds": d.phase_offset_seconds,
        "generation_id": d.generation_id,
    }


def _row_to_score(row: sa.engine.row.RowMapping) -> SkillScore:
    forcing_raw = row["forcing_type"]
    flow_regime_raw = row["flow_regime"]
    return SkillScore(
        id=row["id"],
        station_id=StationId(row["station_id"]),
        model_id=ModelId(row["model_id"]),
        model_artifact_id=(
            ArtifactId(row["model_artifact_id"])
            if row["model_artifact_id"] is not None
            else None
        ),
        parameter=row["parameter"],
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
        time_step_seconds=row["time_step_seconds"],
        phase_offset_seconds=row["phase_offset_seconds"],
        generation_id=row["generation_id"],
    )


def _row_to_diagram(row: sa.engine.row.RowMapping) -> SkillDiagram:
    flow_regime_raw = row["flow_regime"]
    return SkillDiagram(
        id=row["id"],
        station_id=StationId(row["station_id"]),
        model_id=ModelId(row["model_id"]),
        model_artifact_id=(
            ArtifactId(row["model_artifact_id"])
            if row["model_artifact_id"] is not None
            else None
        ),
        parameter=row["parameter"],
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
        time_step_seconds=row["time_step_seconds"],
        phase_offset_seconds=row["phase_offset_seconds"],
        generation_id=row["generation_id"],
    )
