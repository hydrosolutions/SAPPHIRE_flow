"""Append-only record for a QC-rejected member/group-station forecast
(Plan 404 T1) — never `forecasts` (D2).

Revision ID: 0065
Revises: 0064
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rejected_forecasts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("attempt_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "station_id",
            UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text, sa.ForeignKey("models.id"), nullable=False),
        sa.Column(
            "model_artifact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("model_artifacts.id"),
            nullable=True,
        ),
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("station_groups.id"),
            nullable=True,
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parameter", sa.Text, nullable=False),
        sa.Column("units", sa.Text, nullable=False),
        sa.Column("representation", sa.Text, nullable=False),
        sa.Column("time_step_seconds", sa.Integer, nullable=False),
        sa.Column("values", JSONB, nullable=False),
        sa.Column("qc_status", sa.Text, nullable=False),
        sa.Column("qc_flags", JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "representation IN ('members', 'quantiles')",
            name="ck_rejected_forecasts_representation",
        ),
        sa.CheckConstraint(
            "qc_status IN ('qc_failed', 'qc_suspect', 'qc_passed', 'qc_unchecked')",
            name="ck_rejected_forecasts_qc_status",
        ),
        sa.CheckConstraint(
            "time_step_seconds > 0", name="ck_rejected_forecasts_time_step_positive"
        ),
    )
    op.create_index(
        "ix_rejected_forecasts_station_issued_at",
        "rejected_forecasts",
        ["station_id", "issued_at"],
    )

    # Role-independent append-only guard — mirrors migration 0057's
    # `forecast_evidence`/`forecast_evidence_blobs` triggers: UPDATE, DELETE
    # and TRUNCATE are refused even for the table-owning role.
    op.execute("""
        CREATE FUNCTION reject_rejected_forecast_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'rejected_forecasts is append-only: %', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_rejected_forecasts_append_only
        BEFORE UPDATE OR DELETE ON rejected_forecasts
        FOR EACH ROW EXECUTE FUNCTION reject_rejected_forecast_mutation();
    """)
    op.execute("""
        CREATE TRIGGER trg_rejected_forecasts_append_only_truncate
        BEFORE TRUNCATE ON rejected_forecasts
        FOR EACH STATEMENT EXECUTE FUNCTION reject_rejected_forecast_mutation();
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_rejected_forecasts_append_only_truncate "
        "ON rejected_forecasts"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_rejected_forecasts_append_only "
        "ON rejected_forecasts"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_rejected_forecast_mutation()")
    op.drop_index("ix_rejected_forecasts_station_issued_at", "rejected_forecasts")
    op.drop_table("rejected_forecasts")
