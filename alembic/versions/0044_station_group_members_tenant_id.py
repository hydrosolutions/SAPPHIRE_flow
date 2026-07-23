"""station_group_members.tenant_id + composite tenant-match FKs (Plan 147
Slice A, step 4/4)

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-23

The structural, fail-closed invariant: a membership row's SINGLE
``tenant_id`` is bound by TWO composite FKs — ``(station_id, tenant_id) ->
stations(id, tenant_id)`` and ``(group_id, tenant_id) ->
station_groups(id, tenant_id)`` — so the DB forces
``station.tenant_id == group.tenant_id == member.tenant_id`` through every
writer (including raw SQL), with no trigger and no session variable.

GUARD (SELECT + raise, same migration transaction as the backfill — mirrors
the 0023/0033 precedent): before backfilling, detect any EXISTING member row
whose station and group already disagree on tenant (only possible if 0042/
0043 backfilled every station/group onto the SAME `sapphire` default, so on
a fresh chain this can never fire — it is a defensive guard against a
tampered/partial-migration intermediate state, and is what the migration
soundness test exercises). If any such row exists, the whole transaction
raises and rolls back — tenant identity is never silently coerced.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INCONSISTENT_MEMBERS_SQL = sa.text(
    """
    SELECT m.group_id, m.station_id, s.tenant_id AS station_tenant_id,
           g.tenant_id AS group_tenant_id
    FROM station_group_members m
    JOIN stations s ON s.id = m.station_id
    JOIN station_groups g ON g.id = m.group_id
    WHERE s.tenant_id <> g.tenant_id
    """
)


def upgrade() -> None:
    bind = op.get_bind()

    mismatched = bind.execute(_INCONSISTENT_MEMBERS_SQL).all()
    if mismatched:
        raise RuntimeError(
            "migration 0044: refusing to backfill "
            "station_group_members.tenant_id — "
            f"{len(mismatched)} membership row(s) have a station whose "
            "tenant_id disagrees with their group's tenant_id (e.g. "
            f"group_id={mismatched[0].group_id} "
            f"station_id={mismatched[0].station_id} "
            f"station_tenant_id={mismatched[0].station_tenant_id} "
            f"group_tenant_id={mismatched[0].group_tenant_id}). Reconcile "
            "these rows (move the station or the membership to agreeing "
            "tenants) before retrying; tenant identity is never silently "
            "coerced."
        )

    op.add_column(
        "station_group_members",
        sa.Column("tenant_id", PG_UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE station_group_members m
            SET tenant_id = g.tenant_id
            FROM station_groups g
            WHERE m.group_id = g.id
            """
        )
    )
    # The UPDATE above populated every membership row from its group's tenant,
    # so NOT NULL needs no server-side default. The persistent column carries
    # NO default: a future INSERT that omits tenant_id fails loud (the row's
    # single tenant_id is an explicit decision, structurally cross-checked by
    # the two composite FKs below), never silently coerced to Swiss.
    op.alter_column(
        "station_group_members",
        "tenant_id",
        nullable=False,
    )
    op.create_foreign_key(
        "fk_station_group_members_station_tenant",
        "station_group_members",
        "stations",
        ["station_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_foreign_key(
        "fk_station_group_members_group_tenant",
        "station_group_members",
        "station_groups",
        ["group_id", "tenant_id"],
        ["id", "tenant_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_station_group_members_group_tenant",
        "station_group_members",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_station_group_members_station_tenant",
        "station_group_members",
        type_="foreignkey",
    )
    op.drop_column("station_group_members", "tenant_id")
