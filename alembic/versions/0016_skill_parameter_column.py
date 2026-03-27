"""Add parameter column to skill_scores and skill_diagrams tables.

Revision ID: 0016
Revises: 0015
Create Date: 2026-03-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add parameter column using server_default/drop pattern (matches migration 0008).
    # Safe even if skill rows already exist in a developer's local DB.
    op.add_column("skill_scores", sa.Column("parameter", sa.Text, nullable=False, server_default="discharge"))
    op.add_column("skill_diagrams", sa.Column("parameter", sa.Text, nullable=False, server_default="discharge"))
    op.alter_column("skill_scores", "parameter", server_default=None)
    op.alter_column("skill_diagrams", "parameter", server_default=None)

    # Update unique index to include parameter as discriminator
    op.drop_index("uq_skill_scores_natural_key", table_name="skill_scores")
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            "model_artifact_id",
            "skill_source",
            "parameter",
            "lead_time_hours",
            "metric",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            sa.text("COALESCE(forcing_type, '')"),
        ],
        unique=True,
    )

    # NEW — skill_diagrams had no unique constraint at all (pre-existing gap)
    op.create_index(
        "uq_skill_diagrams_natural_key",
        "skill_diagrams",
        [
            "station_id",
            "model_artifact_id",
            "skill_source",
            "parameter",
            "lead_time_hours",
            "diagram_type",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            sa.text("COALESCE(threshold_level, '')"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_skill_diagrams_natural_key", table_name="skill_diagrams")
    op.drop_index("uq_skill_scores_natural_key", table_name="skill_scores")
    op.create_index(
        "uq_skill_scores_natural_key",
        "skill_scores",
        [
            "station_id",
            "model_artifact_id",
            "skill_source",
            "lead_time_hours",
            "metric",
            sa.text("COALESCE(season, '')"),
            sa.text("COALESCE(flow_regime, '')"),
            sa.text("COALESCE(forcing_type, '')"),
        ],
        unique=True,
    )
    op.drop_column("skill_diagrams", "parameter")
    op.drop_column("skill_scores", "parameter")
