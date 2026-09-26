"""Plan 147 Slice C: DB-backed end-to-end auth enforcement + scope
filtering. Seeds real `access_tokens`/`access_token_stations` rows (via
`PgAccessTokenStore`) and drives the FastAPI app with real bearer keys —
the same pepper the app resolves at lifespan startup
(`ACCESS_TOKEN_PEPPER`, set process-wide by tests/conftest.py)."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.routes.forecast_lab import (
    get_bafu_forecast_archive_path,
    get_forecast_combination_strategy,
)
from sapphire_flow.api.security import (
    Principal,
    ensure_station_in_scope,
    hash_token,
    load_access_token_pepper,
    require_reviewer,
)
from sapphire_flow.db.metadata import access_token_stations, access_tokens
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
from sapphire_flow.types.enums import (
    AccessTokenRole,
    AlertStatus,
    ModelCombinationStrategy,
)
from sapphire_flow.types.ids import AccessTokenId, AlertId, StationId, TenantId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID, Tenant
from tests.conftest import make_alert, make_station_config
from tests.integration.api.test_dashboard_forecasts import _seed_forecast, _seed_model

if TYPE_CHECKING:
    from collections.abc import Generator

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
# require_principal compares token.expires_at against the REAL wall clock
# (datetime.now(UTC)), not the fixture's fixed _NOW — expiry fixtures must
# be relative to real time, or a fixed-past _FUTURE would already be
# "expired" against actual now() and every request would spuriously 401.
_REAL_NOW = ensure_utc(datetime.now(UTC))
_FUTURE = ensure_utc(_REAL_NOW + timedelta(days=30))
_PAST = ensure_utc(_REAL_NOW - timedelta(days=1))

# Plan 401: a reviewer token is tenant-bound and scoped exactly like a consumer.
_TENANT_BOUND_ROLES = pytest.mark.parametrize(
    "role", [AccessTokenRole.CONSUMER, AccessTokenRole.REVIEWER], ids=lambda r: r.value
)


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

    # The SINGLE request connection (get_connection, now RW-capable) resolves
    # to the rollback-isolated db_connection so the last_used_at write + reads
    # share it and see this test's not-yet-committed token/station rows. There
    # is no second connection dependency to override (Codex round 2 — auth uses
    # exactly one connection; that invariant is proven against the REAL engine
    # in TestAuthUsesExactlyOneConnectionPerRequest below, not masked here).
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
    @_TENANT_BOUND_ROLES
    def test_station_outside_token_tenant_is_rejected(
        self, db_connection: sa.Connection, role: AccessTokenRole
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
                role=role,
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

    @_TENANT_BOUND_ROLES
    @pytest.mark.parametrize("method,path", _ADMIN_GATED_ROUTE_SAMPLES)
    def test_consumer_is_forbidden(
        self,
        client: TestClient,
        db_connection: sa.Connection,
        method: str,
        path: str,
        role: AccessTokenRole,
    ) -> None:
        raw_key = _make_token(db_connection, role=role, station_ids=frozenset())
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


class TestAuthUsesExactlyOneConnectionPerRequest:
    """MAJOR (Codex round 2): auth must NOT open a second connection for the
    `last_used_at` write. This uses the REAL dependency wiring against the live
    engine (no override of get_connection to a shared object) and counts pool
    checkouts — a single request must check out EXACTLY ONE connection. Before
    the fix (a separate get_connection_rw), a request checked out two."""

    def test_single_request_checks_out_one_connection(
        self, db_engine: sa.Engine
    ) -> None:
        pepper = load_access_token_pepper()
        token_id = AccessTokenId(uuid4())
        key_prefix = f"conn{uuid4().hex[:8]}"
        raw_secret = uuid4().hex
        token = AccessToken(
            id=token_id,
            token_hash=hash_token(raw_secret, pepper=pepper),
            key_prefix=key_prefix,
            name="one-conn-token",
            role=AccessTokenRole.ADMIN,
            tenant_id=None,
            pepper_version=1,
            expires_at=_FUTURE,
            disabled_at=None,
            created_at=_NOW,
            last_used_at=None,
            station_ids=frozenset(),
        )
        # Seed a COMMITTED token on the real engine so the live get_connection
        # (its own transaction) can see it.
        with db_engine.begin() as conn:
            PgAccessTokenStore(conn).create_token(token, station_ids=frozenset())

        checkouts = 0

        def _count(*_args: object) -> None:
            nonlocal checkouts
            checkouts += 1

        event.listen(db_engine, "checkout", _count)
        try:
            with TestClient(app) as c:
                original_engine = app.state.engine
                app.state.engine = db_engine
                try:
                    checkouts = 0  # ignore startup/seed checkouts
                    resp = c.get(
                        "/api/v1/stations",
                        headers=_auth(f"{key_prefix}.{raw_secret}"),
                    )
                finally:
                    app.state.engine = original_engine
            assert resp.status_code == 200
            assert checkouts == 1, (
                f"expected exactly one connection checkout per request, got {checkouts}"
            )
        finally:
            event.remove(db_engine, "checkout", _count)
            with db_engine.begin() as conn:
                conn.execute(
                    sa.delete(access_tokens).where(access_tokens.c.id == token_id)
                )


class TestCrossTenantScopeRejectedOnLoad:
    """MAJOR (Codex round 2): stored consumer scope is re-validated against the
    token's tenant on LOAD (the read/auth path), not only at create. A scope
    row for a station in another tenant introduced out-of-band (corruption /
    direct SQL) must FAIL CLOSED — the load raises and auth returns 401, never
    silently authorizing the cross-tenant station."""

    def _seed_cross_tenant_scope_row(
        self, db_connection: sa.Connection, role: AccessTokenRole
    ) -> tuple[str, StationId]:
        # A consumer token in DEFAULT_TENANT_ID, initially validly scoped to an
        # in-tenant station...
        in_tenant_sid = _seed_station(
            db_connection, seed=40, tenant_id=DEFAULT_TENANT_ID
        )
        raw_key = _make_token(
            db_connection,
            role=role,
            tenant_id=DEFAULT_TENANT_ID,
            station_ids=frozenset({in_tenant_sid}),
        )
        # ...then a station in ANOTHER tenant + a scope row wired directly
        # (bypassing create_token's validation), simulating corruption.
        other_tenant = Tenant(
            id=TenantId(uuid4()),
            code=f"other-{uuid4().hex[:6]}",
            name="Other",
            created_at=_NOW,
        )
        PgTenantStore(db_connection).store_tenant(other_tenant)
        foreign_sid = _seed_station(db_connection, seed=41, tenant_id=other_tenant.id)

        prefix = raw_key.split(".")[0]
        token = PgAccessTokenStore(db_connection).fetch_by_key_prefix(prefix)
        assert token is not None
        db_connection.execute(
            sa.insert(access_token_stations).values(
                token_id=token.id, station_id=foreign_sid
            )
        )
        return raw_key, foreign_sid

    @_TENANT_BOUND_ROLES
    def test_load_raises_cross_tenant_scope_error(
        self, db_connection: sa.Connection, role: AccessTokenRole
    ) -> None:
        raw_key, _foreign = self._seed_cross_tenant_scope_row(db_connection, role)
        prefix = raw_key.split(".")[0]
        with pytest.raises(CrossTenantScopeError):
            PgAccessTokenStore(db_connection).fetch_by_key_prefix(prefix)

    @_TENANT_BOUND_ROLES
    def test_auth_fails_closed_with_401_not_authorized(
        self, client: TestClient, db_connection: sa.Connection, role: AccessTokenRole
    ) -> None:
        raw_key, foreign_sid = self._seed_cross_tenant_scope_row(db_connection, role)
        # The corrupt cross-tenant scope must NOT authorize the foreign
        # station — the whole token is rejected 401 (fail-closed).
        resp = client.get("/api/v1/stations", headers=_auth(raw_key))
        assert resp.status_code == 401
        resp_foreign = client.get(
            f"/api/v1/stations/{foreign_sid}", headers=_auth(raw_key)
        )
        assert resp_foreign.status_code == 401


# ---------- Plan 401 T2: the reviewer role ---------------------------------


def _review_test_app(db_connection: sa.Connection) -> FastAPI:
    """A test-only app standing for a REVIEW route — none exists until Plan
    402. The route takes a station and applies the principal's scope like any
    other station route."""
    review_app = FastAPI()
    review_app.state.access_token_pepper = load_access_token_pepper()

    def _override_conn() -> Generator[sa.Connection, None, None]:
        yield db_connection

    review_app.dependency_overrides[get_connection] = _override_conn

    @review_app.get("/review/stations/{station_id}")
    def _review_station(
        station_id: str, principal: Annotated[Principal, Depends(require_reviewer)]
    ) -> dict[str, str]:
        ensure_station_in_scope(principal, StationId(UUID(station_id)))
        return {"station_id": station_id}

    return review_app


class TestReviewRouteGate:
    """Plan 401 D2: a REVIEW route admits reviewer and admin tokens, refuses a
    consumer with 403, and applies the principal's station scope."""

    def _get(
        self, db_connection: sa.Connection, raw_key: str, station_id: StationId
    ) -> int:
        with TestClient(_review_test_app(db_connection)) as c:
            return c.get(
                f"/review/stations/{station_id}", headers=_auth(raw_key)
            ).status_code

    def test_consumer_is_refused(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection, seed=50, tenant_id=DEFAULT_TENANT_ID)
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.CONSUMER, station_ids=frozenset({sid})
        )
        assert self._get(db_connection, raw_key, sid) == 403

    def test_reviewer_is_admitted_for_an_in_scope_station(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, seed=51, tenant_id=DEFAULT_TENANT_ID)
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.REVIEWER, station_ids=frozenset({sid})
        )
        assert self._get(db_connection, raw_key, sid) == 200

    def test_reviewer_gets_404_for_an_out_of_scope_station(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection, seed=52, tenant_id=DEFAULT_TENANT_ID)
        other = _seed_station(db_connection, seed=53, tenant_id=DEFAULT_TENANT_ID)
        raw_key = _make_token(
            db_connection, role=AccessTokenRole.REVIEWER, station_ids=frozenset({sid})
        )
        assert self._get(db_connection, raw_key, other) == 404

    def test_admin_is_admitted(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection, seed=54, tenant_id=DEFAULT_TENANT_ID)
        raw_key = _make_token(db_connection, role=AccessTokenRole.ADMIN, tenant_id=None)
        assert self._get(db_connection, raw_key, sid) == 200


