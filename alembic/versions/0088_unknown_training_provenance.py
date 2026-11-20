"""Explicit unknown external training provenance for the MVP."""

from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE model_artifacts
 ADD COLUMN training_provenance text NOT NULL DEFAULT 'known',
 ALTER COLUMN trained_at DROP NOT NULL,
 ALTER COLUMN training_period_start DROP NOT NULL,
 ALTER COLUMN training_period_end DROP NOT NULL;
ALTER TABLE model_artifacts ADD CONSTRAINT ck_model_artifacts_training_provenance
 CHECK ((training_provenance = 'known' AND trained_at IS NOT NULL
         AND training_period_start IS NOT NULL AND training_period_end IS NOT NULL)
     OR (training_provenance = 'unknown' AND trained_at IS NULL
         AND training_period_start IS NULL AND training_period_end IS NULL));
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_api') THEN
  GRANT SELECT (training_provenance) ON model_artifacts TO sapphire_api;
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_worker') THEN
  GRANT SELECT (training_provenance), INSERT (training_provenance)
   ON model_artifacts TO sapphire_worker;
 END IF;
END $$;
""")


def downgrade() -> None:
    op.execute("""
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM model_artifacts WHERE training_provenance = 'unknown') THEN
  RAISE EXCEPTION 'cannot downgrade with unknown training provenance';
 END IF;
END $$;
ALTER TABLE model_artifacts
 DROP CONSTRAINT ck_model_artifacts_training_provenance,
 DROP COLUMN training_provenance,
 ALTER COLUMN trained_at SET NOT NULL,
 ALTER COLUMN training_period_start SET NOT NULL,
 ALTER COLUMN training_period_end SET NOT NULL;
""")
