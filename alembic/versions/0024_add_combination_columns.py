"""Add multi-model combination columns and nullable artifact_id.

Revision ID: 0024
Revises: 0023
Create Date: 2026-04-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update artifact_scope CHECK constraint on models table
    op.drop_constraint("models_artifact_scope_check", "models", type_="check")
    op.create_check_constraint(
        "models_artifact_scope_check",
        "models",
        "artifact_scope IN ('station', 'group', 'virtual')",
    )

    # 2. Add combination columns to forecasts
    op.add_column(
        "forecasts", sa.Column("combination_strategy", sa.Text, nullable=True)
    )
    op.add_column("forecasts", sa.Column("source_model_ids", JSONB, nullable=True))

    # 3. Make model_artifact_id nullable on forecasts
    op.alter_column(
        "forecasts", "model_artifact_id", existing_type=sa.UUID, nullable=True
    )

    # 4. Make model_artifact_id nullable on skill_scores
    op.alter_column(
        "skill_scores", "model_artifact_id", existing_type=sa.UUID, nullable=True
    )

    # 5. Make model_artifact_id nullable on skill_diagrams
    op.alter_column(
        "skill_diagrams", "model_artifact_id", existing_type=sa.UUID, nullable=True
    )

    # 6. Insert sentinel model entries
    _sql = (
        "INSERT INTO models (id, display_name, artifact_scope, description) VALUES "
        "('_pooled', 'Pooled Ensemble', 'virtual',"
        " 'Grand ensemble from all models'), "
        "('_bma', 'BMA Ensemble', 'virtual',"
        " 'Bayesian Model Averaging weighted ensemble'), "
        "('_consensus', 'Consensus Forecast', 'virtual',"
        " 'Consensus across models')"
    )
    op.execute(sa.text(_sql))


def downgrade() -> None:
    # Remove sentinel models
    op.execute(
        sa.text("DELETE FROM models WHERE id IN ('_pooled', '_bma', '_consensus')")
    )

    # Revert nullable changes
    op.alter_column(
        "skill_diagrams", "model_artifact_id", existing_type=sa.UUID, nullable=False
    )
    op.alter_column(
        "skill_scores", "model_artifact_id", existing_type=sa.UUID, nullable=False
    )
    op.alter_column(
        "forecasts", "model_artifact_id", existing_type=sa.UUID, nullable=False
    )

    # Remove combination columns
    op.drop_column("forecasts", "source_model_ids")
    op.drop_column("forecasts", "combination_strategy")

    # Revert CHECK constraint
    op.drop_constraint("models_artifact_scope_check", "models", type_="check")
    op.create_check_constraint(
        "models_artifact_scope_check",
        "models",
        "artifact_scope IN ('station', 'group')",
    )
