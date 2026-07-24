"""Plan 147 Slice C: CLI create/revoke + the `create-admin` bootstrap write
their access_tokens row and `API_KEY_CREATED`/`API_KEY_REVOKED` audit row in
ONE transaction (Slice B atomicity rule)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa

from sapphire_flow.cli.access_tokens import create_token, list_tokens, revoke_token
from sapphire_flow.db.metadata import audit_log
from sapphire_flow.store.access_token_store import PgAccessTokenStore
from sapphire_flow.types.auth import AccessToken
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.ids import AccessTokenId
from tests.conftest import make_station_config

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_EXPIRES = ensure_utc(_NOW + timedelta(days=30))
_PEPPER = "cli-test-pepper"


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