@pytest.fixture
def _forecast_lab_config() -> Generator[None, None, None]:
    """The snapshot route's two config dependencies, without a config file."""
    app.dependency_overrides[get_bafu_forecast_archive_path] = lambda: None
    app.dependency_overrides[get_forecast_combination_strategy] = lambda: (
        ModelCombinationStrategy.PRIMARY
    )
    yield
    app.dependency_overrides.pop(get_bafu_forecast_archive_path, None)
    app.dependency_overrides.pop(get_forecast_combination_strategy, None)


_OBS_QUERY = "parameter=discharge&start=2025-01-01T00:00:00Z&end=2025-01-03T00:00:00Z"
# Every GET PRINCIPAL route, by template (pinned against the live app below).
_DETAIL_ROUTES: dict[str, str] = {
    "/api/v1/stations/{station_id}": "/api/v1/stations/{sid}",
    "/api/v1/stations/{station_id}/observations": (
        "/api/v1/stations/{sid}/observations?" + _OBS_QUERY
    ),
    "/api/v1/stations/{station_id}/forecasts": "/api/v1/stations/{sid}/forecasts",
    "/api/v1/forecasts/{forecast_id}": "/api/v1/forecasts/{fid}",
    "/api/v1/forecast-lab/snapshot": (
        "/api/v1/forecast-lab/snapshot?station_code={code}"
    ),
}
_COLLECTION_ROUTES: dict[str, str] = {
    "/api/v1/stations": "/api/v1/stations",
    "/api/v1/alerts": "/api/v1/alerts?station_id={sid}",
    "/api/v1/forecast-lab/snapshot": "/api/v1/forecast-lab/snapshot",
}


