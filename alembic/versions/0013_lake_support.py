"""Add LAKE station kind and parameter column to flow_regime_configs

Revision ID: 0013
Revises: 0012
Create Date: 2026-03-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Update station_kind CHECK constraint to include 'lake'
    op.drop_constraint("stations_station_kind_check", "stations", type_="check")
    op.create_check_constraint(
        "ck_stations_station_kind",
        "stations",
        "station_kind IN ('weather', 'river', 'lake')",
    )

    # Add parameter column to flow_regime_configs
    op.add_column(
        "flow_regime_configs",
        sa.Column("parameter", sa.Text, nullable=False, server_default="discharge"),
    )
    # Drop server_default after backfill — all existing rows are discharge
    op.alter_column("flow_regime_configs", "parameter", server_default=None)


def downgrade() -> None:
    op.drop_column("flow_regime_configs", "parameter")

    op.drop_constraint("ck_stations_station_kind", "stations", type_="check")
    op.create_check_constraint(
        "stations_station_kind_check",
        "stations",
        "station_kind IN ('weather', 'river')",
    )
