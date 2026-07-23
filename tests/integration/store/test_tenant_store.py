from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE, DEFAULT_TENANT_ID, Tenant

if TYPE_CHECKING:
    import sqlalchemy as sa

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


class TestFetchSeededDefaultTenant:
    """Migration 0041 seeds the `sapphire` tenant at a fixed id — every
    session-scoped test DB carries it."""

    def test_fetch_by_code(self, db_connection: sa.Connection) -> None:
        store = PgTenantStore(db_connection)
        tenant = store.fetch_tenant_by_code(DEFAULT_TENANT_CODE)
        assert tenant is not None
        assert tenant.id == DEFAULT_TENANT_ID

    def test_fetch_by_id(self, db_connection: sa.Connection) -> None:
        store = PgTenantStore(db_connection)
        tenant = store.fetch_tenant(DEFAULT_TENANT_ID)
        assert tenant is not None
        assert tenant.code == DEFAULT_TENANT_CODE

    def test_appears_in_fetch_all(self, db_connection: sa.Connection) -> None:
        store = PgTenantStore(db_connection)
        codes = {t.code for t in store.fetch_all_tenants()}
        assert DEFAULT_TENANT_CODE in codes


class TestStoreAndFetchTenant:
    def test_round_trip(self, db_connection: sa.Connection) -> None:
        store = PgTenantStore(db_connection)
        tid = TenantId(uuid.uuid4())
        tenant = Tenant(id=tid, code="dhm", name="DHM (Nepal)", created_at=_NOW)

        returned_id = store.store_tenant(tenant)
        assert returned_id == tid

        fetched = store.fetch_tenant(tid)
        assert fetched is not None
        assert fetched.code == "dhm"
        assert fetched.name == "DHM (Nepal)"

    def test_fetch_missing_returns_none(self, db_connection: sa.Connection) -> None:
        store = PgTenantStore(db_connection)
        assert store.fetch_tenant(TenantId(uuid.uuid4())) is None

    def test_fetch_by_code_missing_returns_none(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgTenantStore(db_connection)
        assert store.fetch_tenant_by_code("no-such-tenant") is None
