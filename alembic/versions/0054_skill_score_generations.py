"""Renumbered 0053 -> 0054 during the rebase onto main (2026-09-07).

Plan 241 T4 had already taken revision 0053 (`time_step_seconds` on
`forecasts`) and chained it onto 0052. Two migrations sharing
`down_revision = "0052"` would give alembic two heads and refuse to upgrade,
so this one now chains onto 0053 instead. No content change — only the
revision identifiers moved.

skill score generations — publication ledger + generation_id (Plan 235)

Revision ID: 0054
Revises: 0053
Create Date: 2026-09-04

Plan 235 D1/D2/D3/D2c/D2d — a recompute of an identical stratum (same
station/model/artifact/parameter/.../metric) after corrected observations
arrive currently carries the SAME natural key as the row it should replace,
so `INSERT ... ON CONFLICT DO NOTHING` silently drops it
(`services/skill/service.py`, `store/skill_store.py`). Bumping
`computation_version` per run was tried and RETRACTED (see migration 0052's
docstring) — it conflates algorithm version with a run counter and can
fragment or collide across mapped tasks.

This migration adds a stable, per-recompute GENERATION identity instead,
alongside `computation_version` (which keeps its existing meaning):

* `skill_generations` — an APPEND-ONLY publication ledger. A row here is
  the ONLY thing that makes a generation's `skill_scores`/`skill_diagrams`
  rows current (`store.skill_store._latest_generation_predicate`).
  INSERT-only by design (D2c): `sapphire_worker` holds `INSERT` only on
  `skill_scores`/`skill_diagrams` today (`docker/bootstrap-roles.sql`), and
  `mark_stale`'s `UPDATE` cannot run in production under that grant — this
  ledger removes the need for any `UPDATE` at all, including for "marking
  stale" (publish an empty-count generation for the scope instead, D2c).
* `generation_id` on `skill_scores`/`skill_diagrams` — nullable (one-release
  rollback rule, `docs/standards/cicd.md`); `NULL` marks a pre-Plan-235
  baseline row, given deterministic semantics below rather than backfilled,
  per the family review's "no over-engineering" guard.

Fixer round (blocker): `skill_generations` also carries a nullable
`model_artifact_id`. `skill_scores`/`skill_diagrams` are keyed by
`model_artifact_id` (NULL for POOLED/BMA combinations) — without it in the
ledger's own scope, `latest_generation_predicate` cannot distinguish two
different artifacts of the SAME model, so a generation minted for a
candidate artifact under evaluation (e.g. during retraining) could rank
above and supersede the STILL-ACTIVE artifact's generation for the same
(station, model, parameter, skill_source, forcing_type) scope. Included in
`ix_skill_generations_scope` via the same NULL-safe `COALESCE(...::text,
'')` pattern `uq_skill_scores_natural_key` already uses for this column.

Index changes, `computation_version >= 2` only (0052's `< 2` legacy index is
untouched, per that migration's own rule against tightening it further):

* `uq_skill_scores_natural_key` / `uq_skill_diagrams_natural_key` gain
  `AND generation_id IS NULL` on their existing predicate — UNCHANGED
  columns, so a pre-235 (or rolled-back) write keeps EXACTLY 0052's
  collision behavior, isolated from generation-tagged rows. Blocker this
  avoids: a raw nullable `generation_id` folded into ONE index (instead of
  split by predicate) would let PostgreSQL's every-NULL-is-distinct rule
  make that index a no-op for old-image writes, silently reintroducing
  0051's original NULL-collision bug for the generation axis.
* `uq_skill_scores_natural_key_generation` / `_diagrams_..._generation` are
  NEW: 0052's tightened key PLUS `generation_id` (not COALESCE'd — this
  partial index's own predicate, `generation_id IS NOT NULL`, guarantees
  it), for `generation_id IS NOT NULL`. A recompute's row at an identical
  stratum is now a DIFFERENT row under a different generation, not a
  collision — the corrected result survives (D1) and both an overlapping
  earlier and later publication persist untouched (D2d); precedence is
  decided entirely by readers via `skill_generations`, never by dropping a
  write.

Downgrade drops the two new indexes per table, restores each table's single
0052-shape (>= 2) index (dropping the `generation_id IS NULL` clause,
0052's exact predicate), drops both `generation_id` columns, and drops
`skill_generations` — restoring 0052's exact behavior, not introducing a
new gap.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_generations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
        # Fixer round (blocker) — see module docstring. Not a foreign key
        # to `model_artifacts.id`: like `skill_scores.model_artifact_id`,
        # a generation can legitimately name POOLED/BMA (NULL) rather than
        # one artifact.
        sa.Column("model_artifact_id", UUID(as_uuid=True), nullable=True),
        sa.Column("parameter", sa.Text, nullable=False),
        sa.Column("skill_source", sa.Text, nullable=False),
        sa.Column("forcing_type", sa.Text, nullable=True),
        sa.Column("computation_version", sa.Integer, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score_count", sa.Integer, nullable=False),
        sa.Column("diagram_count", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_skill_generations_scope",
        "skill_generations",
        [
            "station_id",
            "model_id",
            sa.text("COALESCE(model_artifact_id::text, '')"),
            "parameter",
            "skill_source",
            sa.text("COALESCE(forcing_type, '')"),
            "computation_version",
            "published_at",
        ],
        if_not_exists=True,
    )

    # Deliberately NOT a foreign key to `skill_generations.id` — see the
    # module docstring's "generation_id on skill_scores/skill_diagrams"
    # paragraph and `db/metadata.py`'s comment on the same column: a score/
    # diagram row is written WHILE its generation is still being computed,
    # before the `skill_generations` publication row exists (D3's
    # completeness gate publishes LAST). An FK would make that ordering
    # impossible.
    op.add_column(
        "skill_scores",
        sa.Column("generation_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "skill_diagrams",
        sa.Column("generation_id", UUID(as_uuid=True), nullable=True),
    )

    op.drop_index(
        "uq_skill_scores_natural_key", table_name="skill_scores", if_exists=True
    )
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            "model_id",
            sa.text("COALESCE(model_artifact_id::text, '')"),
            "parameter",
            "skill_source",
            sa.text("COALESCE(forcing_type, '')"),
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "metric",
            "time_step_seconds",
            sa.text("COALESCE(phase_offset_seconds, -1)"),
        ],
        unique=True,
        if_not_exists=True,
        postgresql_where=sa.text("computation_version >= 2 AND generation_id IS NULL"),
    )
    op.create_index(
        "uq_skill_scores_natural_key_generation",
        "skill_scores",
        [
            "station_id",
            "model_id",
            sa.text("COALESCE(model_artifact_id::text, '')"),
            "parameter",
            "skill_source",
            sa.text("COALESCE(forcing_type, '')"),
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "metric",
            "time_step_seconds",
            sa.text("COALESCE(phase_offset_seconds, -1)"),
            "generation_id",
        ],
        unique=True,
        if_not_exists=True,
        postgresql_where=sa.text(
            "computation_version >= 2 AND generation_id IS NOT NULL"
        ),
    )

    op.drop_index(
        "uq_skill_diagrams_natural_key", table_name="skill_diagrams", if_exists=True
    )
    op.create_index(
        "uq_skill_diagrams_natural_key",
        "skill_diagrams",
        [
            "station_id",
            "model_id",
            sa.text("COALESCE(model_artifact_id::text, '')"),
            "parameter",
            "skill_source",
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "diagram_type",
            sa.text("COALESCE(threshold_level, '')"),
            "time_step_seconds",
            sa.text("COALESCE(phase_offset_seconds, -1)"),
        ],
        unique=True,
        if_not_exists=True,
        postgresql_where=sa.text("computation_version >= 2 AND generation_id IS NULL"),
    )
    op.create_index(
        "uq_skill_diagrams_natural_key_generation",
        "skill_diagrams",
        [
            "station_id",
            "model_id",
            sa.text("COALESCE(model_artifact_id::text, '')"),
            "parameter",
            "skill_source",
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "diagram_type",
            sa.text("COALESCE(threshold_level, '')"),
            "time_step_seconds",
            sa.text("COALESCE(phase_offset_seconds, -1)"),
            "generation_id",
        ],
        unique=True,
        if_not_exists=True,
        postgresql_where=sa.text(
            "computation_version >= 2 AND generation_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_skill_diagrams_natural_key_generation",
        table_name="skill_diagrams",
        if_exists=True,
    )
    op.drop_index(
        "uq_skill_diagrams_natural_key", table_name="skill_diagrams", if_exists=True
    )
    op.create_index(
        "uq_skill_diagrams_natural_key",
        "skill_diagrams",
        [
            "station_id",
            "model_id",
            sa.text("COALESCE(model_artifact_id::text, '')"),
            "parameter",
            "skill_source",
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "diagram_type",
            sa.text("COALESCE(threshold_level, '')"),
            "time_step_seconds",
            sa.text("COALESCE(phase_offset_seconds, -1)"),
        ],
        unique=True,
        if_not_exists=True,
        postgresql_where=sa.text("computation_version >= 2"),
    )

    op.drop_index(
        "uq_skill_scores_natural_key_generation",
        table_name="skill_scores",
        if_exists=True,
    )
    op.drop_index(
        "uq_skill_scores_natural_key", table_name="skill_scores", if_exists=True
    )
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            "model_id",
            sa.text("COALESCE(model_artifact_id::text, '')"),
            "parameter",
            "skill_source",
            sa.text("COALESCE(forcing_type, '')"),
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "metric",
            "time_step_seconds",
            sa.text("COALESCE(phase_offset_seconds, -1)"),
        ],
        unique=True,
        if_not_exists=True,
        postgresql_where=sa.text("computation_version >= 2"),
    )

    op.drop_column("skill_diagrams", "generation_id")
    op.drop_column("skill_scores", "generation_id")
    op.drop_index(
        "ix_skill_generations_scope", table_name="skill_generations", if_exists=True
    )
    op.drop_table("skill_generations")
