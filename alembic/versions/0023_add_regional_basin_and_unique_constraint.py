"""Add regional_basin column and unique station-basin constraint.

Revision ID: 0023
Revises: 0022
Create Date: 2026-04-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("basins", sa.Column("regional_basin", sa.Text, nullable=True))

    result = op.get_bind().execute(
        sa.text(
            "SELECT basin_id FROM stations"
            " WHERE basin_id IS NOT NULL"
            " GROUP BY basin_id HAVING count(*) > 1"
        )
    )
    duplicates = [row[0] for row in result]
    if duplicates:
        raise RuntimeError(
            f"Cannot create unique index uq_stations_basin_id: "
            f"duplicate basin_id values found in stations table: {duplicates}"
        )

    op.create_index(
        "uq_stations_basin_id",
        "stations",
        ["basin_id"],
        unique=True,
        postgresql_where=sa.text("basin_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_stations_basin_id", table_name="stations")
    op.drop_column("basins", "regional_basin")
