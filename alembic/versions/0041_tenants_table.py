"""tenants table + seed default tenant (Plan 147 Slice A)

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-23

Root of the v1.0 tenant model (Plan 147). One row per tenant — ``code`` is
the human/config handle (e.g. "sapphire", "dhm") resolved to a ``TenantId``
once at the config/CLI boundary. Seeds a default ``sapphire`` tenant at a
FIXED well-known id so migrations 0042-0044 can backfill every existing
Swiss station/group/member onto it deterministically, matching
``sapphire_flow.types.tenant.DEFAULT_TENANT_ID`` (kept in sync manually).
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in sync with sapphire_flow.types.tenant.DEFAULT_TENANT_ID / _CODE.
_DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
_DEFAULT_TENANT_CODE = "sapphire"

_tenants_dml = sa.table(
    "tenants",
    sa.column("id", PG_UUID(as_uuid=True)),
    sa.column("code", sa.Text),
    sa.column("name", sa.Text),
)


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.bulk_insert(
        _tenants_dml,
        [
            {
                "id": _DEFAULT_TENANT_ID,
                "code": _DEFAULT_TENANT_CODE,
                "name": "SAPPHIRE (Swiss v0)",
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("tenants")
