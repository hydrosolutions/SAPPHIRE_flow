"""Immutable selected-weather evidence version on the forecast parent."""

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE forecasts ADD COLUMN weather_evidence_version smallint;
ALTER TABLE forecasts ADD CONSTRAINT ck_forecasts_weather_evidence_version
 CHECK (weather_evidence_version IS NULL OR weather_evidence_version = 1);
CREATE FUNCTION reject_weather_evidence_version_mutation() RETURNS trigger AS $$
BEGIN
 IF OLD.weather_evidence_version IS DISTINCT FROM NEW.weather_evidence_version THEN
  RAISE EXCEPTION 'forecast weather evidence version is immutable';
 END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_weather_evidence_version_immutable
 BEFORE UPDATE OF weather_evidence_version ON forecasts
 FOR EACH ROW EXECUTE FUNCTION reject_weather_evidence_version_mutation();
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_api') THEN
  GRANT SELECT (weather_evidence_version) ON forecasts TO sapphire_api;
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sapphire_worker') THEN
  GRANT SELECT (weather_evidence_version) ON forecasts TO sapphire_worker;
 END IF;
END $$;
""")


def downgrade() -> None:
    op.execute("""
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM forecasts WHERE weather_evidence_version IS NOT NULL) THEN
  RAISE EXCEPTION 'cannot remove retained selected-weather evidence identity';
 END IF;
END $$;
DROP TRIGGER trg_weather_evidence_version_immutable ON forecasts;
DROP FUNCTION reject_weather_evidence_version_mutation();
ALTER TABLE forecasts DROP COLUMN weather_evidence_version;
""")
