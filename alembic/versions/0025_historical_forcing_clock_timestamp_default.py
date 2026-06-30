"""historical_forcing.created_at server default -> clock_timestamp()

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-30

now()/transaction_timestamp() is constant for the whole transaction, so two
versions of the same logical key inserted in ONE transaction tie on created_at
and the latest-version supersession in ``PgHistoricalForcingStore.fetch_forcing``
becomes nondeterministic (id is a random UUID). clock_timestamp() returns a
row-level wall clock, giving same-transaction inserts distinct, insertion-ordered
created_at values so ``ORDER BY created_at DESC`` is deterministic.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "historical_forcing",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def downgrade() -> None:
    op.alter_column(
        "historical_forcing",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        server_default=sa.text("now()"),
    )