class _Scene:
    def __init__(self, conn: sa.Connection) -> None:
        self.in_scope = _seed_station(conn, seed=70, tenant_id=DEFAULT_TENANT_ID)
        self.other = _seed_station(conn, seed=71, tenant_id=DEFAULT_TENANT_ID)
        model_id = _seed_model(conn)
        self.forecasts = {
            sid: _seed_forecast(
                conn, representation="members", station_id=sid, model_id=model_id
            )
            for sid in (self.in_scope, self.other)
        }
        alert_store = PgAlertStore(conn)
        for seed, sid in ((70, self.in_scope), (71, self.other)):
            alert_store.upsert_alert(
                make_alert(station_id=sid, rng=random.Random(seed))
            )
        self.codes = {self.in_scope: "ST-70", self.other: "ST-71"}

    def url(self, template: str, station_id: StationId) -> str:
        return template.format(
            sid=station_id,
            fid=self.forecasts[station_id],
            code=self.codes[station_id],
        )


def _comparable(resp: object) -> tuple[int, object]:
    """Status plus body, with the snapshot's wall-clock fields dropped."""
    status: int = resp.status_code  # type: ignore[attr-defined]
    body = resp.json()  # type: ignore[attr-defined]
    if isinstance(body, dict) and body.get("schema_version", "").startswith(
        "forecast-lab-snapshot"
    ):
        body = [s["station"]["code"] for s in body["stations"]]
    return status, body


