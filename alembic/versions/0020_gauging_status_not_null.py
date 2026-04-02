"""Make gauging_status NOT NULL.

Step 2 of 2: all existing rows have the default 'gauged' from migration 0019.
Now enforce NOT NULL.

Revision ID: 0020
Revises: 0019
Create Date: 2026-04-02

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("stations", "gauging_status", nullable=False)


def downgrade() -> None:
    op.alter_column("stations", "gauging_status", nullable=True)
