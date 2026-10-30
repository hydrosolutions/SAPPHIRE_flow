"""Private append-only BAFU batch admission and committed receipt records."""

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE bafu_unit_batch_admissions (
    batch_id uuid PRIMARY KEY,
    disposition text NOT NULL CHECK (disposition IN ('admitted', 'rejected')),
    sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    station_id uuid NOT NULL,
    gauge_code text NOT NULL CHECK (gauge_code ~ '^[0-9]{4}$'),
    lindas_kind text NOT NULL CHECK (lindas_kind IN ('river', 'lake')),
    received_at timestamptz NOT NULL,
    registry_version text NOT NULL CHECK (length(btrim(registry_version)) > 0),
    registry_sha256 text NOT NULL CHECK (registry_sha256 ~ '^[0-9a-f]{64}$'),
    input_digest text NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    admitted_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT fk_bafu_admission_station FOREIGN KEY (station_id, tenant_id)
        REFERENCES stations(id, tenant_id),
    CONSTRAINT uq_bafu_admission_input UNIQUE (batch_id, input_digest)
);
CREATE INDEX ix_bafu_admission_station_sequence
    ON bafu_unit_batch_admissions (tenant_id, station_id, sequence DESC);
CREATE TABLE bafu_unit_batch_receipts (
    batch_id uuid PRIMARY KEY,
    input_digest text NOT NULL,
    result_digest text NOT NULL CHECK (result_digest ~ '^[0-9a-f]{64}$'),
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    committed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT fk_bafu_receipt_input FOREIGN KEY (batch_id, input_digest)
        REFERENCES bafu_unit_batch_admissions(batch_id, input_digest)
);
CREATE FUNCTION bafu_unit_journal_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'BAFU unit journal records are immutable';
END $$;
CREATE TRIGGER bafu_admission_immutable
    BEFORE UPDATE OR DELETE ON bafu_unit_batch_admissions
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_journal_immutable();
CREATE TRIGGER bafu_receipt_immutable
    BEFORE UPDATE OR DELETE ON bafu_unit_batch_receipts
    FOR EACH ROW EXECUTE FUNCTION bafu_unit_journal_immutable();
CREATE TRIGGER bafu_admission_no_truncate
    BEFORE TRUNCATE ON bafu_unit_batch_admissions
    FOR EACH STATEMENT EXECUTE FUNCTION bafu_unit_journal_immutable();
CREATE TRIGGER bafu_receipt_no_truncate
    BEFORE TRUNCATE ON bafu_unit_batch_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION bafu_unit_journal_immutable();
REVOKE ALL ON bafu_unit_batch_admissions, bafu_unit_batch_receipts FROM PUBLIC;
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM bafu_unit_batch_admissions)
       OR EXISTS (SELECT 1 FROM bafu_unit_batch_receipts) THEN
        RAISE EXCEPTION 'cannot discard populated BAFU unit journal';
    END IF;
END $$;
DROP TABLE bafu_unit_batch_receipts;
DROP TABLE bafu_unit_batch_admissions;
DROP FUNCTION bafu_unit_journal_immutable();
""")
