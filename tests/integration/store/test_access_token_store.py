from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from sapphire_flow.db.metadata import access_tokens
from sapphire_flow.store.access_token_store import (
    CrossTenantScopeError,
    PgAccessTokenStore,
)
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.auth import AccessToken
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.ids import AccessTokenId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import make_station_config

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_EXPIRES = ensure_utc(_NOW + timedelta(days=30))


def _token(**overrides: object) -> AccessToken:
    defaults: dict[str, object] = dict(
        id=AccessTokenId(uuid4()),
        token_hash=f"hash-{uuid4().hex}",
        key_prefix=f"pfx{uuid4().hex[:8]}",
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


class TestCreateAndFetchByKeyPrefix:
    def test_round_trips(self, db_connection: sa.Connection) -> None:
        store = PgAccessTokenStore(db_connection)
        token = _token()
        store.create_token(token, station_ids=frozenset())

        fetched = store.fetch_by_key_prefix(token.key_prefix)
        assert fetched is not None
        assert fetched.id == token.id
        assert fetched.token_hash == token.token_hash
        assert fetched.role is AccessTokenRole.ADMIN

    def test_unknown_prefix_returns_none(self, db_connection: sa.Connection) -> None:
        store = PgAccessTokenStore(db_connection)
        assert store.fetch_by_key_prefix("no-such-prefix") is None


class TestScopeMembershipValidation:
    def test_in_tenant_station_is_accepted(self, db_connection: sa.Connection) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)

        store = PgAccessTokenStore(db_connection)
        token = _token(role=AccessTokenRole.CONSUMER, tenant_id=DEFAULT_TENANT_ID)
        store.create_token(token, station_ids=frozenset({station.id}))

        fetched = store.fetch_token(token.id)
        assert fetched is not None
        assert fetched.station_ids == frozenset({station.id})

    def test_cross_tenant_station_is_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.store.tenant_store import PgTenantStore
        from sapphire_flow.types.ids import TenantId
        from sapphire_flow.types.tenant import Tenant

        other_tenant = Tenant(
            id=TenantId(uuid4()), code=f"x-{uuid4().hex[:6]}", name="X", created_at=_NOW
        )
        PgTenantStore(db_connection).store_tenant(other_tenant)
        station = make_station_config(tenant_id=other_tenant.id)
        PgStationStore(db_connection).store_station(station)

        store = PgAccessTokenStore(db_connection)
        token = _token(role=AccessTokenRole.CONSUMER, tenant_id=DEFAULT_TENANT_ID)
        with pytest.raises(CrossTenantScopeError):
            store.create_token(token, station_ids=frozenset({station.id}))

    def test_admin_token_cannot_carry_scope(self, db_connection: sa.Connection) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)

        store = PgAccessTokenStore(db_connection)
        token = _token(role=AccessTokenRole.ADMIN, tenant_id=None)
        with pytest.raises(
            ValueError, match="admin tokens cannot carry a station scope"
        ):
            store.create_token(token, station_ids=frozenset({station.id}))


class TestRevokeToken:
    def test_sets_disabled_at(self, db_connection: sa.Connection) -> None:
        store = PgAccessTokenStore(db_connection)
        token = _token()
        store.create_token(token, station_ids=frozenset())

        store.revoke_token(token.id, revoked_at=_NOW)

        fetched = store.fetch_token(token.id)
        assert fetched is not None
        assert fetched.disabled_at == _NOW


def _raw_insert_values(**overrides: object) -> dict[str, object]:
    """A raw `access_tokens` row dict that BYPASSES `AccessToken.__post_init__`
    — so these tests exercise the DB CHECK/UNIQUE constraints (alembic 0047 +
    metadata.py), not the Python dataclass guard."""
    values: dict[str, object] = dict(
        id=uuid4(),
        token_hash=f"hash-{uuid4().hex}",
        key_prefix=f"pfx{uuid4().hex[:8]}",
        name="raw-db-row",
        role=AccessTokenRole.ADMIN.value,
        tenant_id=None,
        pepper_version=1,
        expires_at=_EXPIRES,
        disabled_at=None,
        created_at=_NOW,
        last_used_at=None,
    )
    values.update(overrides)
    return values


class TestRoleTenantDbCheckConstraint:
    """BLOCKER/F7 (Slice C fixer round): the G4 role/tenant invariant is
    enforced at the DB layer by `ck_access_tokens_role_tenant`, NOT only by
    `AccessToken.__post_init__`. These insert raw rows (bypassing the
    dataclass) to prove the constraint fires even for a row written outside
    the Python domain type. Removing the CHECK from migration 0047 lets these
    bad rows insert (red-before proof)."""

    def test_db_rejects_consumer_without_tenant(
        self, db_connection: sa.Connection
    ) -> None:
        with (
            pytest.raises(IntegrityError, match="ck_access_tokens_role_tenant"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.insert(access_tokens).values(
                    **_raw_insert_values(
                        role=AccessTokenRole.CONSUMER.value, tenant_id=None
                    )
                )
            )

    def test_db_rejects_admin_with_tenant(self, db_connection: sa.Connection) -> None:
        with (
            pytest.raises(IntegrityError, match="ck_access_tokens_role_tenant"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.insert(access_tokens).values(
                    **_raw_insert_values(
                        role=AccessTokenRole.ADMIN.value,
                        tenant_id=DEFAULT_TENANT_ID,
                    )
                )
            )

    def test_db_accepts_consumer_with_tenant(
        self, db_connection: sa.Connection
    ) -> None:
        db_connection.execute(
            sa.insert(access_tokens).values(
                **_raw_insert_values(
                    role=AccessTokenRole.CONSUMER.value, tenant_id=DEFAULT_TENANT_ID
                )
            )
        )

    def test_db_accepts_admin_without_tenant(
        self, db_connection: sa.Connection
    ) -> None:
        db_connection.execute(
            sa.insert(access_tokens).values(
                **_raw_insert_values(role=AccessTokenRole.ADMIN.value, tenant_id=None)
            )
        )


class TestKeyPrefixUniqueConstraint:
    """MINOR (Slice C fixer round): `key_prefix` is DB-UNIQUE (migration 0047
    + metadata.py) so `fetch_by_key_prefix` can safely use `one_or_none()`
    without risking `MultipleResultsFound` (a 500 on the auth hot path). A
    second row with a duplicate prefix must be rejected by the DB."""

    def test_duplicate_key_prefix_is_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        shared_prefix = f"pfx{uuid4().hex[:8]}"
        db_connection.execute(
            sa.insert(access_tokens).values(
                **_raw_insert_values(key_prefix=shared_prefix)
            )
        )
        with (
            pytest.raises(IntegrityError, match="ix_access_tokens_key_prefix"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(
                sa.insert(access_tokens).values(
                    **_raw_insert_values(key_prefix=shared_prefix)
                )
            )
