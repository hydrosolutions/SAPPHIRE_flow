"""Digest-bound read projections of validated selected-weather evidence."""

from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE forecast_weather_summaries (
 forecast_id uuid NOT NULL REFERENCES forecast_evidence(forecast_id),
 schema_version smallint NOT NULL,
 weather_evidence_version smallint NOT NULL,
 manifest_sha256 text NOT NULL, snapshot_sha256 text NOT NULL,
 summary_json text NOT NULL, summary_sha256 text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (forecast_id, schema_version),
 CONSTRAINT ck_weather_summary_schema CHECK (schema_version > 0),
 CONSTRAINT ck_weather_summary_evidence_version
  CHECK (weather_evidence_version IN (1,2)),
 CONSTRAINT ck_weather_summary_hashes CHECK (
  manifest_sha256 ~ '^[0-9a-f]{64}$' AND snapshot_sha256 ~ '^[0-9a-f]{64}$'
  AND summary_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE FUNCTION check_forecast_weather_summary_parent() RETURNS trigger AS $$
DECLARE parent record; manifest jsonb; summary jsonb;
BEGIN
 SELECT f.station_id, f.model_id, f.issued_at, f.data_use,
        f.weather_evidence_version, e.manifest_json, e.snapshot_sha256
 INTO parent FROM forecasts f JOIN forecast_evidence e ON e.forecast_id=f.id
 WHERE f.id=NEW.forecast_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'weather summary parent missing'; END IF;
 manifest := parent.manifest_json::jsonb;
 summary := NEW.summary_json::jsonb;
 IF parent.weather_evidence_version IS DISTINCT FROM NEW.weather_evidence_version
 OR parent.snapshot_sha256 IS DISTINCT FROM NEW.snapshot_sha256
 OR encode(sha256(convert_to(parent.manifest_json, 'UTF8')), 'hex')
    <> NEW.manifest_sha256
 OR encode(sha256(convert_to(NEW.summary_json, 'UTF8')), 'hex') <> NEW.summary_sha256
 OR (manifest->>'forecast_id') IS DISTINCT FROM NEW.forecast_id::text
 OR (manifest->>'station_id') IS DISTINCT FROM parent.station_id::text
 OR (manifest->>'model_id') IS DISTINCT FROM parent.model_id
 OR (manifest->>'data_use') IS DISTINCT FROM parent.data_use
 OR (manifest->>'issued_at')::timestamptz IS DISTINCT FROM parent.issued_at
 OR (manifest->>'weather_evidence_version')::integer
    IS DISTINCT FROM NEW.weather_evidence_version
 OR (manifest->>'snapshot_sha256') IS DISTINCT FROM NEW.snapshot_sha256
 OR jsonb_typeof(manifest->'weather_station_ids') IS DISTINCT FROM 'array'
 OR NOT ((manifest->'weather_station_ids') ? parent.station_id::text)
 OR (summary->>'station_id') IS DISTINCT FROM parent.station_id::text
 OR (summary->>'issue_time')::timestamptz IS DISTINCT FROM parent.issued_at
 THEN RAISE EXCEPTION 'weather summary parent binding mismatch'; END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_weather_summary_parent BEFORE INSERT ON forecast_weather_summaries
 FOR EACH ROW EXECUTE FUNCTION check_forecast_weather_summary_parent();
CREATE TRIGGER trg_weather_summary_append_only BEFORE UPDATE OR DELETE
 ON forecast_weather_summaries FOR EACH ROW
 EXECUTE FUNCTION reject_forecast_evidence_mutation();
CREATE TRIGGER trg_weather_summary_append_only_truncate BEFORE TRUNCATE
 ON forecast_weather_summaries FOR EACH STATEMENT
 EXECUTE FUNCTION reject_forecast_evidence_mutation();
DO $$ BEGIN
 IF EXISTS (SELECT FROM pg_roles WHERE rolname='sapphire_api') THEN
  GRANT SELECT ON forecast_weather_summaries TO sapphire_api;
 END IF;
 IF EXISTS (SELECT FROM pg_roles WHERE rolname='sapphire_worker') THEN
  GRANT SELECT, INSERT ON forecast_weather_summaries TO sapphire_worker;
 END IF;
END $$;
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE forecast_weather_summaries;
DROP FUNCTION check_forecast_weather_summary_parent();
""")
