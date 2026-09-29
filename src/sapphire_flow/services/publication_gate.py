"""Shared tenant predicate for publication reads and rejected-forecast review.

Plan 341 T3 supplies an empty active set in production. Plan 342 owns live
activation; tests may inject enrolled tenants to exercise gated behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.ids import TenantId


def publication_gate_active(
    tenant_id: TenantId, active_tenant_ids: frozenset[TenantId]
) -> bool:
    return tenant_id in active_tenant_ids
