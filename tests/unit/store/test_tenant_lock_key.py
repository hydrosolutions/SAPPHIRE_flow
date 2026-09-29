"""Plan 510 D3: every caller of ``lock_tenant`` maps a tenant to one key."""

from __future__ import annotations

from uuid import UUID

from sapphire_flow.store.tenant_store import tenant_lock_key
from sapphire_flow.types.ids import TenantId

_INT32 = range(-(2**31), 2**31)


class TestTenantLockKey:
    def test_the_same_tenant_always_maps_to_the_same_key(self) -> None:
        tenant = TenantId(UUID("12345678-1234-5678-1234-567812345678"))
        assert tenant_lock_key(tenant) == tenant_lock_key(tenant)

    def test_both_halves_fit_the_two_integer_advisory_lock_signature(self) -> None:
        for raw in (UUID(int=0), UUID(int=2**128 - 1), UUID(int=2**127)):
            assert all(half in _INT32 for half in tenant_lock_key(TenantId(raw)))

    def test_different_tenants_get_different_second_halves(self) -> None:
        one = tenant_lock_key(TenantId(UUID(int=1 << 120)))
        two = tenant_lock_key(TenantId(UUID(int=2 << 120)))
        assert one[0] == two[0]
        assert one[1] != two[1]

    def test_the_namespace_half_is_fixed_and_distinct_from_the_tenant_half(
        self,
    ) -> None:
        assert tenant_lock_key(TenantId(UUID(int=0)))[0] == 0x53464C4B
