"""Add sha256_hash to model_artifacts.

Revision ID: 0022
Revises: 0021
Create Date: 2026-04-07

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_artifacts",
        sa.Column("sha256_hash", sa.Text, nullable=True),
    )
    op.execute("UPDATE model_artifacts SET sha256_hash = '' WHERE sha256_hash IS NULL")
    op.alter_column("model_artifacts", "sha256_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("model_artifacts", "sha256_hash")
