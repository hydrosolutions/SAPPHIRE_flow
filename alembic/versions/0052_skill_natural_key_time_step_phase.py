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

Downgrade drops both columns and reverts both indexes to their 0051 shape.
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

    op.drop_index("uq_skill_scores_natural_key", table_name="skill_scores")
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
            "time_step_seconds",
            sa.text("COALESCE(phase_offset_seconds, -1)"),
        ],
        unique=True,
    )

    op.drop_index("uq_skill_diagrams_natural_key", table_name="skill_diagrams")
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
            "time_step_seconds",
            sa.text("COALESCE(phase_offset_seconds, -1)"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_skill_diagrams_natural_key", table_name="skill_diagrams")
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
    )

    op.drop_index("uq_skill_scores_natural_key", table_name="skill_scores")
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
    )

    op.drop_column("skill_diagrams", "phase_offset_seconds")
    op.drop_column("skill_diagrams", "time_step_seconds")
    op.drop_column("skill_scores", "phase_offset_seconds")
    op.drop_column("skill_scores", "time_step_seconds")
