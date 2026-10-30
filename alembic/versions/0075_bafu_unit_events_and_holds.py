"""Private BAFU unit evidence and additive read-hold records; no activation."""

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE bafu_unit_batch_admissions ADD CONSTRAINT uq_bafu_admission_scope
    UNIQUE (batch_id, tenant_id, station_id);
CREATE TABLE bafu_unit_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    station_id uuid NOT NULL,
    measurement_time timestamptz,
    parameter text NOT NULL DEFAULT 'discharge' CHECK (parameter = 'discharge'),
    source text NOT NULL DEFAULT 'measured' CHECK (source = 'measured'),
    outcome text NOT NULL CHECK (outcome IN
        ('inserted', 'restated', 'evidence_only', 'quarantined', 'rejected')),
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    observation_id uuid,
    canonical_value double precision,
    value_state_sha256 text CHECK (value_state_sha256 ~ '^[0-9a-f]{64}$'),
    capture jsonb NOT NULL CHECK (jsonb_typeof(capture) = 'object'),
    before_state jsonb CHECK (jsonb_typeof(before_state) = 'object'),
    after_state jsonb CHECK (jsonb_typeof(after_state) = 'object'),
    reason text,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT fk_bafu_event_batch FOREIGN KEY (batch_id, tenant_id, station_id)
        REFERENCES bafu_unit_batch_admissions(batch_id, tenant_id, station_id),
    CONSTRAINT fk_bafu_event_observation FOREIGN KEY (observation_id, station_id)
        REFERENCES observations(id, station_id),
    CONSTRAINT uq_bafu_event_scope UNIQUE (event_id, tenant_id, station_id),
    CONSTRAINT uq_bafu_event_batch_fingerprint UNIQUE (batch_id, fingerprint),
    CONSTRAINT ck_bafu_event_shape CHECK (
        (outcome IN ('inserted', 'restated', 'evidence_only')
            AND measurement_time IS NOT NULL AND observation_id IS NOT NULL
            AND canonical_value IS NOT NULL AND value_state_sha256 IS NOT NULL
            AND after_state IS NOT NULL AND reason IS NULL)
        OR (outcome IN ('quarantined', 'rejected') AND observation_id IS NULL
            AND canonical_value IS NULL AND value_state_sha256 IS NULL
            AND after_state IS NULL AND reason IS NOT NULL)),
    CONSTRAINT ck_bafu_event_finite CHECK
        (canonical_value IS NULL OR canonical_value NOT IN
            ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8))
);
CREATE INDEX ix_bafu_event_current
    ON bafu_unit_events (tenant_id, station_id, measurement_time, event_id DESC);
