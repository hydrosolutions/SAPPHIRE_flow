"""Retain frozen water-level-threshold evaluation evidence per skill generation."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_generations",
        sa.Column(
            "threshold_evaluation_manifest", JSONB(none_as_null=True), nullable=True
        ),
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM skill_generations "
            "WHERE threshold_evaluation_manifest IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError(
            "cannot remove retained rated-threshold evidence; "
            "restore or retire it with owner approval first"
        )
    op.drop_column("skill_generations", "threshold_evaluation_manifest")
