"""Widen forecast unique index to include parameter.

Revision ID: 0017
Revises: 0016
Create Date: 2026-03-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_forecasts_station_model_issued", table_name="forecasts")
    op.create_index(
        "uq_forecasts_station_model_issued_param",
        "forecasts",
        ["station_id", "model_id", "issued_at", "parameter"],
        unique=True,
        postgresql_where=sa.text("status != 'superseded'"),
    )


def downgrade() -> None:
    op.drop_index("uq_forecasts_station_model_issued_param", table_name="forecasts")
    op.create_index(
        "uq_forecasts_station_model_issued",
        "forecasts",
        ["station_id", "model_id", "issued_at"],
        unique=True,
        postgresql_where=sa.text("status != 'superseded'"),
    )
