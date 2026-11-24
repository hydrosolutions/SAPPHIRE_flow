"""Preserve declared forecast periods without relabelling existing rows."""

from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE forecasts ADD COLUMN period_definition jsonb;
ALTER TABLE forecasts ADD CONSTRAINT ck_forecasts_period_definition CHECK
 (period_definition IS NULL OR jsonb_typeof(period_definition) = 'object');
CREATE FUNCTION reject_forecast_period_mutation() RETURNS trigger AS $$
BEGIN
 IF OLD.period_definition IS DISTINCT FROM NEW.period_definition THEN
  RAISE EXCEPTION 'forecast period definition is immutable';
 END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_forecast_period_immutable
 BEFORE UPDATE OF period_definition ON forecasts
 FOR EACH ROW EXECUTE FUNCTION reject_forecast_period_mutation();
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_api') THEN
  GRANT SELECT (period_definition) ON forecasts TO sapphire_api;
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_worker') THEN
  GRANT SELECT (period_definition) ON forecasts TO sapphire_worker;
 END IF;
END $$;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM forecasts WHERE period_definition IS NOT NULL) THEN
  RAISE EXCEPTION 'cannot remove stored forecast period definitions';
 END IF;
END $$;
DROP TRIGGER trg_forecast_period_immutable ON forecasts;
DROP FUNCTION reject_forecast_period_mutation();
ALTER TABLE forecasts DROP COLUMN period_definition;
""")
