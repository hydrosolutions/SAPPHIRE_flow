"""Versioned weather period context; preserve retained v1 evidence."""

from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE operational_weather_cache ADD COLUMN temporal_context jsonb;
ALTER TABLE operational_weather_cache DROP CONSTRAINT ck_operational_weather_version;
ALTER TABLE operational_weather_cache ADD CONSTRAINT ck_operational_weather_version
 CHECK (payload_version IN (1,2) AND attempt_generation > 0);
ALTER TABLE operational_weather_cache ADD CONSTRAINT ck_operational_weather_context
 CHECK ((payload_version = 1 AND temporal_context IS NULL) OR
        (payload_version = 2 AND temporal_context IS NOT NULL
         AND jsonb_typeof(temporal_context) = 'object'));
ALTER TABLE forecasts DROP CONSTRAINT ck_forecasts_weather_evidence_version;
ALTER TABLE forecasts ADD CONSTRAINT ck_forecasts_weather_evidence_version
 CHECK (weather_evidence_version IS NULL OR weather_evidence_version IN (1,2));
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM operational_weather_cache WHERE payload_version = 2)
 OR EXISTS (SELECT 1 FROM forecasts WHERE weather_evidence_version = 2) THEN
  RAISE EXCEPTION 'cannot remove retained v2 weather period evidence';
 END IF;
END $$;
ALTER TABLE forecasts DROP CONSTRAINT ck_forecasts_weather_evidence_version;
ALTER TABLE forecasts ADD CONSTRAINT ck_forecasts_weather_evidence_version
 CHECK (weather_evidence_version IS NULL OR weather_evidence_version = 1);
ALTER TABLE operational_weather_cache DROP CONSTRAINT ck_operational_weather_context;
ALTER TABLE operational_weather_cache DROP CONSTRAINT ck_operational_weather_version;
ALTER TABLE operational_weather_cache ADD CONSTRAINT ck_operational_weather_version
 CHECK (payload_version = 1 AND attempt_generation > 0);
ALTER TABLE operational_weather_cache DROP COLUMN temporal_context;
""")
