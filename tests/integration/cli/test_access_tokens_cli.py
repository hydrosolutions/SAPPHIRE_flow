"""Plan 147 Slice C: CLI create/revoke + the `create-admin` bootstrap write
their access_tokens row and `API_KEY_CREATED`/`API_KEY_REVOKED` audit row in
ONE transaction (Slice B atomicity rule)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from sapphire_flow.cli.access_tokens import create_token, list_tokens, revoke_token
from sapphire_flow.db.metadata import audit_log
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole
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
