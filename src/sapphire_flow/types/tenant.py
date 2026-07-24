from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sapphire_flow.types.ids import TenantId

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime

# Plan 147 Slice A: the default (Swiss v0) tenant, seeded by migration 0041 at
# this exact id — every pre-existing station/group/member backfills onto it.
# Kept in sync manually with `alembic/versions/0041_tenants_table.py`.
DEFAULT_TENANT_CODE = "sapphire"
DEFAULT_TENANT_ID: TenantId = TenantId(UUID("00000000-0000-0000-0000-000000000001"))


@dataclass(frozen=True, kw_only=True, slots=True)
class Tenant:
    id: TenantId
    code: str
    name: str
    created_at: UtcDatetime
