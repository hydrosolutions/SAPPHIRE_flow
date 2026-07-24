"""station_groups.tenant_id NOT NULL, per-tenant name uniqueness (Plan 147
Slice A, step 3/4)

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-23

Add-nullable -> backfill every existing group onto the default ``sapphire``
tenant -> replace the old GLOBAL ``UNIQUE (name)`` with ``UNIQUE (tenant_id,
name)`` -> NOT NULL -> add ``UNIQUE (id, tenant_id)`` (the composite-FK
target for migration 0044).

Downgrade restores the global ``UNIQUE (name)`` — this FAILS LOUDLY (a
Postgres duplicate-key error, aborting the migration transaction) if two
tenants hold a colliding group name, by design: the downgrade cannot honestly
re-establish global uniqueness in that case.
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in sync with sapphire_flow.types.tenant.DEFAULT_TENANT_ID.
_DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

# Postgres' implicit name for `sa.Column("name", ..., unique=True)` inline in
# the original CREATE TABLE (no explicit constraint name given there).
_LEGACY_NAME_UNIQUE = "station_groups_name_key"


def upgrade() -> None:
    op.add_column(
        "station_groups",
        sa.Column(
            "tenant_id",
            PG_UUID(as_uuid=True),
            nullable=True,
            # Backfill-only default (dropped at the end of upgrade()) — the
            # persistent column carries no default, so an INSERT omitting
            # tenant_id fails loud rather than silently defaulting to Swiss.
            server_default=sa.text(f"'{_DEFAULT_TENANT_ID}'"),
        ),
    )
    op.create_foreign_key(
        "fk_station_groups_tenant_id",
        "station_groups",
        "tenants",
        ["tenant_id"],
        ["id"],
    )
    op.execute(
        sa.text(
            "UPDATE station_groups SET tenant_id = :tid WHERE tenant_id IS NULL"
        ).bindparams(tid=_DEFAULT_TENANT_ID)
    )
    op.drop_constraint(_LEGACY_NAME_UNIQUE, "station_groups", type_="unique")
    op.create_unique_constraint(
        "uq_station_groups_tenant_id_name", "station_groups", ["tenant_id", "name"]
    )
    op.alter_column("station_groups", "tenant_id", nullable=False)
    # Drop the backfill default — future writers must name tenant_id explicitly.
    op.alter_column("station_groups", "tenant_id", server_default=None)
    op.create_unique_constraint(
        "uq_station_groups_id_tenant_id", "station_groups", ["id", "tenant_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_station_groups_id_tenant_id", "station_groups", type_="unique"
    )
    op.drop_constraint(
        "uq_station_groups_tenant_id_name", "station_groups", type_="unique"
    )
    # Fails loudly (duplicate key) if two tenants hold a colliding group
    # name — deliberate, see module docstring.
    op.create_unique_constraint(_LEGACY_NAME_UNIQUE, "station_groups", ["name"])
    op.drop_constraint(
        "fk_station_groups_tenant_id", "station_groups", type_="foreignkey"
    )
    op.drop_column("station_groups", "tenant_id")
