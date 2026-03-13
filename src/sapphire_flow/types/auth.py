from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import AuditActorType, AuditEventType, UserRole
    from sapphire_flow.types.ids import AccessTokenId, StationId, UserId


class AccessTokenScope(NamedTuple):
    stations: list[StationId] | None
    parameters: list[str] | None
    boundary: dict | None  # type: ignore[type-arg]


class User(NamedTuple):
    id: UserId
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: UtcDatetime


class AccessToken(NamedTuple):
    id: AccessTokenId
    consumer_name: str
    scope: AccessTokenScope
    created_by: UserId
    created_at: UtcDatetime
    last_used_at: UtcDatetime | None
    revoked_at: UtcDatetime | None


class AuditEntry(NamedTuple):
    event_type: AuditEventType
    actor_id: UserId | None
    actor_type: AuditActorType
    target_type: str | None
    target_id: str | None
    detail: dict | None  # type: ignore[type-arg]
    ip_address: str | None
    created_at: UtcDatetime
