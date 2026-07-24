"""Plan 147 Slice C: DB-backed end-to-end auth enforcement + scope
filtering. Seeds real `access_tokens`/`access_token_stations` rows (via
`PgAccessTokenStore`) and drives the FastAPI app with real bearer keys —
the same pepper the app resolves at lifespan startup
(`ACCESS_TOKEN_PEPPER`, set process-wide by tests/conftest.py)."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_connection, get_connection_rw
from sapphire_flow.api.security import hash_token, load_access_token_pepper
from sapphire_flow.store.access_token_store import (
    CrossTenantScopeError,
    PgAccessTokenStore,
)
from sapphire_flow.store.alert_store import PgAlertStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.alert import Alert
from sapphire_flow.types.auth import AccessToken
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole, AlertStatus
from sapphire_flow.types.ids import AccessTokenId, AlertId, StationId, TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID, Tenant
from tests.conftest import make_alert, make_station_config

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

    # Both get_connection (RO reads) and get_connection_rw (the
    # last_used_at write in require_principal) resolve to the SAME
    # rollback-isolated db_connection — a genuinely separate connection
    # would open its own transaction and never see this test's
    # not-yet-committed token/station rows.
    app.dependency_overrides[get_connection] = _override_conn
    app.dependency_overrides[get_connection_rw] = _override_conn
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_connection, None)
    app.dependency_overrides.pop(get_connection_rw, None)


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
    """F7 LOCKED: a consumer's GET /alerts excludes both out-of-scope-station
    and null-station alerts; admin's includes everything. Seeds real rows
    via PgAlertStore.upsert_alert (not an empty-scope no-op) — the
    filtering logic in api_alerts.py/alert_store.py is actually exercised."""

    def test_consumer_gets_empty_list_with_only_stationless_alerts(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.CONSUMER, station_ids=frozenset()
        )
        resp = client.get("/api/v1/alerts", headers=_auth(raw_key))
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_consumer_sees_only_in_scope_station_alert(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        in_scope = _seed_station(db_connection, seed=20, tenant_id=DEFAULT_TENANT_ID)
        out_of_scope = _seed_station(
            db_connection, seed=21, tenant_id=DEFAULT_TENANT_ID
        )

        in_scope_alert = make_alert(station_id=in_scope, rng=random.Random(20))
        out_of_scope_alert = make_alert(station_id=out_of_scope, rng=random.Random(21))
        stationless_alert = Alert(
            id=AlertId(uuid4()),
            station_id=None,
            source=in_scope_alert.source,
            alert_level="Moderate",
            status=AlertStatus.RAISED,
            trigger_probability=0.6,
            trigger_value=150.0,
            triggered_at=in_scope_alert.triggered_at,
            acknowledged_at=None,
            acknowledged_by=None,
            resolved_at=None,
            first_detected_at=None,
            notified_at=None,
            created_at=in_scope_alert.created_at,
        )

        alert_store = PgAlertStore(db_connection)
        alert_store.upsert_alert(in_scope_alert)
        alert_store.upsert_alert(out_of_scope_alert)
        alert_store.upsert_alert(stationless_alert)

        consumer_key = _make_token(
            db_connection,
            role=AccessTokenRole.CONSUMER,
            station_ids=frozenset({in_scope}),
        )
        resp = client.get("/api/v1/alerts", headers=_auth(consumer_key))
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert [item["id"] for item in body["items"]] == [str(in_scope_alert.id)]

        admin_key = _make_token(
            db_connection, role=AccessTokenRole.ADMIN, tenant_id=None
        )
        admin_resp = client.get("/api/v1/alerts", headers=_auth(admin_key))
        assert admin_resp.status_code == 200
        admin_body = admin_resp.json()
        assert admin_body["total"] == 3
        assert {item["id"] for item in admin_body["items"]} == {
            str(in_scope_alert.id),
            str(out_of_scope_alert.id),
            str(stationless_alert.id),
        }


class TestConsumerAlertPaginationAppliesScopeBeforeLimitOffset:
    """Major finding (Slice C fixer round): consumer alert filtering used to
    happen AFTER the store applied LIMIT/OFFSET and computed an unscoped
    total — a consumer could see short/empty pages despite later in-scope
    alerts, plus a wrong total. Interleave in-scope/out-of-scope alerts
    across the sort order so a naive post-filter-after-pagination bug would
    surface as a wrong `total` or a page missing an in-scope alert."""

    def test_mixed_scope_alerts_paginate_correctly_for_consumer(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        in_scope = _seed_station(db_connection, seed=30, tenant_id=DEFAULT_TENANT_ID)
        out_of_scope = _seed_station(
            db_connection, seed=31, tenant_id=DEFAULT_TENANT_ID
        )
        alert_store = PgAlertStore(db_connection)

        # 5 alerts total, newest-first by triggered_at (the store's sort
        # order): in, out, in, out, in — 3 in-scope, 2 out-of-scope.
        expected_in_scope_ids: list[str] = []
        for i in range(5):
            sid = in_scope if i % 2 == 0 else out_of_scope
            triggered = ensure_utc(datetime(2025, 6, 1, i, tzinfo=UTC))
            alert = make_alert(
                station_id=sid, alert_level=f"Level-{i}", rng=random.Random(30 + i)
            )
            alert = replace(alert, triggered_at=triggered)
            alert_store.upsert_alert(alert)
            if sid == in_scope:
                expected_in_scope_ids.append(str(alert.id))
        # Newest-first: i=4,2,0 (in-scope only, matching the store's sort).
        expected_in_scope_ids.reverse()

        consumer_key = _make_token(
            db_connection,
            role=AccessTokenRole.CONSUMER,
            station_ids=frozenset({in_scope}),
        )

        page1 = client.get(
            "/api/v1/alerts",
            params={"limit": 2, "offset": 0},
            headers=_auth(consumer_key),
        ).json()
        page2 = client.get(
            "/api/v1/alerts",
            params={"limit": 2, "offset": 2},
            headers=_auth(consumer_key),
        ).json()

        assert page1["total"] == 3
        assert page2["total"] == 3
        assert [item["id"] for item in page1["items"]] == expected_in_scope_ids[:2]
        assert [item["id"] for item in page2["items"]] == expected_in_scope_ids[2:]
        # No out-of-scope id ever leaks, and no in-scope id is skipped.
        all_seen = [item["id"] for item in page1["items"] + page2["items"]]
        assert sorted(all_seen) == sorted(expected_in_scope_ids)


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


class TestLastUsedAtUpdatedOnAuthentication:
    """Major finding (Slice C fixer round): `last_used_at` must be updated
    on every SUCCESSFUL authentication (security.md's documented contract,
    used for inactive-key monitoring) and left untouched when auth is
    rejected."""

    def test_successful_request_updates_last_used_at(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        raw_key = _make_token(db_connection, role=AccessTokenRole.ADMIN, tenant_id=None)
        prefix = raw_key.split(".")[0]
        store = PgAccessTokenStore(db_connection)
        before = store.fetch_by_key_prefix(prefix)
        assert before is not None
        assert before.last_used_at is None

        resp = client.get("/api/v1/stations", headers=_auth(raw_key))
        assert resp.status_code == 200

        after = store.fetch_by_key_prefix(prefix)
        assert after is not None
        assert after.last_used_at is not None

    def test_expired_token_rejected_at_401_does_not_update_last_used_at(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.ADMIN, tenant_id=None, expires_at=_PAST
        )
        prefix = raw_key.split(".")[0]
        store = PgAccessTokenStore(db_connection)

        resp = client.get("/api/v1/stations", headers=_auth(raw_key))
        assert resp.status_code == 401

        after = store.fetch_by_key_prefix(prefix)
        assert after is not None
        assert after.last_used_at is None

    def test_unknown_key_rejected_at_401_does_not_error(
        self, client: TestClient
    ) -> None:
        # No matching row at all — require_principal must 401 BEFORE
        # attempting any last_used_at update (there is no token id to
        # update against).
        resp = client.get(
            "/api/v1/stations", headers=_auth("nonexistentprefix.badsecret")
        )
        assert resp.status_code == 401


_ADMIN_GATED_ROUTE_SAMPLES: list[tuple[str, str]] = [
    ("GET", "/api/v1/health/detail"),
    ("GET", "/health/detail/"),
    ("GET", "/"),
    ("GET", "/tables/"),
    ("GET", "/tables/some_table/"),
    ("GET", "/tables/some_table/rows"),
    ("GET", "/observations/"),
    ("GET", "/stations/"),
    ("GET", f"/stations/{uuid4()}/"),
    ("GET", f"/api/v1/stations/{uuid4()}/observations.json"),
    ("GET", f"/api/v1/stations/{uuid4()}/forcing.json"),
    ("GET", f"/api/v1/stations/{uuid4()}/baselines.json"),
    ("GET", f"/api/v1/stations/{uuid4()}/hindcasts.json"),
    ("GET", "/forecasts/"),
    ("GET", f"/forecasts/{uuid4()}/"),
    ("GET", f"/api/v1/forecasts/{uuid4()}/data.json"),
    ("GET", "/models/"),
    ("GET", "/models/some-model/"),
    ("GET", "/api/v1/models/some-model/skill-chart.json"),
]


class TestAdminGatedRoutesRejectConsumerAllowAdmin:
    """Major finding (Slice C fixer round): exercise EVERY retained legacy
    HTML route and `.json` export as both a consumer (must 403 — the router
    is admin-gated in full, R3) and an admin (must clear the auth gate; any
    remaining 404/200/etc is business logic, not auth). Complements the
    structural route-matrix test in tests/unit/api/test_security.py, which
    proves the classification but doesn't fire real requests."""

    @pytest.mark.parametrize("method,path", _ADMIN_GATED_ROUTE_SAMPLES)
    def test_consumer_is_forbidden(
        self,
        client: TestClient,
        db_connection: sa.Connection,
        method: str,
        path: str,
    ) -> None:
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.CONSUMER, station_ids=frozenset()
        )
        resp = client.request(method, path, headers=_auth(raw_key))
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path", _ADMIN_GATED_ROUTE_SAMPLES)
    def test_admin_clears_the_auth_gate(
        self,
        client: TestClient,
        db_connection: sa.Connection,
        method: str,
        path: str,
    ) -> None:
        raw_key = _make_token(db_connection, role=AccessTokenRole.ADMIN, tenant_id=None)
        resp = client.request(method, path, headers=_auth(raw_key))
        assert resp.status_code not in (401, 403)
