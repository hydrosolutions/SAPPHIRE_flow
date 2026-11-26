"""Per-station ingest-paused switch (#501): stops observation polling only."""

from alembic import op

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE stations ADD COLUMN ingest_paused boolean NOT NULL DEFAULT false;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE stations DROP COLUMN ingest_paused;")
