# pyright: reportUnknownMemberType=false
"""Plan 147 Slice E (G3/G6/R5 LOCKED): tenant write-isolation.

Write authority comes ONLY from config + a validated run principal — NEVER
from the target row being written, and NEVER from a read-only access-token
(``types/auth.py::AccessToken`` is a GET-only HTTP credential, G4). This
module resolves a ``WritePrincipal`` from the ``[deployment]`` config block
(``config/deployment_identity.py``) and enforces it at each write chokepoint
(``services/training.py::promote_artifact``,
``services/model_onboarding.py::create_station_assignment`` /
``create_group_assignment``, ``services/onboarding.py::onboard_from_camelsch``,
and the scheduled ``flows/train_models.py::train_models_flow`` unit filter).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.services.tenant_boundary import resolve_tenant_code
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.ids import PrincipalId
from sapphire_flow.types.write_principal import WritePrincipal

if TYPE_CHECKING:
    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        StationGroupStore,
        StationStore,
        TenantStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import AuditEventType
    from sapphire_flow.types.ids import TenantId
    from sapphire_flow.types.training import TrainingUnit


def resolve_run_principal(
    tenant_store: TenantStore,
    identity_config: DeploymentIdentityConfig,
    *,
    tenant_code: str | None = None,
    operator: str | None = None,
) -> WritePrincipal:
    """Build the run's ``WritePrincipal`` from config + a validated run
    identity (G6). Serves BOTH surfaces:

    - **Interactive CLI**: an explicit ``tenant_code`` (``--tenant <code>``)
      is validated against ``identity_config.writable_tenants`` (bypassed
      for a ``global_admin`` host) and resolved to a ``TenantId``.
    - **Scheduled flows**: called with ``tenant_code=None`` — the run's
      tenant is resolved from config ALONE, never from a training unit.
      Absent an explicit code, a ``global_admin`` host is unscoped; a
      single-writable-tenant host binds that sole tenant; a host declaring
      MORE than one writable tenant with no explicit code is ambiguous and
      raises (one scheduled deployment per tenant — the plan's per-tenant-
      deployment rule).

    Every declared ``writable_tenants`` code is resolved against the
    ``tenants`` table UP FRONT, regardless of which branch is taken below —
    an unknown code is a hard error at principal-resolution time (G6),
    not a wait to see if that code is ever exercised.
    """
    for code in identity_config.writable_tenants:
        resolve_tenant_code(tenant_store, code)

    principal_id = (
        PrincipalId(operator)
        if operator is not None
        else (
            PrincipalId(identity_config.operator)
            if identity_config.operator is not None
            else None
        )
    )

    if tenant_code is not None:
        if not identity_config.global_admin and (
            tenant_code not in identity_config.writable_tenants
        ):
            raise ConfigurationError(
                f"tenant code {tenant_code!r} is not in this host's "
                f"writable_tenants {sorted(identity_config.writable_tenants)!r}"
            )
        tenant_id = resolve_tenant_code(tenant_store, tenant_code)
        return WritePrincipal(id=principal_id, tenant_id=tenant_id)

    if identity_config.global_admin:
        return WritePrincipal(id=principal_id, tenant_id=None)

    if len(identity_config.writable_tenants) == 1:
        (sole_code,) = identity_config.writable_tenants
        tenant_id = resolve_tenant_code(tenant_store, sole_code)
        return WritePrincipal(id=principal_id, tenant_id=tenant_id)

    raise ConfigurationError(
        "no tenant code supplied and this host's [deployment] config does "
        "not declare exactly one writable tenant (or global_admin=true); "
        f"writable_tenants={sorted(identity_config.writable_tenants)!r} — an "
        "explicit tenant code is required to disambiguate"
    )


def enforce_tenant_isolation(
    *,
    principal: WritePrincipal | None,
    target_tenant_id: TenantId,
    audit_log_store: AuditLogStore | None,
    event_type: AuditEventType,
    target_type: str,
    target_id: str,
    detail: dict[str, object] | None,
    now: UtcDatetime,
) -> None:
    """Reject a write whose target tenant does not match the principal's
    (R5/G6). No-op for an unscoped principal (``tenant_id=None`` — global-
    admin, or ``principal=None`` for a caller that supplied none — back-
    compat for tests/replay contexts with no DB-backed audit trail).

    On rejection: persists a ``system``-actor rejection event to
    ``audit_log`` (Slice B, the operator handle + tenant context recorded in
    ``detail`` per F3's minimal-conformant option — a config operator maps
    to no ``actor_id``) BEFORE raising, then raises ``TenantIsolationError``.
    Called BEFORE any domain mutation at every Slice-E chokepoint, so no
    rollback is required — the rejection is never persisted alongside a
    partial write.
    """
    if principal is None or principal.tenant_id is None:
        return
    if principal.tenant_id == target_tenant_id:
        return

    full_detail: dict[str, object] = {
        **(detail or {}),
        "operator": principal.id,
        "principal_tenant_id": str(principal.tenant_id),
        "target_tenant_id": str(target_tenant_id),
        "outcome": "rejected_tenant_mismatch",
    }
    if audit_log_store is not None:
        audit_log_store.append_entry(
            AuditEntry.system(
                event_type=event_type,
                target_type=target_type,
                target_id=target_id,
                detail=full_detail,
                ip_address=None,
                created_at=now,
            )
        )
    raise TenantIsolationError(
        f"principal (tenant={principal.tenant_id}) is not authorized to "
        f"write to tenant {target_tenant_id} ({target_type}={target_id})"
    )


def resolve_flow_run_principal(
    *,
    tenant_store: TenantStore | None,
    tenant_code: str | None,
    operator: str | None,
) -> WritePrincipal | None:
    """Shared flow-entrypoint wiring: build a ``WritePrincipal`` from
    ``SAPPHIRE_CONFIG``'s ``[deployment]`` block.

    Returns ``None`` — no enforcement, matching pre-Slice-E behavior — when
    either ``tenant_store`` or ``SAPPHIRE_CONFIG`` is unavailable (test/replay
    wiring with caller-injected stores and no DB). Every PRODUCTION flow
    entrypoint (``station_store is None`` bootstrap branch) has both, so
    production writes are always principal-enforced."""
    import os

    if tenant_store is None:
        return None
    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is None:
        return None

    from sapphire_flow.config.deployment_identity import (
        load_deployment_identity_config,
    )

    identity_config = load_deployment_identity_config(config_path)
    return resolve_run_principal(
        tenant_store,
        identity_config,
        tenant_code=tenant_code,
        operator=operator,
    )


def target_tenant_id_for_unit(
    unit: TrainingUnit,
    station_store: StationStore,
    group_store: StationGroupStore,
) -> TenantId | None:
    """Resolve a training unit's target tenant — the station's tenant for a
    station-scoped unit, the group's tenant for a group-scoped one. Returns
    ``None`` only if the target row cannot be found (deleted mid-run); such
    a unit is left for the existing not-found handling downstream, not
    treated as a tenant match."""
    if unit.station_id is not None:
        station = station_store.fetch_station(unit.station_id)
        return station.tenant_id if station is not None else None
    group = (
        group_store.fetch_group(unit.group_id) if unit.group_id is not None else None
    )
    return group.tenant_id if group is not None else None
