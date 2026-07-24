from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import AccessTokenRole, AuditActorType

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import AuditEventType
    from sapphire_flow.types.ids import AccessTokenId, StationId, TenantId, UserId


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


@dataclass(frozen=True, kw_only=True, slots=True)
class AccessToken:
    """Plan 147 Slice C: the `access_tokens` row (R1/R2/R5 LOCKED).

    `token_hash` is the HMAC-SHA-256(pepper, raw_key) hex digest — never the
    raw key. `key_prefix` is the fast pre-verification lookup key.
    `tenant_id=None` denotes a global-admin token (unscoped). `station_ids`
    is the token's `access_token_stations` scope join — meaningful only for
    `CONSUMER`; empty means "sees nothing" (fail-closed, R2).

    G4 LOCKED role/tenant pairing, enforced in `__post_init__` (mirrored by
    a DB CHECK constraint, `alembic/versions/0047`, so the invariant holds
    even for rows written outside this dataclass): `role=consumer` REQUIRES
    a non-null `tenant_id` (every consumer token belongs to exactly one
    tenant — there is no such thing as a tenantless consumer); `role=admin`
    REQUIRES `tenant_id=None` (admin is always unscoped/global — a
    "tenant-bound admin" is not a representable state, since
    `Principal.is_admin` grants unrestricted global reads regardless of
    `tenant_id`).
    """

    id: AccessTokenId
    token_hash: str
    key_prefix: str
    name: str
    role: AccessTokenRole
    tenant_id: TenantId | None
    pepper_version: int
    expires_at: UtcDatetime
    disabled_at: UtcDatetime | None
    created_at: UtcDatetime
    last_used_at: UtcDatetime | None
    station_ids: frozenset[StationId]

    def __post_init__(self) -> None:
        if self.role is AccessTokenRole.CONSUMER and self.tenant_id is None:
            raise ValueError(
                "AccessToken: role=consumer requires a non-null tenant_id "
                "(G4 — every consumer token belongs to exactly one tenant)"
            )
        if self.role is AccessTokenRole.ADMIN and self.tenant_id is not None:
            raise ValueError(
                "AccessToken: role=admin requires tenant_id=None "
                "(G4 — admin is always unscoped/global, never tenant-bound)"
            )
