"""rating_curves table (Plan 035 Task 1)

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-19

Creates the ``rating_curves`` table (architecture-context.md §2206, extended
per Plan 035 Task 1): one row per rating-curve version per station, with a
partial UNIQUE index enforcing at most one active (``valid_to IS NULL``)
curve per station and a UNIQUE ``(station_id, version)`` index enforcing
monotonic versioning.

``uploaded_by`` is added as ``UUID NULL`` without an FK constraint — the
``users`` table does not yet exist in this migration chain. A later v1
migration adds the FK once ``users`` is created (Plan 035 Task 1 design
note).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rating_curves",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("points", JSONB, nullable=False),
        sa.Column(
            "interpolation",
            sa.Text,
            nullable=False,
            server_default="linear",
        ),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "station_id", "version", name="uq_rating_curves_station_version"
        ),
    )
    op.create_index(
        "ix_rating_curves_station_valid_from",
        "rating_curves",
        ["station_id", sa.text("valid_from DESC")],
    )
    op.create_index(
        "uq_rating_curves_station_active",
        "rating_curves",
        ["station_id"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_rating_curves_station_active", table_name="rating_curves")
    op.drop_index("ix_rating_curves_station_valid_from", table_name="rating_curves")
    op.drop_table("rating_curves")
