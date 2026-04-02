"""Add gauging_status column to stations (nullable with default).

Step 1 of 2: add as nullable with server_default so the previous app image
can still INSERT rows without knowledge of this column during rolling deployment.

Revision ID: 0019
Revises: 0018
Create Date: 2026-04-02

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stations",
        sa.Column("gauging_status", sa.Text, nullable=True, server_default="gauged"),
    )
    op.create_check_constraint(
        "ck_stations_gauging_status",
        "stations",
        "gauging_status IN ('gauged', 'ungauged', 'calculated')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_stations_gauging_status", "stations", type_="check")
    op.drop_column("stations", "gauging_status")
