"""Add clim_baselines table for climatological baseline storage

Revision ID: 0011
Revises: 0010
Create Date: 2026-03-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clim_baselines",
        sa.Column("station_id", UUID(as_uuid=True), nullable=False),
        sa.Column("parameter", sa.Text, nullable=False),
        sa.Column("day_of_year", sa.Integer, nullable=False),
        sa.Column("rolling_mean", sa.Float, nullable=False),
        sa.Column("rolling_std", sa.Float, nullable=False),
        sa.Column("sample_count", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"]),
        sa.PrimaryKeyConstraint("station_id", "parameter", "day_of_year"),
        sa.CheckConstraint(
            "day_of_year >= 1 AND day_of_year <= 366",
            name="ck_clim_baselines_day_of_year",
        ),
    )


def downgrade() -> None:
    op.drop_table("clim_baselines")
