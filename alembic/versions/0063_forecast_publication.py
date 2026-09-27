"""Forecast publication decisions, selection, feed and host backup health.

Revision ID: 0063
Revises: 0062
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_forecasts_publication_identity",
        "forecasts",
        ["id", "station_id", "parameter", "issued_at"],
    )
    op.create_unique_constraint(
        "uq_forecast_preservation_proof_identity",
        "forecast_preservation_attestations",
        ["id", "forecast_id", "backup_id"],
    )
    op.create_table(
        "protected_backup_health",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("backup_id", UUID(as_uuid=True), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_separate", sa.Boolean(), nullable=False),
        sa.Column("retention_ready", sa.Boolean(), nullable=False),
        sa.Column("manifest_sha256", sa.Text(), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_protected_backup_health_singleton"),
        sa.CheckConstraint(
            "status IN ('verified', 'missing', 'stale', 'invalid')",
            name="ck_protected_backup_health_status",
        ),
        sa.CheckConstraint(
            "status <> 'verified' OR (backup_id IS NOT NULL "
            "AND restored_at IS NOT NULL AND manifest_sha256 IS NOT NULL "
            "AND target_separate AND retention_ready)",
            name="ck_protected_backup_health_verified",
        ),
    )
    op.create_table(
        "protected_backup_forecast_proofs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("forecast_id", UUID(as_uuid=True), nullable=False),
        sa.Column("attestation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("backup_id", UUID(as_uuid=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["attestation_id", "forecast_id", "backup_id"],
            [
                "forecast_preservation_attestations.id",
                "forecast_preservation_attestations.forecast_id",
                "forecast_preservation_attestations.backup_id",
            ],
            name="fk_protected_backup_proof_exact_attestation",
        ),
    )
    op.create_index(
        "ix_protected_backup_forecast_proofs_forecast",
        "protected_backup_forecast_proofs",
        ["forecast_id", "verified_at"],
    )
    op.create_table(
        "forecast_publication_selections",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("station_id", UUID(as_uuid=True), nullable=False),
        sa.Column("parameter", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selected_forecast_id", UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("linked_warning_publication_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "station_id", "parameter", "issued_at"),
        sa.ForeignKeyConstraint(
            ["station_id", "tenant_id"],
            ["stations.id", "stations.tenant_id"],
            name="fk_forecast_publication_selection_station_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["selected_forecast_id", "station_id", "parameter", "issued_at"],
            [
                "forecasts.id",
                "forecasts.station_id",
                "forecasts.parameter",
                "forecasts.issued_at",
            ],
            name="fk_forecast_publication_selection_exact_forecast",
        ),
        sa.CheckConstraint(
            "version >= 0", name="ck_forecast_publication_selection_version"
        ),
        sa.CheckConstraint(
            "linked_warning_publication_id IS NULL OR selected_forecast_id IS NOT NULL",
            name="ck_forecast_publication_selection_warning_link",
        ),
    )
    op.create_table(
        "forecast_publication_decisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("station_id", UUID(as_uuid=True), nullable=False),
        sa.Column("parameter", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("selection_version", sa.Integer(), nullable=False),
        sa.Column("forecast_version", sa.Integer(), nullable=False),
        sa.Column("preservation_at_publish", sa.Text(), nullable=True),
        sa.Column("replaced_forecast_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "replaced_decision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("forecast_publication_decisions.id"),
            nullable=True,
        ),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "station_id", "parameter", "issued_at"],
            [
                "forecast_publication_selections.tenant_id",
                "forecast_publication_selections.station_id",
                "forecast_publication_selections.parameter",
                "forecast_publication_selections.issued_at",
            ],
            name="fk_forecast_publication_decision_selection",
        ),
        sa.ForeignKeyConstraint(
            ["forecast_id", "station_id", "parameter", "issued_at"],
            [
                "forecasts.id",
                "forecasts.station_id",
                "forecasts.parameter",
                "forecasts.issued_at",
            ],
            name="fk_forecast_publication_decision_exact_forecast",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_forecast_publication_decision_actor_tenant",
        ),
        sa.UniqueConstraint(
            "actor_user_id",
            "tenant_id",
            "action",
            "idempotency_key",
            name="uq_forecast_publication_decision_idempotency",
        ),
        sa.CheckConstraint(
            "action IN ('publish', 'withdraw')",
            name="ck_forecast_publication_decision_action",
        ),
        sa.CheckConstraint(
            "selection_version > 0 AND forecast_version > 0",
            name="ck_forecast_publication_decision_versions",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128 AND length(request_sha256) = 64",
            name="ck_forecast_publication_decision_request",
        ),
        sa.CheckConstraint(
            "(action = 'publish' AND preservation_at_publish IS NOT NULL "
            "AND preservation_at_publish "
            "IN ('verified', 'backup_pending') "
            "AND reason_code IS NULL AND reason_text IS NULL) OR "
            "(action = 'withdraw' AND preservation_at_publish IS NULL "
            "AND reason_code IS NOT NULL AND reason_text IS NOT NULL "
            "AND reason_code IN ('incorrect_forecast', 'data_error', 'other') "
            "AND length(trim(reason_text)) > 0)",
            name="ck_forecast_publication_decision_shape",
        ),
    )
    op.create_index(
        "ix_forecast_publication_decisions_forecast",
        "forecast_publication_decisions",
        ["forecast_id", "created_at"],
    )
    op.create_index(
        "uq_forecast_publication_decisions_withdrawn_forecast",
        "forecast_publication_decisions",
        ["forecast_id"],
        unique=True,
        postgresql_where=sa.text("action = 'withdraw'"),
    )
    op.create_table(
        "forecast_publication_sequence",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("next_value", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_forecast_publication_sequence_singleton"),
        sa.CheckConstraint(
            "next_value > 0", name="ck_forecast_publication_sequence_positive"
        ),
    )
    op.execute(
        "INSERT INTO forecast_publication_sequence (id, next_value) VALUES (1, 1)"
    )
    op.create_table(
        "forecast_publication_events",
        sa.Column("sequence", sa.BigInteger(), primary_key=True),
        sa.Column(
            "decision_id",
            UUID(as_uuid=True),
            sa.ForeignKey("forecast_publication_decisions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("forecast_id", UUID(as_uuid=True), nullable=False),
        sa.Column("replaced_forecast_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('published', 'replaced', 'withdrawn')",
            name="ck_forecast_publication_event_type",
        ),
    )
    op.execute("""
        CREATE FUNCTION reject_forecast_publication_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'forecast publication history is append-only: %', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in (
        "forecast_publication_decisions",
        "forecast_publication_events",
        "protected_backup_forecast_proofs",
    ):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_forecast_publication_mutation();
        """)
        op.execute(f"""
            CREATE TRIGGER trg_{table}_append_only_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_forecast_publication_mutation();
        """)
    op.execute("""
        CREATE FUNCTION protect_linked_forecast_warning() RETURNS trigger AS $$
        BEGIN
            IF OLD.linked_warning_publication_id IS NOT NULL
               AND NEW.selected_forecast_id IS DISTINCT FROM
                   OLD.selected_forecast_id THEN
                RAISE EXCEPTION 'linked warning requires a joint decision'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_forecast_publication_linked_warning
        BEFORE UPDATE ON forecast_publication_selections
        FOR EACH ROW EXECUTE FUNCTION protect_linked_forecast_warning();
    """)
    op.execute("""
        CREATE FUNCTION public.lock_publication_grants(
            p_actor uuid, p_tenant uuid, p_station uuid
        ) RETURNS boolean AS $$
        DECLARE
            active boolean;
            has_review boolean;
            has_publish boolean;
        BEGIN
            SELECT u.is_active INTO active
            FROM public.users AS u
            WHERE u.id = p_actor AND u.tenant_id = p_tenant
            FOR SHARE;
            IF NOT FOUND OR NOT active THEN
                RETURN false;
            END IF;
            PERFORM 1 FROM public.human_station_grants AS g
            WHERE g.user_id = p_actor AND g.tenant_id = p_tenant
              AND g.station_id = p_station AND g.permission = 'review'
            FOR SHARE;
            has_review := FOUND;
            PERFORM 1 FROM public.human_station_grants AS g
            WHERE g.user_id = p_actor AND g.tenant_id = p_tenant
              AND g.station_id = p_station AND g.permission = 'publish'
            FOR SHARE;
            has_publish := FOUND;
            RETURN has_review AND has_publish;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public;
    """)
    op.execute("""
        CREATE FUNCTION public.lock_publication_candidate(p_forecast uuid)
        RETURNS boolean AS $$
        BEGIN
            PERFORM 1 FROM public.forecasts WHERE id = p_forecast FOR UPDATE;
            RETURN FOUND;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public;
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.lock_publication_grants(uuid,uuid,uuid) "
        "FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.lock_publication_candidate(uuid) FROM PUBLIC"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION public.lock_publication_candidate(uuid)")
    op.execute("DROP FUNCTION public.lock_publication_grants(uuid,uuid,uuid)")
    op.execute(
        "DROP TRIGGER trg_forecast_publication_linked_warning "
        "ON forecast_publication_selections"
    )
    op.execute("DROP FUNCTION protect_linked_forecast_warning()")
    for table in (
        "protected_backup_forecast_proofs",
        "forecast_publication_events",
        "forecast_publication_decisions",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_append_only_truncate ON {table}")
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION reject_forecast_publication_mutation()")
    op.drop_table("forecast_publication_events")
    op.drop_table("forecast_publication_sequence")
    op.drop_index("uq_forecast_publication_decisions_withdrawn_forecast")
    op.drop_index("ix_forecast_publication_decisions_forecast")
    op.drop_table("forecast_publication_decisions")
    op.drop_table("forecast_publication_selections")
    op.drop_index("ix_protected_backup_forecast_proofs_forecast")
    op.drop_table("protected_backup_forecast_proofs")
    op.drop_table("protected_backup_health")
    op.drop_constraint(
        "uq_forecast_preservation_proof_identity",
        "forecast_preservation_attestations",
        type_="unique",
    )
    op.drop_constraint("uq_forecasts_publication_identity", "forecasts", type_="unique")
