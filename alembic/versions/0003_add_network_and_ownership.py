"""Add network and ownership columns to stations and basins

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- stations ---
    # Drop old unique constraint on code
    op.drop_constraint("stations_code_key", "stations", type_="unique")

    # Add network column (DEFAULT 'bafu' for existing rows)
    op.add_column(
        "stations",
        sa.Column("network", sa.Text, nullable=False, server_default="bafu"),
    )
    # Remove server_default after backfilling existing rows
    op.alter_column("stations", "network", server_default=None)

    # Add ownership column
    op.add_column(
        "stations",
        sa.Column("ownership", sa.Text, nullable=False, server_default="own"),
    )
    op.create_check_constraint(
        "ck_stations_ownership",
        "stations",
        "ownership IN ('own', 'foreign')",
    )

    # Add wigos_id column
    op.add_column("stations", sa.Column("wigos_id", sa.Text, nullable=True))

    # Add composite unique constraint
    op.create_unique_constraint(
        "uq_stations_network_code", "stations", ["network", "code"]
    )

    # --- basins ---
    # Drop old unique constraint on code
    op.drop_constraint("basins_code_key", "basins", type_="unique")

    # Add network column (DEFAULT 'bafu' for existing rows)
    op.add_column(
        "basins",
        sa.Column("network", sa.Text, nullable=False, server_default="bafu"),
    )
    op.alter_column("basins", "network", server_default=None)

    # Add composite unique constraint
    op.create_unique_constraint(
        "uq_basins_network_code", "basins", ["network", "code"]
    )


def downgrade() -> None:
    # --- basins ---
    op.drop_constraint("uq_basins_network_code", "basins", type_="unique")
    op.drop_column("basins", "network")
    op.create_unique_constraint("basins_code_key", "basins", ["code"])

    # --- stations ---
    op.drop_constraint("uq_stations_network_code", "stations", type_="unique")
    op.drop_column("stations", "wigos_id")
    op.drop_constraint("ck_stations_ownership", "stations")
    op.drop_column("stations", "ownership")
    op.drop_column("stations", "network")
    op.create_unique_constraint("stations_code_key", "stations", ["code"])
