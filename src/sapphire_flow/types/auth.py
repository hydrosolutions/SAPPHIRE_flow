from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import AuditActorType

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import AuditEventType
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

    `__post_init__` enforces the actor_type/actor_id pairing so an invalid
    combination (e.g. `SYSTEM` with a non-null `actor_id`, or `API_KEY`/`USER`
    with `actor_id=None`) cannot be constructed — `NewType` alone cannot
    distinguish `UserId` from `AccessTokenId` at runtime, so this validates
    only the presence/absence half of the contract; prefer the `.system()` /
    `.user()` / `.api_key()` constructors below over calling `AuditEntry(...)`
    directly.
    """

    event_type: AuditEventType
    actor_id: UserId | AccessTokenId | None
    actor_type: AuditActorType
    target_type: str | None
    target_id: str | None
    detail: dict | None  # type: ignore[type-arg]
    ip_address: str | None
    created_at: UtcDatetime

    def __post_init__(self) -> None:
        if self.actor_type is AuditActorType.SYSTEM:
            if self.actor_id is not None:
                raise ValueError(
                    "AuditEntry: actor_type=SYSTEM requires actor_id=None "
                    f"(got {self.actor_id!r})"
                )
        elif self.actor_id is None:
            raise ValueError(
                f"AuditEntry: actor_type={self.actor_type.value} requires a "
                "non-null actor_id"
            )

    @classmethod
    def system(
        cls,
        *,
        event_type: AuditEventType,
        target_type: str | None,
        target_id: str | None,
        detail: dict | None,  # type: ignore[type-arg]
        ip_address: str | None,
        created_at: UtcDatetime,
    ) -> AuditEntry:
        return cls(
            event_type=event_type,
            actor_id=None,
            actor_type=AuditActorType.SYSTEM,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            ip_address=ip_address,
            created_at=created_at,
        )

    @classmethod
    def user(
        cls,
        *,
        actor_id: UserId,
        event_type: AuditEventType,
        target_type: str | None,
        target_id: str | None,
        detail: dict | None,  # type: ignore[type-arg]
        ip_address: str | None,
        created_at: UtcDatetime,
    ) -> AuditEntry:
        return cls(
            event_type=event_type,
            actor_id=actor_id,
            actor_type=AuditActorType.USER,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            ip_address=ip_address,
            created_at=created_at,
        )

    @classmethod
    def api_key(
        cls,
        *,
        actor_id: AccessTokenId,
        event_type: AuditEventType,
        target_type: str | None,
        target_id: str | None,
        detail: dict | None,  # type: ignore[type-arg]
        ip_address: str | None,
        created_at: UtcDatetime,
    ) -> AuditEntry:
        return cls(
            event_type=event_type,
            actor_id=actor_id,
            actor_type=AuditActorType.API_KEY,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            ip_address=ip_address,
            created_at=created_at,
        )
