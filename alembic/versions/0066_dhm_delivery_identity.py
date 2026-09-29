"""Identify DHM delivery observations and rating curves for scoped replacement.

Revision ID: 0066
Revises: 0065
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("observations", sa.Column("delivery_id", sa.Text(), nullable=True))
    op.add_column("rating_curves", sa.Column("delivery_id", sa.Text(), nullable=True))
    op.add_column(
        "rating_curves", sa.Column("rating_type_label", sa.Text(), nullable=True)
    )
    op.create_index(
        "ix_observations_delivery_id",
        "observations",
        ["delivery_id"],
        postgresql_where=sa.text("delivery_id IS NOT NULL"),
    )
    op.create_index(
        "ix_rating_curves_delivery_id",
        "rating_curves",
        ["delivery_id"],
        postgresql_where=sa.text("delivery_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_rating_curves_delivery_id", table_name="rating_curves")
    op.drop_index("ix_observations_delivery_id", table_name="observations")
    op.drop_column("rating_curves", "rating_type_label")
    op.drop_column("rating_curves", "delivery_id")
    op.drop_column("observations", "delivery_id")
