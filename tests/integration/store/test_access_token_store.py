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
from sapphire_flow.types.enums import AccessTokenRole, ScopeMode, StationKind
from sapphire_flow.types.ids import AccessTokenId, StationId
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


def _set_scope_mode(
    conn: sa.Connection, token_id: AccessTokenId, mode: ScopeMode
) -> None:
    """Raw UPDATE — T6's `set-scope-mode` CLI verb is the supported way to
    do this in production, but T5 (the loader branch) is implemented and
    tested before T6 (the phase graph runs T5 ahead of T6/T1/T2), so these
    tests flip the column directly."""
    conn.execute(
        sa.update(access_tokens)
        .where(access_tokens.c.id == token_id)
        .values(scope_mode=mode.value)
    )


class TestTenantModeScopeResolution:
    """Plan 215 D2/T5: the `'tenant'`-mode loader branch derives
    `station_ids` from `stations.tenant_id` at load time instead of reading
    `access_token_stations` — the five D2 exit items, in order."""

    def test_tenant_mode_token_picks_up_station_created_after_token(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgAccessTokenStore(db_connection)
        token = _token(role=AccessTokenRole.CONSUMER, tenant_id=DEFAULT_TENANT_ID)
        store.create_token(token, station_ids=frozenset())
        _set_scope_mode(db_connection, token.id, ScopeMode.TENANT)

        # The station is created AFTER the token — a materialised
        # ('stations'-mode) scope would never see it without a fresh grant.
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)

        fetched = store.fetch_token(token.id)
        assert fetched is not None
        assert station.id in fetched.station_ids
        assert fetched.scope_mode is ScopeMode.TENANT

    def test_stations_mode_token_does_not_pick_up_new_station(
        self, db_connection: sa.Connection
    ) -> None:
        granted = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(granted)

        store = PgAccessTokenStore(db_connection)
        token = _token(role=AccessTokenRole.CONSUMER, tenant_id=DEFAULT_TENANT_ID)
        store.create_token(token, station_ids=frozenset({granted.id}))
        # Stays in default 'stations' mode — no _set_scope_mode call.

        later = make_station_config(
            tenant_id=DEFAULT_TENANT_ID,
            code="LATER-001",
            station_id=StationId(uuid4()),
        )
        PgStationStore(db_connection).store_station(later)

        fetched = store.fetch_token(token.id)
        assert fetched is not None
        assert fetched.station_ids == frozenset({granted.id})
        assert fetched.scope_mode is ScopeMode.STATIONS

    def test_cross_tenant_station_never_enters_tenant_mode_scope(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.store.tenant_store import PgTenantStore
        from sapphire_flow.types.ids import TenantId
        from sapphire_flow.types.tenant import Tenant

        other_tenant = Tenant(
            id=TenantId(uuid4()), code=f"x-{uuid4().hex[:6]}", name="X", created_at=_NOW
        )
        PgTenantStore(db_connection).store_tenant(other_tenant)
        foreign_station = make_station_config(tenant_id=other_tenant.id)
        PgStationStore(db_connection).store_station(foreign_station)
        own_station = make_station_config(
            tenant_id=DEFAULT_TENANT_ID,
            code="OWN-001",
            station_id=StationId(uuid4()),
        )
        PgStationStore(db_connection).store_station(own_station)

        store = PgAccessTokenStore(db_connection)
        token = _token(role=AccessTokenRole.CONSUMER, tenant_id=DEFAULT_TENANT_ID)
        store.create_token(token, station_ids=frozenset())
        _set_scope_mode(db_connection, token.id, ScopeMode.TENANT)

        fetched = store.fetch_token(token.id)
        assert fetched is not None
        assert own_station.id in fetched.station_ids
        assert foreign_station.id not in fetched.station_ids

    def test_non_bafu_non_river_station_enters_tenant_mode_scope(
        self, db_connection: sa.Connection
    ) -> None:
        """D2.2's deliberate widening, locked so it cannot silently change
        shape: tenant mode is unfiltered by `network`/`station_kind` — a
        `weather`-kind station on a non-`bafu` network, in the same tenant,
        IS in scope."""
        weather_station = make_station_config(
            tenant_id=DEFAULT_TENANT_ID,
            code="WX-001",
            network="not-bafu",
            station_kind=StationKind.WEATHER,
        )
        PgStationStore(db_connection).store_station(weather_station)

        store = PgAccessTokenStore(db_connection)
        token = _token(role=AccessTokenRole.CONSUMER, tenant_id=DEFAULT_TENANT_ID)
        store.create_token(token, station_ids=frozenset())
        _set_scope_mode(db_connection, token.id, ScopeMode.TENANT)

        fetched = store.fetch_token(token.id)
        assert fetched is not None
        assert weather_station.id in fetched.station_ids

    def test_db_rejects_tenant_scope_mode_on_admin_row(
        self, db_connection: sa.Connection
    ) -> None:
        store = PgAccessTokenStore(db_connection)
        token = _token(role=AccessTokenRole.ADMIN, tenant_id=None)
        store.create_token(token, station_ids=frozenset())

        with (
            pytest.raises(
                IntegrityError, match="ck_access_tokens_tenant_mode_is_consumer"
            ),
            db_connection.begin_nested(),
        ):
            _set_scope_mode(db_connection, token.id, ScopeMode.TENANT)
