from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import TenantStore
    from sapphire_flow.types.ids import TenantId


def resolve_tenant_code(tenant_store: TenantStore, code: str) -> TenantId:
    """Parse-don't-validate boundary (Plan 147 Slice A): a raw tenant CODE
    string — from `config.toml`'s ``[deployment]`` block or a ``--tenant``
    CLI arg — is resolved to a ``TenantId`` exactly once, here, against the
    ``tenants`` table. An unknown code is a hard startup error; no raw
    tenant-code string leaks past this boundary into domain code."""
    tenant = tenant_store.fetch_tenant_by_code(code)
    if tenant is None:
        raise ConfigurationError(f"unknown tenant code {code!r}")
    return tenant.id
