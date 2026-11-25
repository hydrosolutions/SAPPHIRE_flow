"""Preserve threshold probability verification provenance and paired counts."""

from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("skill_scores", "skill_diagrams"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN threshold_context jsonb")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_threshold_context "
            "CHECK (threshold_context IS NULL OR "
            "jsonb_typeof(threshold_context) = 'object')"
        )


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM skill_scores WHERE threshold_context IS NOT NULL)
 OR EXISTS (SELECT 1 FROM skill_diagrams WHERE threshold_context IS NOT NULL) THEN
  RAISE EXCEPTION 'cannot remove stored threshold skill context';
 END IF;
END $$;
ALTER TABLE skill_scores DROP COLUMN threshold_context;
ALTER TABLE skill_diagrams DROP COLUMN threshold_context;
""")