@pytest.mark.usefixtures("_forecast_lab_config")
class TestReviewerOnPrincipalRoutes:
    """Plan 401 D2: on every existing PRINCIPAL route a reviewer gets exactly
    what a consumer with the same scope gets."""

    def test_route_lists_cover_every_get_principal_route(self) -> None:
        from tests.unit.api.test_security import _classify_routes

        principal_gets = {
            path
            for (method, path), tag in _classify_routes().items()
            if tag == "PRINCIPAL" and method == "GET"
        }
        assert principal_gets == set(_DETAIL_ROUTES) | set(_COLLECTION_ROUTES)

    def _keys(self, conn: sa.Connection, scene: _Scene) -> dict[AccessTokenRole, str]:
        return {
            role: _make_token(conn, role=role, station_ids=frozenset({scene.in_scope}))
            for role in (AccessTokenRole.CONSUMER, AccessTokenRole.REVIEWER)
        }

    @pytest.mark.parametrize("template", list(_DETAIL_ROUTES.values()))
    def test_detail_route_in_scope_200_out_of_scope_404(
        self, client: TestClient, db_connection: sa.Connection, template: str
    ) -> None:
        scene = _Scene(db_connection)
        keys = self._keys(db_connection, scene)
        reviewer = _auth(keys[AccessTokenRole.REVIEWER])

        assert (
            client.get(
                scene.url(template, scene.in_scope), headers=reviewer
            ).status_code
            == 200
        )
        assert (
            client.get(scene.url(template, scene.other), headers=reviewer).status_code
            == 404
        )

    @pytest.mark.parametrize(
        "template", list(_DETAIL_ROUTES.values()) + list(_COLLECTION_ROUTES.values())
    )
    def test_reviewer_matches_consumer(
        self, client: TestClient, db_connection: sa.Connection, template: str
    ) -> None:
        scene = _Scene(db_connection)
        keys = self._keys(db_connection, scene)

        for station_id in (scene.in_scope, scene.other):
            url = scene.url(template, station_id)
            consumer = client.get(url, headers=_auth(keys[AccessTokenRole.CONSUMER]))
            reviewer = client.get(url, headers=_auth(keys[AccessTokenRole.REVIEWER]))
            assert _comparable(reviewer) == _comparable(consumer), url

    def test_collections_filter_to_the_reviewer_scope(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        scene = _Scene(db_connection)
        reviewer = _auth(self._keys(db_connection, scene)[AccessTokenRole.REVIEWER])

        stations = client.get("/api/v1/stations", headers=reviewer)
        alerts_out = client.get(
            f"/api/v1/alerts?station_id={scene.other}", headers=reviewer
        )
        snapshot = client.get("/api/v1/forecast-lab/snapshot", headers=reviewer)

        assert [i["id"] for i in stations.json()["items"]] == [str(scene.in_scope)]
        assert (alerts_out.status_code, alerts_out.json()["items"]) == (200, [])
        assert _comparable(snapshot) == (200, ["ST-70"])

    @_TENANT_BOUND_ROLES
    def test_acknowledgement_post_is_501(
        self, client: TestClient, db_connection: sa.Connection, role: AccessTokenRole
    ) -> None:
        raw_key = _make_token(db_connection, role=role, station_ids=frozenset())
        resp = client.post(
            f"/api/v1/alerts/{uuid4()}/acknowledge", headers=_auth(raw_key)
        )
        assert resp.status_code == 501


class TestForecastDetailHidesOutOfScopeExistence:
    """Security review 2026-09-26: an out-of-scope forecast must answer
    exactly like an absent one — "Station not found" confirmed it exists."""

    @_TENANT_BOUND_ROLES
    def test_absent_and_out_of_scope_forecasts_answer_identically(
        self, client: TestClient, db_connection: sa.Connection, role: AccessTokenRole
    ) -> None:
        in_scope = _seed_station(db_connection, seed=60, tenant_id=DEFAULT_TENANT_ID)
        other = _seed_station(db_connection, seed=61, tenant_id=DEFAULT_TENANT_ID)
        model_id = _seed_model(db_connection)
        out_of_scope_forecast = _seed_forecast(
            db_connection,
            representation="members",
            station_id=other,
            model_id=model_id,
        )
        raw_key = _make_token(
            db_connection, role=role, station_ids=frozenset({in_scope})
        )

        absent = client.get(f"/api/v1/forecasts/{uuid4()}", headers=_auth(raw_key))
        hidden = client.get(
            f"/api/v1/forecasts/{out_of_scope_forecast}", headers=_auth(raw_key)
        )

        assert (hidden.status_code, hidden.json()) == (
            absent.status_code,
            absent.json(),
        )
        assert absent.status_code == 404
