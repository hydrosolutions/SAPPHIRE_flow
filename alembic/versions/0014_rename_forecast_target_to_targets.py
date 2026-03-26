"""Rename forecast_target to forecast_targets (JSONB array)

Revision ID: 0014
Revises: 0013
Create Date: 2026-03-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stations", sa.Column("forecast_targets", JSONB, nullable=True))
    op.execute(
        """
        UPDATE stations SET forecast_targets = CASE
            WHEN forecast_target = 'both' THEN '["discharge", "water_level"]'::jsonb
            WHEN forecast_target IS NOT NULL THEN jsonb_build_array(forecast_target)
            ELSE NULL
        END
        """
    )
    op.drop_column("stations", "forecast_target")


def downgrade() -> None:
    op.add_column("stations", sa.Column("forecast_target", sa.Text, nullable=True))
    op.execute(
        """
        UPDATE stations SET forecast_target = CASE
            WHEN forecast_targets IS NOT NULL THEN forecast_targets->>0
            ELSE NULL
        END
        """
    )
    op.drop_column("stations", "forecast_targets")
