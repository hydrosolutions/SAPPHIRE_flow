from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from sapphire_flow.types.auth import AccessToken, AuditEntry
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole, AuditActorType, AuditEventType
from sapphire_flow.types.ids import AccessTokenId, StationId, TenantId, UserId

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_EXPIRES = ensure_utc(datetime(2026, 2, 1, tzinfo=UTC))


def _access_token(**overrides: object) -> AccessToken:
    defaults: dict[str, object] = dict(
        id=AccessTokenId(uuid.uuid4()),
        token_hash="hash",
        key_prefix="pfx",
        name="test",
        role=AccessTokenRole.ADMIN,
        tenant_id=None,
        pepper_version=1,
        expires_at=_EXPIRES,
        disabled_at=None,
        created_at=_NOW,
        last_used_at=None,
        station_ids=frozenset(),
    )
    defaults.update(overrides)
    return AccessToken(**defaults)  # type: ignore[arg-type]


class TestAccessTokenRoleTenantInvariant:
    """G4 LOCKED (blocker, Slice C fixer round): `role=consumer` requires a
    non-null `tenant_id`; `role=admin` requires `tenant_id=None` — mirrored
    by a DB CHECK constraint (`ck_access_tokens_role_tenant`,
    alembic/versions/0047), so a tenantless consumer or tenant-bound admin
    is unrepresentable both here and at the DB layer."""

    def test_consumer_with_null_tenant_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role=consumer requires a non-null"):
            _access_token(role=AccessTokenRole.CONSUMER, tenant_id=None)

    def test_admin_with_non_null_tenant_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role=admin requires tenant_id=None"):
            _access_token(role=AccessTokenRole.ADMIN, tenant_id=TenantId(uuid.uuid4()))

    def test_consumer_with_tenant_id_constructs(self) -> None:
        token = _access_token(
            role=AccessTokenRole.CONSUMER,
            tenant_id=TenantId(uuid.uuid4()),
            station_ids=frozenset({StationId(uuid.uuid4())}),
        )
        assert token.role is AccessTokenRole.CONSUMER

    def test_admin_with_null_tenant_id_constructs(self) -> None:
        token = _access_token(role=AccessTokenRole.ADMIN, tenant_id=None)
        assert token.role is AccessTokenRole.ADMIN


class TestAuditEntryActorIdActorTypeInvariant:
    """The domain-level half of F4's actor-attribution invariant: an
    AuditEntry with an actor_type/actor_id combination that disagrees with
    the contract (`SYSTEM` ⇒ `actor_id=None`; `USER`/`API_KEY` ⇒
    `actor_id` present) must be REJECTED at construction, not written
    unchanged by the store."""

    def test_system_actor_with_non_null_actor_id_raises(self) -> None:
        with pytest.raises(ValueError, match="SYSTEM requires actor_id=None"):
            AuditEntry(
                event_type=AuditEventType.MODEL_PROMOTED,
                actor_id=UserId(uuid.uuid4()),
                actor_type=AuditActorType.SYSTEM,
                target_type=None,
                target_id=None,
                detail=None,
                ip_address=None,
                created_at=_NOW,
            )

    def test_api_key_actor_with_null_actor_id_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key requires a non-null actor_id"):
            AuditEntry(
                event_type=AuditEventType.API_KEY_CREATED,
                actor_id=None,
                actor_type=AuditActorType.API_KEY,
                target_type="access_token",
                target_id=None,
                detail=None,
                ip_address=None,
                created_at=_NOW,
            )

    def test_user_actor_with_null_actor_id_raises(self) -> None:
        with pytest.raises(ValueError, match="user requires a non-null actor_id"):
            AuditEntry(
                event_type=AuditEventType.USER_CREATED,
                actor_id=None,
                actor_type=AuditActorType.USER,
                target_type="user",
                target_id=None,
                detail=None,
                ip_address=None,
                created_at=_NOW,
            )

    def test_valid_system_entry_constructs(self) -> None:
        entry = AuditEntry(
            event_type=AuditEventType.MODEL_PROMOTED,
            actor_id=None,
            actor_type=AuditActorType.SYSTEM,
            target_type=None,
            target_id=None,
            detail=None,
            ip_address=None,
            created_at=_NOW,
        )
        assert entry.actor_id is None

    def test_valid_api_key_entry_constructs(self) -> None:
        token_id = AccessTokenId(uuid.uuid4())
        entry = AuditEntry(
            event_type=AuditEventType.API_KEY_CREATED,
            actor_id=token_id,
            actor_type=AuditActorType.API_KEY,
            target_type="access_token",
            target_id=None,
            detail=None,
            ip_address=None,
            created_at=_NOW,
        )
        assert entry.actor_id == token_id


class TestAuditEntryTypedConstructors:
    """The preferred discriminated-construction API — each constructor
    fixes actor_type and lets actor_id's shape follow from the call site."""

    def test_system_constructor_sets_null_actor_id(self) -> None:
        entry = AuditEntry.system(
            event_type=AuditEventType.MODEL_PROMOTED,
            target_type="model_artifact",
            target_id=str(uuid.uuid4()),
            detail=None,
            ip_address=None,
            created_at=_NOW,
        )
        assert entry.actor_type is AuditActorType.SYSTEM
        assert entry.actor_id is None

    def test_user_constructor_sets_actor_id(self) -> None:
        user_id = UserId(uuid.uuid4())
        entry = AuditEntry.user(
            actor_id=user_id,
            event_type=AuditEventType.USER_CREATED,
            target_type="user",
            target_id=None,
            detail=None,
            ip_address=None,
            created_at=_NOW,
        )
        assert entry.actor_type is AuditActorType.USER
        assert entry.actor_id == user_id

    def test_api_key_constructor_sets_actor_id(self) -> None:
        token_id = AccessTokenId(uuid.uuid4())
        entry = AuditEntry.api_key(
            actor_id=token_id,
            event_type=AuditEventType.API_KEY_CREATED,
            target_type="access_token",
            target_id=None,
            detail=None,
            ip_address=None,
            created_at=_NOW,
        )
        assert entry.actor_type is AuditActorType.API_KEY
        assert entry.actor_id == token_id
