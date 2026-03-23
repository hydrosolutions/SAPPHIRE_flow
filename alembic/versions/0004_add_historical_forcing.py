"""Add historical_forcing table

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "historical_forcing",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("valid_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parameter", sa.Text, nullable=False),
        sa.Column(
            "spatial_type",
            sa.Text,
            sa.CheckConstraint(
                "spatial_type IN ('point', 'basin_average', 'elevation_band')"
            ),
            nullable=False,
        ),
        sa.Column("band_id", sa.Integer, nullable=True),
        sa.Column("member_id", sa.Integer, nullable=True),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(spatial_type = 'elevation_band' AND band_id IS NOT NULL) OR "
            "(spatial_type != 'elevation_band' AND band_id IS NULL)",
            name="ck_historical_forcing_band_id_consistency",
        ),
    )

    op.create_index(
        "ix_historical_forcing_station_source_valid",
        "historical_forcing",
        ["station_id", "source", "valid_time"],
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_historical_forcing_natural_key "
        "ON historical_forcing (station_id, source, version, valid_time, parameter, "
        "spatial_type, COALESCE(band_id, -1), COALESCE(member_id, -1))"
    )


def downgrade() -> None:
    op.drop_index("uq_historical_forcing_natural_key", table_name="historical_forcing")
    op.drop_index(
        "ix_historical_forcing_station_source_valid",
        table_name="historical_forcing",
    )
    op.drop_table("historical_forcing")
