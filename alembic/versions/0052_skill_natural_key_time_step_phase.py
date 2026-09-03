"""add time_step_seconds/phase_offset_seconds to skill natural keys (Plan 228)

Revision ID: 0052
Revises: 0051
Create Date: 2026-09-02

BLOCKER found reviewing Plan 228, second per-run scope round: `flows/compute_skills.py`
partitions a station/model's hindcast history into homogeneous `(time_step, phase)`
cohorts (`services/skill/service.py::partition_by_time_step_and_phase`) and computes +
stores skill once PER COHORT — deliberately, so a heterogeneous history degrades to
"fewer cohorts scored" rather than raising and scoring nothing
(`tests/unit/flows/test_compute_skills.py::test_mixed_time_step_history_degrades_gracefully`
asserts BOTH cohorts' scores are present).

But `uq_skill_scores_natural_key`/`uq_skill_diagrams_natural_key` (migration 0051)
still contain neither `time_step` nor `phase` — only `(station, model_artifact,
parameter, skill_source, forcing_type, computation_version, lead_time_hours,
season, flow_regime, metric)`. Two cohorts can legitimately produce a score row
identical on every one of those columns (e.g. a daily cohort's 24h lead and an
hourly cohort's 24h lead both land on `lead_time_hours=24`) — the second
cohort's `INSERT ... ON CONFLICT DO NOTHING` then collides with the first
cohort's row and is silently dropped. Latent today (this system is
single-cadence after Plan 228 D4), live the moment heterogeneity appears (a
retraining, or Plan 226's planned per-cycle anchoring).

Adds `time_step_seconds` (`Integer NOT NULL`, backfilled to `86400` — every
row in this system today is daily, matching migration 0050's identical
precedent) and `phase_offset_seconds` (`Integer`, nullable — an ensemble with
no `valid_time` has no phase) to both tables.

BLOCKER found on a second, independent review of this same migration:
`model_artifact_id` was still indexed RAW (nullable, no `COALESCE`) and
`model_id` was not indexed at all. `services/skill/combined_skill.py`
computes pooled/BMA scores with `model_artifact_id=None` always — for those
rows `model_id` (`POOLED_MODEL_ID` / `BMA_MODEL_ID`) is the ONLY column that
tells the two combination strategies apart, and PostgreSQL treats every
`NULL` as distinct from every other `NULL`, so two NULL-artifact rows never
collided under `ON CONFLICT DO NOTHING` — every repeated pooled/BMA
computation for the same stratum inserted a brand-new duplicate row instead
of being deduplicated.

BLOCKER found on a THIRD, independent (Codex) review: tightening
`model_artifact_id` from a raw nullable compare to a `COALESCE`d one is a
case where an index that previously treated two NULL rows as distinct now
treats them as equal — a LATER, legitimately different recomputation of the
same stratum at the same `computation_version` (e.g. `POOLED_MODEL_ID` run 1
vs. run 2, weeks apart) would then collide with the earlier one and be
silently dropped. An intermediate version of this migration "solved" that by
adding a manufactured per-run identity (`eval_period_end`) to the key. **That
approach is RETRACTED** (independent design check, 2026-09-03): a stable
recompute identity, "every consumer reads the newest generation," and atomic
mark-and-replace (including diagrams) are a bigger, interconnected design
that does not fit safely in one migration — see Plan 235, which owns all of
it. An even earlier version "solved" the same problem by DELETING the older
of any two colliding rows — irreversibly destroying score/diagram history,
in violation of `docs/standards/cicd.md`'s additive-only migration rule.
That DELETE is also gone, and stays gone.

**This migration now does exactly one thing about that third blocker: make
both unique indexes PARTIAL, so the tightened (`model_id` + NULL-safe
`model_artifact_id` + `time_step_seconds`/`phase_offset_seconds`) key applies
only to `computation_version >= 2`** — the cutoff this plan already defines
as the boundary of valid, post-fix data
(`docs/decisions/plan-228-hindcast-skill-resampling.md`). A plain
(non-partial) `CREATE UNIQUE INDEX` over the tightened key would **fail on
deploy**: under 0051's raw (non-`COALESCE`) compare, PostgreSQL treated every
NULL `model_artifact_id` as distinct, so the live mac-mini's ~115,000
`computation_version = 1` scores legitimately already contain repeated
NULL-artifact rows that collide once `model_artifact_id` is compared via
`COALESCE`. Restricting the tightened index to `computation_version >= 2`
grandfathers that known-invalid legacy generation untouched — **no row is
deleted or modified** — while enforcing the corrected NULL-safe key on every
row this system writes going forward (`_COMPUTATION_VERSION = 2` in
`services/skill/service.py`).

**Closing the hole this opens** (family review, 2026-09-03): a partial index
on `>= 2` by itself would remove 0051's uniqueness protection from **future**
`computation_version < 2` writes entirely — and those remain reachable,
because `docs/standards/cicd.md`'s one-version rollback-compatibility rule
means a previous image rolled back onto this schema can still emit a v1
score, and the store places no floor on `SkillScore.computation_version`.
Rather than add that floor as new application-level validation (broad blast
radius: dozens of existing tests deliberately exercise `store_skill_scores`
at `computation_version=1` to test version-selection behavior that has
nothing to do with this fix), this migration keeps a SECOND partial index —
`uq_skill_scores_natural_key_legacy` / `uq_skill_diagrams_natural_key_legacy`
— covering `computation_version < 2`, in EXACTLY 0051's original (raw,
non-`COALESCE`, no `model_id`, no `time_step_seconds`/`phase_offset_seconds`)
shape. A future v1 write is therefore still deduplicated under precisely the
same guarantee 0051 already gave it — no better, no worse, and no silent
regression to zero protection.

Because the two partial indexes are defined by a mutually exclusive
predicate (`>= 2` / `< 2`), every row is covered by exactly one of them and
neither can conflict with the other's rows.

`if_not_exists=True`/`if_exists=True` on every new index operation here per
`docs/conventions.md`'s migration convention.

Downgrade drops both new columns and both new indexes on each table, and
recreates each table's single 0051-shape (non-partial) index — restoring
0051's exact behavior, not introducing a new gap.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_TIME_STEP_SECONDS = 86400  # 1 day — every skill row today is daily


def upgrade() -> None:
    op.add_column(
        "skill_scores",
        sa.Column(
            "time_step_seconds",
            sa.Integer,
            nullable=False,
            server_default=str(_DEFAULT_TIME_STEP_SECONDS),
        ),
    )
    op.add_column(
        "skill_scores",
        sa.Column("phase_offset_seconds", sa.Integer, nullable=True),
    )
    op.add_column(
        "skill_diagrams",
        sa.Column(
            "time_step_seconds",
            sa.Integer,
            nullable=False,
            server_default=str(_DEFAULT_TIME_STEP_SECONDS),
        ),
    )
    op.add_column(
        "skill_diagrams",
        sa.Column("phase_offset_seconds", sa.Integer, nullable=True),
    )

    # Replace 0051's single, plain (all-versions) unique index with a PAIR
    # of partial indexes — see the module docstring for why a plain
    # `CREATE UNIQUE INDEX` over the tightened key fails against the live
    # mac-mini's legacy duplicates.
    op.drop_index(
        "uq_skill_scores_natural_key", table_name="skill_scores", if_exists=True
    )
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            # Blocker found reviewing this migration: pooled/BMA combined
            # scores always carry `model_artifact_id=NULL`
            # (`services/skill/combined_skill.py`), and PostgreSQL treats
            # every NULL as distinct — so without `model_id` here (the only
            # column distinguishing POOLED_MODEL_ID from BMA_MODEL_ID rows)
            # and without a NULL-safe cast on `model_artifact_id`, repeated
            # pooled/BMA computations insert duplicate rows forever instead
            # of colliding under `ON CONFLICT DO NOTHING`.
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
            # Plan 228 per-run scope (blocker, migration 0052): without
            # these, two (time_step, phase) cohorts sharing every other
            # column above collide under `ON CONFLICT DO NOTHING` and one
            # is silently dropped. `phase_offset_seconds` is nullable (an
            # ensemble with no valid_time), so it needs the same NULL-safe
            # COALESCE treatment as season/regime above.
            "time_step_seconds",
            sa.text("COALESCE(phase_offset_seconds, -1)"),
        ],
        unique=True,
        if_not_exists=True,
        postgresql_where=sa.text("computation_version >= 2"),
    )
    op.create_index(
        "uq_skill_scores_natural_key_legacy",
        "skill_scores",
        [
            "station_id",
            "model_artifact_id",
            "parameter",
            "skill_source",
            sa.text("COALESCE(forcing_type, '')"),
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "metric",
        ],
        unique=True,
        if_not_exists=True,
        postgresql_where=sa.text("computation_version < 2"),
    )

    op.drop_index(
        "uq_skill_diagrams_natural_key", table_name="skill_diagrams", if_exists=True
    )
    op.create_index(
        "uq_skill_diagrams_natural_key",
        "skill_diagrams",
        [
            "station_id",
            # See `uq_skill_scores_natural_key` above.
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
    op.create_index(
        "uq_skill_diagrams_natural_key_legacy",
        "skill_diagrams",
        [
            "station_id",
            "model_artifact_id",
            "parameter",
            "skill_source",
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "diagram_type",
            sa.text("COALESCE(threshold_level, '')"),
        ],
        unique=True,
        if_not_exists=True,
        postgresql_where=sa.text("computation_version < 2"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_skill_diagrams_natural_key_legacy",
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
            "model_artifact_id",
            "parameter",
            "skill_source",
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "diagram_type",
            sa.text("COALESCE(threshold_level, '')"),
        ],
        unique=True,
        if_not_exists=True,
    )

    op.drop_index(
        "uq_skill_scores_natural_key_legacy", table_name="skill_scores", if_exists=True
    )
    op.drop_index(
        "uq_skill_scores_natural_key", table_name="skill_scores", if_exists=True
    )
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            "model_artifact_id",
            "parameter",
            "skill_source",
            sa.text("COALESCE(forcing_type, '')"),
            "computation_version",
            "lead_time_hours",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            "metric",
        ],
        unique=True,
        if_not_exists=True,
    )

    op.drop_column("skill_diagrams", "phase_offset_seconds")
    op.drop_column("skill_diagrams", "time_step_seconds")
    op.drop_column("skill_scores", "phase_offset_seconds")
    op.drop_column("skill_scores", "time_step_seconds")
