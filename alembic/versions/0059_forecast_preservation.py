"""Interim forecast preservation attestations and no-cleanup guards (Plan 340 T2).

Revision ID: 0059
Revises: 0058
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forecast_preservation_attestations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "forecast_id",
            UUID(as_uuid=True),
            sa.ForeignKey("forecast_evidence.forecast_id"),
            nullable=False,
        ),
        sa.Column("backup_id", UUID(as_uuid=True), nullable=False),
        sa.Column("capture_manifest_sha256", sa.Text, nullable=False),
        sa.Column("snapshot_sha256", sa.Text, nullable=False),
        sa.Column("forecast_values_sha256", sa.Text, nullable=False),
        sa.Column("artifact_sha256", sa.Text, nullable=True),
        sa.Column("runtime_image_digest", sa.Text, nullable=False),
        sa.Column("backup_manifest_sha256", sa.Text, nullable=False),
        sa.Column("database_dump_sha256", sa.Text, nullable=False),
        sa.Column("image_archive_sha256", sa.Text, nullable=False),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "forecast_id", "backup_id", name="uq_forecast_preservation_forecast_backup"
        ),
        sa.CheckConstraint(
            "runtime_image_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_forecast_preservation_image_digest",
        ),
    )
    op.execute("""
        CREATE FUNCTION reject_forecast_preservation_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'forecast preservation is append-only: %', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_forecast_preservation_append_only
        BEFORE UPDATE OR DELETE ON forecast_preservation_attestations
        FOR EACH ROW EXECUTE FUNCTION reject_forecast_preservation_mutation();
    """)
    op.execute("""
        CREATE TRIGGER trg_forecast_preservation_append_only_truncate
        BEFORE TRUNCATE ON forecast_preservation_attestations
        FOR EACH STATEMENT EXECUTE FUNCTION reject_forecast_preservation_mutation();
    """)
    op.execute("""
        CREATE FUNCTION protect_evidence_linked_records() RETURNS trigger AS $$
        DECLARE linked_forecast_id uuid;
        BEGIN
            IF TG_OP = 'TRUNCATE' THEN
                RAISE EXCEPTION 'evidence-linked records cannot be truncated';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF EXISTS (
                    SELECT 1 FROM forecast_evidence WHERE forecast_id = NEW.forecast_id
                ) THEN
                    RAISE EXCEPTION 'evidence-linked forecast output cannot be added';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME = 'forecast_values' THEN
                linked_forecast_id := OLD.forecast_id;
            ELSIF TG_TABLE_NAME = 'forecasts' THEN
                linked_forecast_id := OLD.id;
                IF TG_OP = 'UPDATE' THEN
                    IF EXISTS (
                        SELECT 1 FROM forecast_evidence WHERE forecast_id = OLD.id
                    ) AND (
                        to_jsonb(NEW) - 'status' - 'version' - 'updated_at'
                    ) IS DISTINCT FROM (
                        to_jsonb(OLD) - 'status' - 'version' - 'updated_at'
                    ) THEN
                        RAISE EXCEPTION 'evidence-linked forecast is immutable';
                    END IF;
                    RETURN NEW;
                END IF;
            ELSIF TG_TABLE_NAME = 'model_artifacts' THEN
                IF EXISTS (
                    SELECT 1 FROM forecasts f JOIN forecast_evidence e
                    ON e.forecast_id = f.id WHERE f.model_artifact_id = OLD.id
                ) THEN
                    RAISE EXCEPTION 'evidence-linked model artifact cannot be removed';
                END IF;
                RETURN OLD;
            END IF;
            IF EXISTS (
                SELECT 1 FROM forecast_evidence WHERE forecast_id = linked_forecast_id
            ) THEN
                RAISE EXCEPTION 'evidence-linked forecast output cannot be removed';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF EXISTS (
                    SELECT 1 FROM forecast_evidence WHERE forecast_id = NEW.forecast_id
                ) THEN
                    RAISE EXCEPTION 'evidence-linked output cannot be reassigned';
                END IF;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                RETURN NEW;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in ("forecasts", "forecast_values", "model_artifacts"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_evidence_no_delete
            BEFORE DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION protect_evidence_linked_records();
        """)
        op.execute(f"""
            CREATE TRIGGER trg_{table}_evidence_no_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION protect_evidence_linked_records();
        """)
    op.execute("""
        CREATE TRIGGER trg_forecast_values_evidence_no_update
        BEFORE UPDATE ON forecast_values
        FOR EACH ROW EXECUTE FUNCTION protect_evidence_linked_records();
    """)
    op.execute("""
        CREATE TRIGGER trg_forecast_values_evidence_no_insert
        BEFORE INSERT ON forecast_values
        FOR EACH ROW EXECUTE FUNCTION protect_evidence_linked_records();
    """)
    op.execute("""
        CREATE TRIGGER trg_forecasts_evidence_immutable_identity
        BEFORE UPDATE ON forecasts
        FOR EACH ROW EXECUTE FUNCTION protect_evidence_linked_records();
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_forecasts_evidence_immutable_identity ON forecasts"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_forecast_values_evidence_no_insert "
        "ON forecast_values"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_forecast_values_evidence_no_update "
        "ON forecast_values"
    )
    for table in ("forecasts", "forecast_values", "model_artifacts"):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_evidence_no_truncate ON {table}"
        )
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_evidence_no_delete ON {table}")
    op.execute("DROP FUNCTION IF EXISTS protect_evidence_linked_records()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_forecast_preservation_append_only_truncate "
        "ON forecast_preservation_attestations"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_forecast_preservation_append_only "
        "ON forecast_preservation_attestations"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_forecast_preservation_mutation()")
    op.drop_table("forecast_preservation_attestations")
