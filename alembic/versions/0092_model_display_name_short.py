"""Store optional compact model labels without changing model identity."""

from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE models ADD COLUMN display_name_short text;
ALTER TABLE models ADD CONSTRAINT ck_models_display_name_short CHECK (
 display_name_short IS NULL OR (
  length(display_name_short) BETWEEN 1 AND 32
  AND display_name_short !~ '^[[:space:]]|[[:space:]]$'
 )
);
UPDATE models SET display_name_short = 'CMAL SE'
 WHERE id = 'cmal_nepal_static_encoder_20261123' AND display_name_short IS NULL;
""")


def downgrade() -> None:
    op.drop_constraint("ck_models_display_name_short", "models", type_="check")
    op.drop_column("models", "display_name_short")
