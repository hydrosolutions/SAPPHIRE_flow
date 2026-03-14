"""Add QC_MISSING status to observations

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-13

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop old qc_status CHECK constraint (try both possible auto-generated names)
    op.execute(
        "ALTER TABLE observations DROP CONSTRAINT IF EXISTS ck_observations_qc_status"
    )
    op.execute(
        "ALTER TABLE observations "
        "DROP CONSTRAINT IF EXISTS observations_qc_status_check"
    )

    # Add new CHECK with 'missing' included
    op.create_check_constraint(
        "ck_observations_qc_status",
        "observations",
        "qc_status IN ('raw', 'qc_passed', 'qc_failed', 'qc_suspect', 'missing')",
    )

    # Make value nullable
    op.alter_column("observations", "value", existing_type=sa.Float(), nullable=True)

    # Add bidirectional constraint: missing ↔ NULL value
    op.create_check_constraint(
        "ck_observations_missing_value",
        "observations",
        "(qc_status = 'missing') = (value IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_observations_missing_value", "observations")
    op.alter_column("observations", "value", existing_type=sa.Float(), nullable=False)
    op.drop_constraint("ck_observations_qc_status", "observations")
    op.create_check_constraint(
        "ck_observations_qc_status",
        "observations",
        "qc_status IN ('raw', 'qc_passed', 'qc_failed', 'qc_suspect')",
    )
