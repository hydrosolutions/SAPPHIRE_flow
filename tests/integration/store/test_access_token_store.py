from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

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

if TYPE_CHECKING:
    import sqlalchemy as sa

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
