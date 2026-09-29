"""Plan 404 D4 — the single named predicate a REVIEW route consults to
decide whether Plan 341's tenant publication gate is active. Answers `False`
until Plan 341 provides its own tenant activation switch. Both landing
orders are covered (`404:111-118`, `341:84`): if Plan 341 lands first, this
plan's T3 wires this predicate to Plan 341's switch; if this plan lands
first, Plan 341's activation wires its switch to this predicate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.ids import TenantId


def publication_gate_active(tenant_id: TenantId) -> bool:
    del tenant_id  # unused until Plan 341's switch exists
    return False
