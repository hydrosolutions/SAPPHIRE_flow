"""Rename flow_regime_configs q50/q90 columns to p50/p90

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-20

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("flow_regime_configs", "q50", new_column_name="p50")
    op.alter_column("flow_regime_configs", "q90", new_column_name="p90")


def downgrade() -> None:
    op.alter_column("flow_regime_configs", "p50", new_column_name="q50")
    op.alter_column("flow_regime_configs", "p90", new_column_name="q90")
