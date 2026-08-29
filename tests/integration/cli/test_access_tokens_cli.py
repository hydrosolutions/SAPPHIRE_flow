"""Plan 147 Slice C: CLI create/revoke + the `create-admin` bootstrap write
their access_tokens row and `API_KEY_CREATED`/`API_KEY_REVOKED` audit row in
ONE transaction (Slice B atomicity rule). Plan 215 T1/T2/T6 extend this to
`grant`/`revoke-station`/`show`/`set-scope-mode`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.cli.access_tokens import (
    create_token,
    grant_station,
    list_tokens,
    revoke_station,
    revoke_token,
    set_scope_mode,
    show_token,
)
from sapphire_flow.db.metadata import access_token_stations, audit_log
from sapphire_flow.store.access_token_store import PgAccessTokenStore
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.auth import AccessToken
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole, ScopeMode
from sapphire_flow.types.ids import AccessTokenId, StationId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import make_station_config

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_EXPIRES = ensure_utc(_NOW + timedelta(days=30))
_PEPPER = "cli-test-pepper"


def _create_consumer_token(
    conn: sa.Connection, *, name: str, station_ids: frozenset[StationId] = frozenset()
) -> AccessToken:
    create_token(
        conn,
        name=name,
        role=AccessTokenRole.CONSUMER,
        tenant_id=DEFAULT_TENANT_ID,
        tenant_code="sapphire",
        station_ids=station_ids,
        expires_at=_EXPIRES,
        now=_NOW,
        pepper=_PEPPER,
    )
    matches = [t for t in list_tokens(conn) if t.name == name]
    assert len(matches) == 1
    return matches[0]


def _create_admin_token(conn: sa.Connection, *, name: str) -> AccessToken:
    create_token(
        conn,
        name=name,
        role=AccessTokenRole.ADMIN,
        tenant_id=None,
        tenant_code=None,
        station_ids=frozenset(),
        expires_at=_EXPIRES,
        now=_NOW,
        pepper=_PEPPER,
    )
    matches = [t for t in list_tokens(conn) if t.name == name]
    assert len(matches) == 1
    return matches[0]


def _set_scope_mode_raw(
    conn: sa.Connection, token_id: AccessTokenId, mode: ScopeMode
) -> None:
    from sapphire_flow.db.metadata import access_tokens

    conn.execute(
        sa.update(access_tokens)
        .where(access_tokens.c.id == token_id)
        .values(scope_mode=mode.value)
    )


def _audit_rows_for(conn: sa.Connection, target_id: str) -> list[sa.engine.Row]:
    return (
        conn.execute(sa.select(audit_log).where(audit_log.c.target_id == target_id))
        .mappings()
        .all()
    )


class TestCreateAdminBootstrap:
    def test_writes_exactly_one_audit_row(self, db_connection: sa.Connection) -> None:
        raw_key = create_token(
            db_connection,
            name="bootstrap-admin",
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            tenant_code=None,
            station_ids=frozenset(),
            expires_at=_EXPIRES,
            now=_NOW,
            pepper=_PEPPER,
        )
        assert raw_key  # the raw key is returned exactly once

        tokens = list_tokens(db_connection)
        assert len(tokens) == 1
        token = tokens[0]
        assert token.role is AccessTokenRole.ADMIN

        rows = _audit_rows_for(db_connection, str(token.id))
        assert len(rows) == 1
        assert rows[0]["event_type"] == "api_key_created"
        assert rows[0]["actor_type"] == "system"
        assert rows[0]["actor_id"] is None


class TestCreateConsumerTokenWithScope:
    def test_creates_token_with_station_scope(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.store.station_store import PgStationStore
        from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)

        raw_key = create_token(
            db_connection,
            name="consumer-token",
            role=AccessTokenRole.CONSUMER,
            tenant_id=DEFAULT_TENANT_ID,
            tenant_code="sapphire",
            station_ids=frozenset({station.id}),
            expires_at=_EXPIRES,
            now=_NOW,
            pepper=_PEPPER,
        )
        assert raw_key

        tokens = list_tokens(db_connection)
        assert len(tokens) == 1
        assert tokens[0].station_ids == frozenset({station.id})


class TestKeyPrefixCollisionRetry:
    """Minor finding (Slice C fixer round): `key_prefix` is now DB-unique
    (alembic 0047) — a colliding generation attempt must retry with a fresh
    prefix, not surface a raw `IntegrityError` (or, before the fix, a
    `MultipleResultsFound` on the next lookup)."""

    def test_retries_on_colliding_prefix_and_still_writes_one_audit_row(
        self, db_connection: sa.Connection
    ) -> None:
        existing = AccessToken(
            id=AccessTokenId(uuid4()),
            token_hash=f"hash-{uuid4().hex}",
            key_prefix="colliding-prefix",
            name="pre-existing",
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            pepper_version=1,
            expires_at=_EXPIRES,
            disabled_at=None,
            created_at=_NOW,
            last_used_at=None,
            station_ids=frozenset(),
        )
        PgAccessTokenStore(db_connection).create_token(
            existing, station_ids=frozenset()
        )

        attempts: list[tuple[str, str, str]] = [
            ("colliding-prefix.first-secret", "colliding-prefix", "first-secret"),
            ("fresh-prefix.second-secret", "fresh-prefix", "second-secret"),
        ]
        calls = iter(attempts)

        raw_key = create_token(
            db_connection,
            name="new-token",
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            tenant_code=None,
            station_ids=frozenset(),
            expires_at=_EXPIRES,
            now=_NOW,
            pepper=_PEPPER,
            token_generator=lambda: next(calls),
        )
        assert raw_key == "fresh-prefix.second-secret"

        tokens = {t.name: t for t in list_tokens(db_connection)}
        assert set(tokens) == {"pre-existing", "new-token"}
        assert tokens["new-token"].key_prefix == "fresh-prefix"

        rows = _audit_rows_for(db_connection, str(tokens["new-token"].id))
        assert len(rows) == 1
        assert rows[0]["event_type"] == "api_key_created"


class TestRevokeToken:
    def test_revoke_disables_and_audits(self, db_connection: sa.Connection) -> None:
        create_token(
            db_connection,
            name="to-revoke",
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            tenant_code=None,
            station_ids=frozenset(),
            expires_at=_EXPIRES,
            now=_NOW,
            pepper=_PEPPER,
        )
        token = list_tokens(db_connection)[0]

        revoke_token(db_connection, token_id=token.id, now=_NOW)

        revoked = list_tokens(db_connection)[0]
        assert revoked.disabled_at is not None

        rows = _audit_rows_for(db_connection, str(token.id))
        event_types = {r["event_type"] for r in rows}
        assert event_types == {"api_key_created", "api_key_revoked"}


class TestGrantAndRevokeStation:
    """Plan 215 T1."""

    def test_grant_makes_station_effectively_in_scope(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(db_connection, name="grant-target")

        summary = grant_station(
            db_connection, token_id=token.id, station_id=station.id, now=_NOW
        )
        assert str(station.id) in summary

        reloaded = PgAccessTokenStore(db_connection).fetch_token(token.id)
        assert reloaded is not None
        assert station.id in reloaded.station_ids

    def test_repeat_grant_is_idempotent(self, db_connection: sa.Connection) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(db_connection, name="grant-twice")

        grant_station(db_connection, token_id=token.id, station_id=station.id, now=_NOW)
        grant_station(db_connection, token_id=token.id, station_id=station.id, now=_NOW)

        reloaded = PgAccessTokenStore(db_connection).fetch_token(token.id)
        assert reloaded is not None
        assert reloaded.station_ids == frozenset({station.id})

    def test_revoke_station_removes_effective_access(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(
            db_connection,
            name="revoke-target",
            station_ids=frozenset({station.id}),
        )

        summary = revoke_station(
            db_connection, token_id=token.id, station_id=station.id, now=_NOW
        )
        assert str(station.id) in summary

        reloaded = PgAccessTokenStore(db_connection).fetch_token(token.id)
        assert reloaded is not None
        assert station.id not in reloaded.station_ids

    def test_repeat_revoke_station_is_harmless(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(
            db_connection,
            name="revoke-twice",
            station_ids=frozenset({station.id}),
        )

        revoke_station(
            db_connection, token_id=token.id, station_id=station.id, now=_NOW
        )
        second = revoke_station(
            db_connection, token_id=token.id, station_id=station.id, now=_NOW
        )
        assert "not in scope" in second

    def test_cross_tenant_grant_is_rejected(self, db_connection: sa.Connection) -> None:
        from sapphire_flow.store.access_token_store import CrossTenantScopeError
        from sapphire_flow.store.tenant_store import PgTenantStore
        from sapphire_flow.types.ids import TenantId
        from sapphire_flow.types.tenant import Tenant

        other_tenant = Tenant(
            id=TenantId(uuid4()), code=f"x-{uuid4().hex[:6]}", name="X", created_at=_NOW
        )
        PgTenantStore(db_connection).store_tenant(other_tenant)
        foreign_station = make_station_config(tenant_id=other_tenant.id)
        PgStationStore(db_connection).store_station(foreign_station)
        token = _create_consumer_token(db_connection, name="cross-tenant-grant")

        with pytest.raises(CrossTenantScopeError):
            grant_station(
                db_connection,
                token_id=token.id,
                station_id=foreign_station.id,
                now=_NOW,
            )

    def test_grant_unknown_station_id_is_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.store.access_token_store import CrossTenantScopeError

        token = _create_consumer_token(db_connection, name="unknown-station-grant")
        with pytest.raises(CrossTenantScopeError, match="unknown station ids"):
            grant_station(
                db_connection,
                token_id=token.id,
                station_id=StationId(uuid4()),
                now=_NOW,
            )

    def test_grant_refuses_admin_token(self, db_connection: sa.Connection) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        admin = _create_admin_token(db_connection, name="admin-grant-refused")

        with pytest.raises(SystemExit):
            grant_station(
                db_connection, token_id=admin.id, station_id=station.id, now=_NOW
            )

    def test_revoke_station_refuses_admin_token(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        admin = _create_admin_token(db_connection, name="admin-revoke-refused")

        with pytest.raises(SystemExit):
            revoke_station(
                db_connection, token_id=admin.id, station_id=station.id, now=_NOW
            )

    def test_grant_refuses_tenant_mode_token(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(db_connection, name="tenant-mode-grant-refused")
        _set_scope_mode_raw(db_connection, token.id, ScopeMode.TENANT)

        with pytest.raises(SystemExit):
            grant_station(
                db_connection, token_id=token.id, station_id=station.id, now=_NOW
            )

    def test_revoke_station_refuses_tenant_mode_token(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(db_connection, name="tenant-mode-revoke-refused")
        _set_scope_mode_raw(db_connection, token.id, ScopeMode.TENANT)

        with pytest.raises(SystemExit):
            revoke_station(
                db_connection, token_id=token.id, station_id=station.id, now=_NOW
            )

    def test_grant_and_revoke_each_write_exactly_one_audit_row(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(db_connection, name="audit-count")

        grant_station(db_connection, token_id=token.id, station_id=station.id, now=_NOW)
        revoke_station(
            db_connection, token_id=token.id, station_id=station.id, now=_NOW
        )

        rows = _audit_rows_for(db_connection, str(token.id))
        scope_changed = [r for r in rows if r["event_type"] == "api_key_scope_changed"]
        assert len(scope_changed) == 2

    def test_grant_output_never_contains_token_hash(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(db_connection, name="no-secret-in-grant-output")

        summary = grant_station(
            db_connection, token_id=token.id, station_id=station.id, now=_NOW
        )
        assert token.token_hash not in summary


class TestShowToken:
    """Plan 215 T2."""

    def test_prints_station_uuid_and_network_code_in_stations_mode(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(
            db_connection,
            name="show-stations-mode",
            station_ids=frozenset({station.id}),
        )

        output = show_token(db_connection, token_id=token.id)
        assert str(station.id) in output
        assert f"{station.network}/{station.code}" in output
        assert "stations" in output

    def test_prints_derived_label_in_tenant_mode(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(db_connection, name="show-tenant-mode")
        _set_scope_mode_raw(db_connection, token.id, ScopeMode.TENANT)

        output = show_token(db_connection, token_id=token.id)
        assert str(station.id) in output
        assert "DERIVED" in output

    def test_rejects_unknown_token_id(self, db_connection: sa.Connection) -> None:
        with pytest.raises(SystemExit):
            show_token(db_connection, token_id=AccessTokenId(uuid4()))

    def test_never_prints_token_hash(self, db_connection: sa.Connection) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(
            db_connection,
            name="no-secret-in-show-output",
            station_ids=frozenset({station.id}),
        )

        output = show_token(db_connection, token_id=token.id)
        assert token.token_hash not in output


class TestSetScopeMode:
    """Plan 215 T6."""

    def test_stations_to_tenant_sets_mode_and_deletes_grant_rows(
        self, db_connection: sa.Connection
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(
            db_connection,
            name="switch-to-tenant",
            station_ids=frozenset({station.id}),
        )

        set_scope_mode(
            db_connection,
            token_id=token.id,
            target_mode=ScopeMode.TENANT,
            now=_NOW,
            confirmed=True,
        )

        reloaded = PgAccessTokenStore(db_connection).fetch_token(token.id)
        assert reloaded is not None
        assert reloaded.scope_mode is ScopeMode.TENANT

        remaining = db_connection.execute(
            sa.select(access_token_stations.c.station_id).where(
                access_token_stations.c.token_id == token.id
            )
        ).all()
        assert remaining == []

    def test_tenant_to_stations_leaves_empty_scope_and_says_so(
        self, db_connection: sa.Connection
    ) -> None:
        token = _create_consumer_token(db_connection, name="switch-to-stations")
        _set_scope_mode_raw(db_connection, token.id, ScopeMode.TENANT)

        summary = set_scope_mode(
            db_connection,
            token_id=token.id,
            target_mode=ScopeMode.STATIONS,
            now=_NOW,
            confirmed=False,
        )
        assert "EMPTY" in summary

        reloaded = PgAccessTokenStore(db_connection).fetch_token(token.id)
        assert reloaded is not None
        assert reloaded.scope_mode is ScopeMode.STATIONS
        assert reloaded.station_ids == frozenset()

    def test_confirmation_flag_is_mandatory_for_tenant_mode(
        self, db_connection: sa.Connection
    ) -> None:
        token = _create_consumer_token(db_connection, name="unconfirmed-tenant-switch")

        with pytest.raises(SystemExit):
            set_scope_mode(
                db_connection,
                token_id=token.id,
                target_mode=ScopeMode.TENANT,
                now=_NOW,
                confirmed=False,
            )

        reloaded = PgAccessTokenStore(db_connection).fetch_token(token.id)
        assert reloaded is not None
        assert reloaded.scope_mode is ScopeMode.STATIONS

    def test_refuses_admin_token(self, db_connection: sa.Connection) -> None:
        admin = _create_admin_token(db_connection, name="admin-scope-mode-refused")

        with pytest.raises(SystemExit):
            set_scope_mode(
                db_connection,
                token_id=admin.id,
                target_mode=ScopeMode.TENANT,
                now=_NOW,
                confirmed=True,
            )

    def test_refuses_unknown_token_id(self, db_connection: sa.Connection) -> None:
        with pytest.raises(SystemExit):
            set_scope_mode(
                db_connection,
                token_id=AccessTokenId(uuid4()),
                target_mode=ScopeMode.TENANT,
                now=_NOW,
                confirmed=True,
            )

    def test_writes_exactly_one_audit_row_per_successful_change(
        self, db_connection: sa.Connection
    ) -> None:
        token = _create_consumer_token(db_connection, name="scope-mode-audit-count")

        set_scope_mode(
            db_connection,
            token_id=token.id,
            target_mode=ScopeMode.TENANT,
            now=_NOW,
            confirmed=True,
        )

        rows = _audit_rows_for(db_connection, str(token.id))
        scope_changed = [r for r in rows if r["event_type"] == "api_key_scope_changed"]
        assert len(scope_changed) == 1

    def test_output_never_contains_token_hash(
        self, db_connection: sa.Connection
    ) -> None:
        token = _create_consumer_token(db_connection, name="no-secret-in-scope-output")

        summary = set_scope_mode(
            db_connection,
            token_id=token.id,
            target_mode=ScopeMode.TENANT,
            now=_NOW,
            confirmed=True,
        )
        assert token.token_hash not in summary

    def test_audit_failure_rolls_back_the_whole_change(
        self, db_connection: sa.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        station = make_station_config(tenant_id=DEFAULT_TENANT_ID)
        PgStationStore(db_connection).store_station(station)
        token = _create_consumer_token(
            db_connection,
            name="atomic-scope-mode",
            station_ids=frozenset({station.id}),
        )

        def _raise_append(self: object, entry: object) -> None:  # noqa: ARG001
            raise RuntimeError("audit boom")

        monkeypatch.setattr(PgAuditLogStore, "append_entry", _raise_append)

        with (
            pytest.raises(RuntimeError, match="audit boom"),
            db_connection.begin_nested(),
        ):
            set_scope_mode(
                db_connection,
                token_id=token.id,
                target_mode=ScopeMode.TENANT,
                now=_NOW,
                confirmed=True,
            )

        # The SAVEPOINT rolled back with the failed audit insert — mode
        # unchanged, grant rows intact.
        reloaded = PgAccessTokenStore(db_connection).fetch_token(token.id)
        assert reloaded is not None
        assert reloaded.scope_mode is ScopeMode.STATIONS
        assert reloaded.station_ids == frozenset({station.id})
