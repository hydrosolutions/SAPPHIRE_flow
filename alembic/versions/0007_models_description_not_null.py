"""Make models.description NOT NULL

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-20

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE models SET description = '' WHERE description IS NULL")
    op.alter_column("models", "description", nullable=False)


def downgrade() -> None:
    op.alter_column("models", "description", nullable=True)
