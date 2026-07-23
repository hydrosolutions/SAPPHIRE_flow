import uuid
from datetime import UTC, datetime

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE, DEFAULT_TENANT_ID, Tenant


class TestTenant:
    def test_round_trip_fields(self) -> None:
        tid = TenantId(uuid.uuid4())
        now = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
        tenant = Tenant(id=tid, code="dhm", name="DHM (Nepal)", created_at=now)
        assert tenant.id == tid
        assert tenant.code == "dhm"
        assert tenant.name == "DHM (Nepal)"
        assert tenant.created_at == now

    def test_frozen(self) -> None:
        tenant = Tenant(
            id=DEFAULT_TENANT_ID,
            code=DEFAULT_TENANT_CODE,
            name="SAPPHIRE (Swiss v0)",
            created_at=ensure_utc(datetime(2026, 1, 1, tzinfo=UTC)),
        )
        try:
            tenant.code = "mutated"  # type: ignore[misc]
        except AttributeError:
            pass
        else:
            raise AssertionError("Tenant must be frozen")


class TestDefaultTenantConstant:
    def test_default_tenant_id_is_a_fixed_well_known_uuid(self) -> None:
        # Kept in sync with alembic/versions/0041_tenants_table.py's seed row
        # — the migration inserts this EXACT id, not a random one.
        assert str(DEFAULT_TENANT_ID) == "00000000-0000-0000-0000-000000000001"

    def test_default_tenant_code_is_sapphire(self) -> None:
        assert DEFAULT_TENANT_CODE == "sapphire"
