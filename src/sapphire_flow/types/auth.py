from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import AuditActorType, AuditEventType
    from sapphire_flow.types.ids import AccessTokenId, UserId


@dataclass(frozen=True, kw_only=True, slots=True)
class AuditEntry:
    """Plan 147 Slice B: the append-only `audit_log` row, per the
    authoritative contract (`docs/spec/types-and-protocols.md:1140-1149`,
    `docs/spec/database-schema.md:991-1000`). `actor_id` is
    `UserId | AccessTokenId | None` — `UserId` when `actor_type=USER`,
    `AccessTokenId` when `actor_type=API_KEY` (`access_tokens.id`, per the
    plan), `None` for `system`/config-declared-operator events (Slice E); a
    system row never uses a reserved sentinel UUID. Both ID NewTypes wrap
    `UUID` and the underlying `audit_log.actor_id` column is a plain UUID
    with no FK (append-only rows must survive token/user deletion) — the
    union here is a domain-level distinction only, not a storage one.
    """

    event_type: AuditEventType
    actor_id: UserId | AccessTokenId | None
    actor_type: AuditActorType
    target_type: str | None
    target_id: str | None
    detail: dict | None  # type: ignore[type-arg]
    ip_address: str | None
    created_at: UtcDatetime
