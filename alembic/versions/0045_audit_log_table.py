"""audit_log table (Plan 147 Slice B, 1/2)

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-24

The append-only audit substrate every audited mutation (Slice C token
create/revoke, Slice E onboard/promote/assign) will write through.
Conforms EXACTLY to the authoritative contract
(`docs/spec/database-schema.md:991-1000`,
`docs/spec/types-and-protocols.md:1140-1149`) — no `tenant_id`/`action`/`at`
columns; tenant context + rejection outcome/reason live in `detail`. No FK
on `actor_id`: an append-only row must survive token revocation/deletion
(no cascade). Append-only itself is enforced by a SEPARATE role-independent
DB trigger, migration 0046 — this migration only creates the table +
indexes + the `actor_type` CHECK.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BIGINT, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("actor_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column(
            "actor_type",
            sa.Text,
            sa.CheckConstraint(
                "actor_type IN ('user', 'api_key', 'system')",
                name="ck_audit_log_actor_type",
            ),
            nullable=False,
        ),
        sa.Column("target_type", sa.Text, nullable=True),
        sa.Column("target_id", sa.Text, nullable=True),
        sa.Column("detail", JSONB, nullable=True),
        sa.Column("ip_address", INET, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index(
        "ix_audit_log_event_type_created_at",
        "audit_log",
        ["event_type", "created_at"],
    )
    op.create_index("ix_audit_log_target", "audit_log", ["target_type", "target_id"])
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_actor_id", table_name="audit_log")
    op.drop_index("ix_audit_log_target", table_name="audit_log")
    op.drop_index("ix_audit_log_event_type_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_table("audit_log")
