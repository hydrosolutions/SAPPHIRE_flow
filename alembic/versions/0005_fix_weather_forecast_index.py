"""Replace weather_forecasts ascending cycle_time index with descending variant

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the ascending index created in 0001
    op.drop_index(
        "ix_weather_forecasts_station_source_valid_cycle",
        table_name="weather_forecasts",
    )
    # Create the descending variant matching metadata.py
    op.create_index(
        "ix_weather_forecasts_station_source_valid_cycle_desc",
        "weather_forecasts",
        ["station_id", "nwp_source", "valid_time", sa.text("cycle_time DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_weather_forecasts_station_source_valid_cycle_desc",
        table_name="weather_forecasts",
    )
    op.create_index(
        "ix_weather_forecasts_station_source_valid_cycle",
        "weather_forecasts",
        ["station_id", "nwp_source", "valid_time", "cycle_time"],
    )
