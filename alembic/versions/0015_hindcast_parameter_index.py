"""Add parameter to hindcast_forecasts compound index.

Revision ID: 0015
Revises: 0014
Create Date: 2026-03-26

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_hindcast_forecasts_station_model_step_param",
        "hindcast_forecasts",
        ["station_id", "model_id", "hindcast_step", "parameter"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hindcast_forecasts_station_model_step_param",
        table_name="hindcast_forecasts",
    )
