"""station water-level datum metadata

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stations",
        sa.Column("water_level_datum_masl", sa.Float(), nullable=True),
    )
    op.add_column(
        "stations",
        sa.Column("water_level_unit", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stations", "water_level_unit")
    op.drop_column("stations", "water_level_datum_masl")
