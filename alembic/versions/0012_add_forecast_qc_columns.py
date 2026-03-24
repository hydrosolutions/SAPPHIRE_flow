"""Add QC columns to forecasts/hindcasts and forecast_qc_overrides table

Adds qc_status and qc_flags columns to forecasts and hindcast_forecasts
tables for forecast output quality checking. Creates the
forecast_qc_overrides table for per-station forecast QC threshold overrides.

Revision ID: 0012
Revises: 0011
Create Date: 2026-03-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # forecasts: add qc_status and qc_flags
    op.add_column(
        "forecasts",
        sa.Column("qc_status", sa.Text, nullable=False, server_default="raw"),
    )
    op.add_column(
        "forecasts",
        sa.Column("qc_flags", JSONB, nullable=False, server_default="[]"),
    )

    # hindcast_forecasts: add qc_status and qc_flags
    op.add_column(
        "hindcast_forecasts",
        sa.Column("qc_status", sa.Text, nullable=False, server_default="raw"),
    )
    op.add_column(
        "hindcast_forecasts",
        sa.Column("qc_flags", JSONB, nullable=False, server_default="[]"),
    )

    # forecast_qc_overrides table
    op.create_table(
        "forecast_qc_overrides",
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("rule_id", sa.Text, nullable=False),
        sa.Column("parameter", sa.Text, nullable=False),
        sa.Column("time_step_seconds", sa.Integer, nullable=False),
        sa.Column("thresholds", JSONB, nullable=False),
        sa.UniqueConstraint(
            "station_id",
            "rule_id",
            "parameter",
            "time_step_seconds",
            name="uq_forecast_qc_overrides_natural_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("forecast_qc_overrides")
    op.drop_column("hindcast_forecasts", "qc_flags")
    op.drop_column("hindcast_forecasts", "qc_status")
    op.drop_column("forecasts", "qc_flags")
    op.drop_column("forecasts", "qc_status")
