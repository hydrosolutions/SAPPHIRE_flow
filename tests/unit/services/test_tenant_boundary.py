import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.tenant_boundary import resolve_tenant_code
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE, DEFAULT_TENANT_ID
from tests.fakes.fake_stores import FakeTenantStore


class TestResolveTenantCode:
    """Plan 147 Slice A: the parse-don't-validate config/CLI boundary — a raw
    tenant code string is resolved to a TenantId exactly once, here."""

    def test_resolves_a_known_code_to_its_tenant_id(self) -> None:
        store = FakeTenantStore()
        resolved = resolve_tenant_code(store, DEFAULT_TENANT_CODE)
        assert resolved == DEFAULT_TENANT_ID

    def test_rejects_an_unknown_code(self) -> None:
        store = FakeTenantStore()
        with pytest.raises(ConfigurationError, match="unknown tenant code"):
            resolve_tenant_code(store, "no-such-tenant")
