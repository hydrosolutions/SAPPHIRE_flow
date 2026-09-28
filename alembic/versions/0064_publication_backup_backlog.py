"""Expose pending publication proof backlog in protected backup health.

Revision ID: 0064
Revises: 0063
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "protected_backup_forecast_proofs",
        sa.Column("publication_decision_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_protected_proof_publication_decision",
        "protected_backup_forecast_proofs",
        "forecast_publication_decisions",
        ["publication_decision_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_protected_proof_decision_attestation",
        "protected_backup_forecast_proofs",
        ["publication_decision_id", "attestation_id"],
    )
    op.add_column(
        "protected_backup_health",
        sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "protected_backup_health",
        sa.Column("overdue_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "protected_backup_health",
        sa.Column("oldest_pending_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "protected_backup_health",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "protected_backup_health",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "protected_backup_health",
        sa.Column(
            "failed_forecasts",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_protected_backup_health_pending_count",
        "protected_backup_health",
        "pending_count >= 0",
    )
    op.create_check_constraint(
        "ck_protected_backup_health_overdue_count",
        "protected_backup_health",
        "overdue_count >= 0 AND overdue_count <= pending_count",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_protected_backup_health_overdue_count",
        "protected_backup_health",
        type_="check",
    )
    op.drop_constraint(
        "uq_protected_proof_decision_attestation",
        "protected_backup_forecast_proofs",
        type_="unique",
    )
    op.drop_constraint(
        "fk_protected_proof_publication_decision",
        "protected_backup_forecast_proofs",
        type_="foreignkey",
    )
    op.drop_column("protected_backup_forecast_proofs", "publication_decision_id")
    op.drop_constraint(
        "ck_protected_backup_health_pending_count",
        "protected_backup_health",
        type_="check",
    )
    for column in (
        "failed_forecasts",
        "next_retry_at",
        "last_attempt_at",
        "oldest_pending_at",
        "pending_count",
        "overdue_count",
    ):
        op.drop_column("protected_backup_health", column)
