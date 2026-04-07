"""Add group_model_assignments table.

Revision ID: 0021
Revises: 0020
Create Date: 2026-04-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INTERVAL, UUID

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "group_model_assignments",
        sa.Column("group_id", UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.Text, nullable=False),
        sa.Column("time_step", INTERVAL, nullable=False),
        sa.Column(
            "status",
            sa.Text,
            sa.CheckConstraint("status IN ('active', 'inactive')"),
            nullable=False,
            server_default="active",
        ),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["group_id"], ["station_groups.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("group_id", "model_id"),
    )


def downgrade() -> None:
    op.drop_table("group_model_assignments")
