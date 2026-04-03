"""Add model_ids and alert_model_strategy to alerts table.

Revision ID: 0018
Revises: 0017
Create Date: 2026-03-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column(
            "model_ids", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )
    op.add_column("alerts", sa.Column("alert_model_strategy", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("alerts", "alert_model_strategy")
    op.drop_column("alerts", "model_ids")
