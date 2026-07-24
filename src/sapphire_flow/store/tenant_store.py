# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import sqlalchemy as sa

from sapphire_flow.db.metadata import tenants
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import Tenant


class PgTenantStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def fetch_tenant(self, tenant_id: TenantId) -> Tenant | None:
        row = (
            self._conn.execute(sa.select(tenants).where(tenants.c.id == tenant_id))
            .mappings()
            .one_or_none()
        )
        return _row_to_tenant(row) if row is not None else None

    def fetch_tenant_by_code(self, code: str) -> Tenant | None:
        row = (
            self._conn.execute(sa.select(tenants).where(tenants.c.code == code))
            .mappings()
            .one_or_none()
        )
        return _row_to_tenant(row) if row is not None else None

    def fetch_all_tenants(self) -> list[Tenant]:
        rows = self._conn.execute(sa.select(tenants)).mappings().all()
        return [_row_to_tenant(row) for row in rows]

    def store_tenant(self, tenant: Tenant) -> TenantId:
        self._conn.execute(
            sa.insert(tenants).values(
                id=tenant.id,
                code=tenant.code,
                name=tenant.name,
                created_at=tenant.created_at,
            )
        )
        return tenant.id


def _row_to_tenant(row: sa.engine.row.RowMapping) -> Tenant:
    return Tenant(
        id=TenantId(row["id"]),
        code=row["code"],
        name=row["name"],
        created_at=utc_from_row(row["created_at"]),
    )
