"""stations.tenant_id NOT NULL (Plan 147 Slice A, step 2/4)

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-23

Add-nullable -> backfill every existing station onto the default
``sapphire`` tenant (seeded by 0041) -> NOT NULL. Also adds
``UNIQUE (id, tenant_id)`` — redundant with the PK alone, but is the FK
target migration 0044's composite FK on ``station_group_members`` binds
tenant identity through (R4 LOCKED: station tenancy is canonical, not
derived from group membership).
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in sync with sapphire_flow.types.tenant.DEFAULT_TENANT_ID.
_DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


def upgrade() -> None:
    op.add_column(
        "stations",
        sa.Column(
            "tenant_id",
            PG_UUID(as_uuid=True),
            nullable=True,
            # A server-side DEFAULT so a legacy caller that INSERTs without
            # naming this column (mostly test seeding helpers) still lands
            # on the sapphire tenant, matching db/metadata.py.
            server_default=sa.text(f"'{_DEFAULT_TENANT_ID}'"),
        ),
    )
    op.create_foreign_key(
        "fk_stations_tenant_id", "stations", "tenants", ["tenant_id"], ["id"]
    )
    op.execute(
        sa.text(
            "UPDATE stations SET tenant_id = :tid WHERE tenant_id IS NULL"
        ).bindparams(tid=_DEFAULT_TENANT_ID)
    )
    op.alter_column("stations", "tenant_id", nullable=False)
    op.create_unique_constraint(
        "uq_stations_id_tenant_id", "stations", ["id", "tenant_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_stations_id_tenant_id", "stations", type_="unique")
    op.drop_constraint("fk_stations_tenant_id", "stations", type_="foreignkey")
    op.drop_column("stations", "tenant_id")
