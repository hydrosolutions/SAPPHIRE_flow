"""Plan 147 Slice C: DB-backed end-to-end auth enforcement + scope
filtering. Seeds real `access_tokens`/`access_token_stations` rows (via
`PgAccessTokenStore`) and drives the FastAPI app with real bearer keys —
the same pepper the app resolves at lifespan startup
(`ACCESS_TOKEN_PEPPER`, set process-wide by tests/conftest.py)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.security import hash_token, load_access_token_pepper
from sapphire_flow.store.access_token_store import (
    CrossTenantScopeError,
    PgAccessTokenStore,
)
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AccessToken
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.ids import AccessTokenId, StationId, TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID, Tenant
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Generator

    import sqlalchemy as sa

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
# require_principal compares token.expires_at against the REAL wall clock
# (datetime.now(UTC)), not the fixture's fixed _NOW — expiry fixtures must
# be relative to real time, or a fixed-past _FUTURE would already be
# "expired" against actual now() and every request would spuriously 401.
_REAL_NOW = ensure_utc(datetime.now(UTC))
_FUTURE = ensure_utc(_REAL_NOW + timedelta(days=30))
_PAST = ensure_utc(_REAL_NOW - timedelta(days=1))


def _seed_station(conn: sa.Connection, *, seed: int, tenant_id: TenantId) -> StationId:
    station = make_station_config(
        code=f"ST-{seed}", rng=random.Random(seed), tenant_id=tenant_id
    )
    PgStationStore(conn).store_station(station)
    return station.id


def _make_token(
    conn: sa.Connection,
    *,
    role: AccessTokenRole,
    station_ids: frozenset[StationId] = frozenset(),
    tenant_id: TenantId | None = DEFAULT_TENANT_ID,
    expires_at: object = _FUTURE,
    disabled_at: object = None,
) -> str:
    """Insert a real access_tokens row and return the raw bearer key."""
    pepper = load_access_token_pepper()
    key_prefix = f"pfx{uuid4().hex[:8]}"
    raw_secret = uuid4().hex
    token = AccessToken(
        id=AccessTokenId(uuid4()),
        token_hash=hash_token(raw_secret, pepper=pepper),
        key_prefix=key_prefix,
        name="test-token",
        role=role,
        tenant_id=tenant_id,
        pepper_version=1,
        expires_at=expires_at,  # type: ignore[arg-type]
        disabled_at=disabled_at,  # type: ignore[arg-type]
        created_at=_NOW,
        last_used_at=None,
        station_ids=station_ids,
    )
    PgAccessTokenStore(conn).create_token(token, station_ids=station_ids)
    return f"{key_prefix}.{raw_secret}"


@pytest.fixture
def client(db_connection: sa.Connection) -> Generator[TestClient, None, None]:
    def _override_conn() -> Generator[sa.Connection, None, None]:
        yield db_connection

    app.dependency_overrides[get_connection] = _override_conn
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_connection, None)


def _auth(raw_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}"}


class TestConsumerStationScope:
    def test_scoped_station_is_visible(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, seed=1, tenant_id=DEFAULT_TENANT_ID)
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.CONSUMER, station_ids=frozenset({sid})
        )
        resp = client.get(f"/api/v1/stations/{sid}", headers=_auth(raw_key))
        assert resp.status_code == 200
        assert resp.json()["id"] == str(sid)

    def test_out_of_scope_station_is_404(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, seed=2, tenant_id=DEFAULT_TENANT_ID)
        other = _seed_station(db_connection, seed=3, tenant_id=DEFAULT_TENANT_ID)
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.CONSUMER, station_ids=frozenset({sid})
        )
        resp = client.get(f"/api/v1/stations/{other}", headers=_auth(raw_key))
        assert resp.status_code == 404

    def test_empty_scope_sees_nothing_in_list(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        _seed_station(db_connection, seed=4, tenant_id=DEFAULT_TENANT_ID)
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.CONSUMER, station_ids=frozenset()
        )
        resp = client.get("/api/v1/stations", headers=_auth(raw_key))
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_admin_sees_all_stations_unscoped(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        _seed_station(db_connection, seed=5, tenant_id=DEFAULT_TENANT_ID)
        _seed_station(db_connection, seed=6, tenant_id=DEFAULT_TENANT_ID)
        raw_key = _make_token(db_connection, role=AccessTokenRole.ADMIN, tenant_id=None)
        resp = client.get("/api/v1/stations", headers=_auth(raw_key))
        assert resp.status_code == 200
        assert resp.json()["total"] >= 2


class TestTokenLifecycleRejection:
    def test_expired_token_is_401(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.ADMIN, tenant_id=None, expires_at=_PAST
        )
        resp = client.get("/api/v1/stations", headers=_auth(raw_key))
        assert resp.status_code == 401

    def test_disabled_token_is_401(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        raw_key = _make_token(
            db_connection,
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            disabled_at=_NOW,
        )
        resp = client.get("/api/v1/stations", headers=_auth(raw_key))
        assert resp.status_code == 401

    def test_unknown_key_is_401(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/stations", headers=_auth("nonexistentprefix.badsecret")
        )
        assert resp.status_code == 401

    def test_tampered_secret_is_401(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        raw_key = _make_token(db_connection, role=AccessTokenRole.ADMIN, tenant_id=None)
        prefix, _, _secret = raw_key.partition(".")
        resp = client.get(
            "/api/v1/stations", headers=_auth(f"{prefix}.wrong-secret-value")
        )
        assert resp.status_code == 401


class TestGlobalModelSkillChartAdminOnly:
    def test_consumer_is_forbidden(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.CONSUMER, station_ids=frozenset()
        )
        resp = client.get(
            "/api/v1/models/some-model/skill-chart.json", headers=_auth(raw_key)
        )
        assert resp.status_code == 403

    def test_admin_is_allowed_through_auth_gate(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        raw_key = _make_token(db_connection, role=AccessTokenRole.ADMIN, tenant_id=None)
        resp = client.get(
            "/api/v1/models/some-model/skill-chart.json", headers=_auth(raw_key)
        )
        # Admin clears the auth gate — any further 404/200 is business logic,
        # not auth (a nonexistent model_id still resolves past auth).
        assert resp.status_code != 401
        assert resp.status_code != 403


class TestStationlessAlertsHiddenFromConsumer:
    def test_consumer_gets_empty_list_with_only_stationless_alerts(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.CONSUMER, station_ids=frozenset()
        )
        resp = client.get("/api/v1/alerts", headers=_auth(raw_key))
        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestCrossTenantScopeRejectedAtCreate:
    def test_station_outside_token_tenant_is_rejected(
        self, db_connection: sa.Connection
    ) -> None:
        other_tenant = Tenant(
            id=TenantId(uuid4()),
            code=f"other-{uuid4().hex[:6]}",
            name="Other",
            created_at=_NOW,
        )
        PgTenantStore(db_connection).store_tenant(other_tenant)
        sid = _seed_station(db_connection, seed=7, tenant_id=other_tenant.id)

        with pytest.raises(CrossTenantScopeError):
            _make_token(
                db_connection,
                role=AccessTokenRole.CONSUMER,
                tenant_id=DEFAULT_TENANT_ID,
                station_ids=frozenset({sid}),
            )
