from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.ids import PrincipalId, TenantId


@dataclass(frozen=True, kw_only=True, slots=True)
class WritePrincipal:
    """Plan 147 Slice E (R5/G6 LOCKED): the tenant write-isolation
    principal. A THIRD principal kind — distinct from the two HTTP read
    roles (``AccessTokenRole.CONSUMER``/``ADMIN``) and NEVER materialized
    from an ``access_tokens`` row (those are GET-only, G4) or from the
    target row being written.

    Built ONLY from config + a validated run identity
    (``services/write_principal.py::resolve_run_principal``): the
    ``[deployment]`` block's ``writable_tenants``/``global_admin`` +
    optional ``operator``, plus an interactive ``--tenant``/``--operator``
    override or the scheduled flow's single config-selected tenant.

    ``tenant_id=None`` denotes an unscoped/global-admin principal (may
    write to any tenant); a set ``tenant_id`` binds every write to that one
    tenant (``services/write_principal.py::enforce_tenant_isolation``
    rejects a mismatched target).
    """

    id: PrincipalId | None
    tenant_id: TenantId | None
