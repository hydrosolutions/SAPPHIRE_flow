"""access_tokens + access_token_stations tables (Plan 147 Slice C)

Revision ID: 0047
Revises: 0046
Create Date: 2026-07-24

R1 LOCKED = HMAC-SHA-256 + a server-side pepper (`token_hash`), NOT bcrypt
(corrects `security.md:24` — see the doc-update gate). R2 LOCKED = a
normalized `access_token_stations` join, NOT JSONB, station-axis scope
only for v1.0. `tenant_id` is nullable — NULL denotes an unscoped
global-admin token; a set value FKs `tenants` (Slice A). No FK on
`token_id`→`access_tokens.id` cascade behaviour is DELETE-implicit here
(access_token_stations rows are scope, not an audit trail — safe to cascade
away with their parent token, unlike `audit_log.actor_id`).

G4 LOCKED role/tenant pairing (fixer round, post-review): a
`ck_access_tokens_role_tenant` CHECK constraint enforces
`role=admin -> tenant_id IS NULL` and `role=consumer -> tenant_id IS NOT
NULL` at the DB layer, mirroring `AccessToken.__post_init__` — this makes
"tenantless consumer" / "tenant-bound admin" structurally unrepresentable
even for rows written outside the dataclass. `key_prefix` is now UNIQUE
(was a plain index) — the fast pre-verification lookup key must never
collide; `PgAccessTokenStore.fetch_by_key_prefix` relies on `one_or_none()`
staying safe, and the CLI retries generation on the (near-impossible)
collision case (`cli/access_tokens.py`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_tokens",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.Text, nullable=False),
        sa.Column("key_prefix", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column(
            "tenant_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=True,
        ),
        sa.Column(
            "pepper_version", sa.SmallInteger, nullable=False, server_default="1"
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_access_tokens_token_hash", "access_tokens", ["token_hash"]
    )
    op.create_check_constraint(
        "ck_access_tokens_role",
        "access_tokens",
        "role IN ('consumer', 'admin')",
    )
    op.create_check_constraint(
        "ck_access_tokens_role_tenant",
        "access_tokens",
        "(role = 'admin' AND tenant_id IS NULL) OR "
        "(role = 'consumer' AND tenant_id IS NOT NULL)",
    )
    op.create_index(
        "ix_access_tokens_key_prefix", "access_tokens", ["key_prefix"], unique=True
    )
    op.create_index("ix_access_tokens_expires_at", "access_tokens", ["expires_at"])

    op.create_table(
        "access_token_stations",
        sa.Column(
            "token_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("access_tokens.id"),
            nullable=False,
        ),
        sa.Column(
            "station_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("stations.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("token_id", "station_id"),
    )


def downgrade() -> None:
    op.drop_table("access_token_stations")
    op.drop_index("ix_access_tokens_expires_at", table_name="access_tokens")
    op.drop_index("ix_access_tokens_key_prefix", table_name="access_tokens")
    op.drop_constraint("ck_access_tokens_role_tenant", "access_tokens", type_="check")
    op.drop_constraint("ck_access_tokens_role", "access_tokens", type_="check")
    op.drop_constraint("uq_access_tokens_token_hash", "access_tokens", type_="unique")
    op.drop_table("access_tokens")
