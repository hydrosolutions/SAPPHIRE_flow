"""calculated_station_formulas table (Plan 015)

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-20

One row per (calculated station, component, parameter) validity window for the weighted-sum
derivation `Q_virtual = Σ(wᵢ · Qᵢ)`. Partial UNIQUE `(calculated_station_id,
component_station_id, parameter) WHERE effective_to IS NULL` enforces at most one current
formula row per triple (mirrors the rating_curves active-curve precedent — no btree_gist).
The eligibility trigger is added in the next revision (0038) so its rollback is granular.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calculated_station_formulas",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "calculated_station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column(
            "component_station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("parameter", sa.Text, nullable=False),
        sa.Column("weight", sa.Float, nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "calculated_station_id != component_station_id",
            name="ck_csf_distinct_stations",
        ),
        sa.CheckConstraint(
            "weight != 0 AND weight > -1e6 AND weight < 1e6",
            name="ck_csf_weight_bounds",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_csf_validity_order",
        ),
    )
    op.create_index(
        "ix_csf_component",
        "calculated_station_formulas",
        ["component_station_id"],
    )
    op.create_index(
        "uq_csf_current",
        "calculated_station_formulas",
        ["calculated_station_id", "component_station_id", "parameter"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_csf_current", table_name="calculated_station_formulas")
    op.drop_index("ix_csf_component", table_name="calculated_station_formulas")
    op.drop_table("calculated_station_formulas")
