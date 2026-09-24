"""Append-only operational forecast evidence (Plan 340 T1).

Revision ID: 0057
Revises: 0056
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BYTEA, UUID

from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forecast_evidence_blobs",
        sa.Column("sha256", sa.Text, primary_key=True),
        sa.Column("payload", BYTEA, nullable=False),
        sa.Column("byte_length", sa.BigInteger, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(sha256) = 64", name="ck_forecast_evidence_blob_hash_length"
        ),
        sa.CheckConstraint("byte_length >= 0", name="ck_forecast_evidence_blob_length"),
    )
    op.create_table(
        "forecast_evidence",
        sa.Column(
            "forecast_id",
            UUID(as_uuid=True),
            sa.ForeignKey("forecasts.id"),
            primary_key=True,
        ),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("manifest_json", sa.Text, nullable=False),
        sa.Column(
            "snapshot_sha256",
            sa.Text,
            sa.ForeignKey("forecast_evidence_blobs.sha256"),
            nullable=True,
        ),
        sa.Column(
            "artifact_sha256",
            sa.Text,
            sa.ForeignKey("forecast_evidence_blobs.sha256"),
            nullable=True,
        ),
        sa.Column("thresholds_json", sa.Text, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'evidence_incomplete')",
            name="ck_forecast_evidence_status",
        ),
        sa.CheckConstraint(
            "status <> 'complete' OR (snapshot_sha256 IS NOT NULL "
            "AND thresholds_json IS NOT NULL AND reason IS NULL)",
            name="ck_forecast_evidence_complete",
        ),
        sa.CheckConstraint(
            "status <> 'evidence_incomplete' OR reason IS NOT NULL",
            name="ck_forecast_evidence_incomplete_reason",
        ),
    )
    op.execute("""
        CREATE FUNCTION reject_forecast_evidence_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'forecast evidence is append-only: %', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in ("forecast_evidence", "forecast_evidence_blobs"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_forecast_evidence_mutation();
        """)
        op.execute(f"""
            CREATE TRIGGER trg_{table}_append_only_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_forecast_evidence_mutation();
        """)


def downgrade() -> None:
    for table in ("forecast_evidence", "forecast_evidence_blobs"):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_append_only_truncate ON {table}"
        )
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_forecast_evidence_mutation()")
    op.drop_table("forecast_evidence")
    op.drop_table("forecast_evidence_blobs")
