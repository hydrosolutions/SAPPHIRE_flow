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
no `valid_time` has no phase) to both tables, and widens both natural-key
unique indexes to include them. `phase_offset_seconds` gets the same
NULL-safe `COALESCE` treatment migration 0051 already uses for
`season`/`flow_regime`/`forcing_type`, since NULL never equals NULL in a
unique index. Adding columns to a unique index only SHRINKS the set of rows
considered duplicates, so no pre-existing row (uniformly backfilled to the
same `(86400, NULL)` pair) can violate the new, less restrictive constraint.

BLOCKER found on a second, independent review of this same migration:
`model_artifact_id` was still indexed RAW (nullable, no `COALESCE`) and
`model_id` was not indexed at all. `services/skill/combined_skill.py`
computes pooled/BMA scores with `model_artifact_id=None` always — for those
rows `model_id` (`POOLED_MODEL_ID` / `BMA_MODEL_ID`) is the ONLY column that
tells the two combination strategies apart, and PostgreSQL treats every
`NULL` as distinct from every other `NULL`, so two NULL-artifact rows never
collided under `ON CONFLICT DO NOTHING` — every repeated pooled/BMA
computation for the same stratum inserted a brand-new duplicate row instead
of being deduplicated. Fixed here (not a new migration) since 0052 has not
shipped to any environment yet: `model_id` is now part of both natural
keys, and `model_artifact_id` is compared via
`COALESCE(model_artifact_id::text, '')` — the same NULL-safe pattern already
used for `season`/`flow_regime`/`forcing_type`/`phase_offset_seconds`.

BLOCKER found on a THIRD, independent (Codex) review: tightening
`model_artifact_id` from a raw nullable compare to a `COALESCE`d one is a
case where an index that previously treated two NULL rows as distinct now
treats them as equal — the `model_id`/`COALESCE` fix above makes
`(POOLED, run 1)` and `(BMA, run 1)` distinct again, but does NOTHING to
stop `(POOLED, run 1)` and `(POOLED, run 2)` — a LATER, legitimately
different recomputation of the same stratum — from colliding, because
`computation_version` is a static schema/algorithm marker (bumped only
when the scoring CODE changes — see `services/skill/service.py::
_COMPUTATION_VERSION`), not a per-run identity. A previous version of this
migration "solved" that by DELETING the older of any two colliding rows —
irreversibly destroying score/diagram history, in violation of
`docs/standards/cicd.md`'s additive-only migration rule (rollback depends
on no release destroying data the previous image tag might still read).
That DELETE is gone.

Instead, `eval_period_end` (an existing, always-populated column since
migration 0016) is now ALSO part of both natural keys. It is the run's own
identity: fixed for every row a single skill-computation call produces
(one `eval_start`/`eval_end` pair per `compute_skill_for_station`/
`compute_combined_skill` call), and strictly later for a later
recomputation of the same stratum, since more observations become
available over time. A retry of the SAME run (identical eval window)
still collides on the natural key and stays idempotent under `ON CONFLICT
DO NOTHING`; a LATER recomputation gets a distinct key, is inserted as a
NEW row — the earlier one is never deleted, so history stays fully
auditable — and `PgSkillStore.fetch_latest_scores`/`fetch_latest_diagrams`
(same PR) surface it as current by preferring the greatest
`eval_period_end` within the greatest `computation_version`.

Because `eval_period_end` is now part of the key, no pre-existing row can
be destroyed by tightening `model_artifact_id`/`model_id`: two rows that
would have collided under the tightened key alone (e.g. two
`POOLED_MODEL_ID` computations of the same stratum, weeks apart) do NOT
collide once `eval_period_end` is added, because they were never computed
over the same observation window. A genuine byte-for-byte duplicate (same
stratum, same eval window) still collides — that is correct
deduplication, not data loss. If some already-deployed database
nonetheless produced a true duplicate under an intermediate schema state,
`CREATE UNIQUE INDEX` below fails loudly instead of silently discarding a
row, which is the correct outcome under the same additive-only standard —
a migration should surface an unexpected data problem, not paper over it.

`if_not_exists=True`/`if_exists=True` on every new index operation here
per `docs/conventions.md`'s migration convention.

Downgrade drops both new columns and reverts both indexes to their 0051
shape (which also excluded `eval_period_end` from the key — restoring
0051's exact behavior, not introducing a new gap).
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
            "time_step_seconds",
            sa.text("COALESCE(phase_offset_seconds, -1)"),
            # Third-review blocker: the run's own identity — see the module
            # docstring. Without this, a later recomputation of the same
            # stratum at the same `computation_version` collides with the
            # earlier one and is silently dropped by `ON CONFLICT DO
            # NOTHING` instead of superseding it.
            "eval_period_end",
        ],
        unique=True,
        if_not_exists=True,
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
            # See `uq_skill_scores_natural_key` above.
            "eval_period_end",
        ],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
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
