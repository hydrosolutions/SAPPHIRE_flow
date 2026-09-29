# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import struct

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sapphire_flow.db.metadata import tenants
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import Tenant

_TENANT_LOCK_NAMESPACE = 0x53464C4B
"""First half of the two-integer advisory key ("SFLK"); the second half is
derived from the tenant id, so this key space never meets the single-key
advisory locks other stores take."""


def tenant_lock_key(tenant_id: TenantId) -> tuple[int, int]:
    (second,) = struct.unpack(">i", tenant_id.bytes[:4])
    return _TENANT_LOCK_NAMESPACE, second


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

    def lock_tenant(self, tenant_id: TenantId) -> Tenant:
        """Serialise on the tenant with a transaction-scoped advisory lock.

        Every caller, whatever its role, takes the same key. A row lock
        (`FOR UPDATE`/`FOR SHARE`) would need UPDATE on `tenants`, which no
        routine role holds; advisory locks coordinate cooperating callers only.
        """
        tenant = self.fetch_tenant(tenant_id)
        if tenant is None:
            raise ValueError(f"tenant {tenant_id} is missing")
        namespace, key = tenant_lock_key(tenant_id)
        self._conn.execute(sa.select(sa.func.pg_advisory_xact_lock(namespace, key)))
        return tenant

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

    def ensure_tenant(self, *, tenant_id: TenantId, code: str, name: str) -> Tenant:
        self._conn.execute(
            pg_insert(tenants)
            .values(id=tenant_id, code=code, name=name)
            .on_conflict_do_nothing(index_elements=[tenants.c.code])
        )
        existing = self.fetch_tenant_by_code(code)
        if existing is None:
            raise ConfigurationError(f"tenant {code!r} is missing after insert-or-skip")
        if existing.name != name:
            raise ConfigurationError(
                f"tenant {code!r} already exists with name {existing.name!r}; "
                f"declared name is {name!r} (a rename is an owner action)"
            )
        return existing


def _row_to_tenant(row: sa.engine.row.RowMapping) -> Tenant:
    return Tenant(
        id=TenantId(row["id"]),
        code=row["code"],
        name=row["name"],
        created_at=utc_from_row(row["created_at"]),
    )
