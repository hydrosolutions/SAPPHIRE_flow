from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import AuditActorType, AuditEventType
    from sapphire_flow.types.ids import UserId


@dataclass(frozen=True, kw_only=True, slots=True)
class AuditEntry:
    """Plan 147 Slice B: the append-only `audit_log` row, per the
    authoritative contract (`docs/spec/types-and-protocols.md:1140-1149`,
    `docs/spec/database-schema.md:991-1000`). `actor_id` is `UserId | None`
    per the spec — `None` for `system`/config-declared-operator events
    (Slice E); a system row never uses a reserved sentinel UUID.
    """

    event_type: AuditEventType
    actor_id: UserId | None
    actor_type: AuditActorType
    target_type: str | None
    target_id: str | None
    detail: dict | None  # type: ignore[type-arg]
    ip_address: str | None
    created_at: UtcDatetime