CREATE TABLE bafu_unit_read_holds (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    station_id uuid NOT NULL,
    parameter text NOT NULL DEFAULT 'discharge' CHECK (parameter = 'discharge'),
    source text NOT NULL DEFAULT 'measured' CHECK (source = 'measured'),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz,
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    origin text NOT NULL CHECK (origin IN ('ingest', 'maintenance')),
    event_id bigint,
    approval_sha256 text CHECK (approval_sha256 ~ '^[0-9a-f]{64}$'),
    registry_sha256 text NOT NULL CHECK (registry_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT fk_bafu_hold_station FOREIGN KEY (station_id, tenant_id)
        REFERENCES stations(id, tenant_id),
    CONSTRAINT fk_bafu_hold_event FOREIGN KEY (event_id, tenant_id, station_id)
        REFERENCES bafu_unit_events(event_id, tenant_id, station_id),
    CONSTRAINT ck_bafu_hold_interval CHECK (ends_at IS NULL OR starts_at < ends_at),
    CONSTRAINT ck_bafu_hold_authority CHECK (
        (origin = 'ingest' AND event_id IS NOT NULL AND approval_sha256 IS NULL)
        OR (origin = 'maintenance' AND event_id IS NULL
            AND approval_sha256 IS NOT NULL)),
    CONSTRAINT uq_bafu_hold_event UNIQUE (event_id)
);
CREATE INDEX ix_bafu_hold_scope ON bafu_unit_read_holds
    (tenant_id, station_id, parameter, source, starts_at);
CREATE TABLE bafu_unit_hold_releases (
    id uuid PRIMARY KEY,
    hold_id uuid NOT NULL REFERENCES bafu_unit_read_holds(id),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz,
    approval_sha256 text NOT NULL CHECK (approval_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT ck_bafu_release_interval CHECK (ends_at IS NULL OR starts_at < ends_at)
);
CREATE INDEX ix_bafu_release_hold ON bafu_unit_hold_releases(hold_id, starts_at);
CREATE FUNCTION bafu_unit_hold_bounds() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_start timestamptz; parent_end timestamptz;
        event_time timestamptz; event_outcome text;
BEGIN
    IF TG_TABLE_NAME = 'bafu_unit_hold_releases' THEN
        SELECT starts_at, ends_at INTO STRICT parent_start, parent_end
            FROM bafu_unit_read_holds WHERE id = NEW.hold_id;
        IF NEW.starts_at < parent_start OR
           (parent_end IS NOT NULL
            AND (NEW.ends_at IS NULL OR NEW.ends_at > parent_end)) THEN
            RAISE EXCEPTION 'release interval must lie within its named hold';
        END IF;
    ELSIF NEW.origin = 'ingest' THEN
        SELECT measurement_time, outcome INTO STRICT event_time, event_outcome
            FROM bafu_unit_events WHERE event_id = NEW.event_id;
        IF event_outcome <> 'quarantined' OR event_time IS NULL
           OR NEW.starts_at <> event_time
           OR NEW.ends_at IS DISTINCT FROM event_time + interval '1 microsecond' THEN
            RAISE EXCEPTION 'ingest hold must cover its quarantined measurement only';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER bafu_hold_bounds BEFORE INSERT ON bafu_unit_read_holds
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_hold_bounds();
CREATE TRIGGER bafu_release_bounds BEFORE INSERT ON bafu_unit_hold_releases
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_hold_bounds();
CREATE TRIGGER bafu_event_immutable BEFORE UPDATE OR DELETE ON bafu_unit_events
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_journal_immutable();
CREATE TRIGGER bafu_event_no_truncate BEFORE TRUNCATE ON bafu_unit_events
    FOR EACH STATEMENT EXECUTE FUNCTION bafu_unit_journal_immutable();
CREATE TRIGGER bafu_hold_immutable BEFORE UPDATE OR DELETE ON bafu_unit_read_holds
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_journal_immutable();
CREATE TRIGGER bafu_hold_no_truncate BEFORE TRUNCATE ON bafu_unit_read_holds
    FOR EACH STATEMENT EXECUTE FUNCTION bafu_unit_journal_immutable();
CREATE TRIGGER bafu_release_immutable BEFORE UPDATE OR DELETE ON bafu_unit_hold_releases
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_journal_immutable();
CREATE TRIGGER bafu_release_no_truncate BEFORE TRUNCATE ON bafu_unit_hold_releases
    FOR EACH STATEMENT EXECUTE FUNCTION bafu_unit_journal_immutable();
REVOKE ALL ON bafu_unit_events, bafu_unit_read_holds, bafu_unit_hold_releases
    FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM bafu_unit_events)
       OR EXISTS (SELECT 1 FROM bafu_unit_read_holds)
       OR EXISTS (SELECT 1 FROM bafu_unit_hold_releases) THEN
        RAISE EXCEPTION 'cannot discard populated BAFU unit evidence or dispositions';
    END IF;
END $$;
DROP TABLE bafu_unit_hold_releases;
DROP TABLE bafu_unit_read_holds;
DROP TABLE bafu_unit_events;
DROP FUNCTION bafu_unit_hold_bounds();
ALTER TABLE bafu_unit_batch_admissions DROP CONSTRAINT uq_bafu_admission_scope;
""")
