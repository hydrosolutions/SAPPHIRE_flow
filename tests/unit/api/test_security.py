from __future__ import annotations

import os
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.api.security import (
    PepperNotConfiguredError,
    Principal,
    generate_raw_token,
    hash_token,
    load_access_token_pepper,
    require_admin,
    require_principal,
    split_raw_token,
)
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.ids import AccessTokenId, StationId, TenantId

if TYPE_CHECKING:
    from pathlib import Path


class TestHashToken:
    def test_same_input_same_pepper_is_deterministic(self) -> None:
        assert hash_token("secret", pepper="pepper1") == hash_token(
            "secret", pepper="pepper1"
        )

    def test_different_pepper_changes_hash(self) -> None:
        assert hash_token("secret", pepper="pepper1") != hash_token(
            "secret", pepper="pepper2"
        )

    def test_different_secret_changes_hash(self) -> None:
        assert hash_token("secret-a", pepper="pepper1") != hash_token(
            "secret-b", pepper="pepper1"
        )

    def test_never_returns_the_raw_secret(self) -> None:
        digest = hash_token("my-raw-secret", pepper="pepper1")
        assert "my-raw-secret" not in digest


class TestGenerateAndSplitRawToken:
    def test_round_trips_prefix_and_secret(self) -> None:
        raw_key, key_prefix, raw_secret = generate_raw_token()
        parts = split_raw_token(raw_key)
        assert parts == (key_prefix, raw_secret)

    def test_generates_unique_keys(self) -> None:
        keys = {generate_raw_token()[0] for _ in range(20)}
        assert len(keys) == 20

    def test_split_rejects_malformed_key(self) -> None:
        assert split_raw_token("no-dot-separator") is None
        assert split_raw_token("") is None
        assert split_raw_token(".missing-prefix") is None
        assert split_raw_token("missing-secret.") is None


class TestLoadAccessTokenPepperFailsClosed:
    """R1 LOCKED: no fallback to an unpeppered hash — the API/CLI refuse to
    run without a readable, non-empty pepper."""

    def test_missing_file_and_no_env_var_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ACCESS_TOKEN_PEPPER", raising=False)
        with pytest.raises(PepperNotConfiguredError):
            load_access_token_pepper(secret_path=tmp_path / "nope")

    def test_empty_file_and_no_env_var_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ACCESS_TOKEN_PEPPER", raising=False)
        p = tmp_path / "pepper"
        p.write_text("")
        with pytest.raises(PepperNotConfiguredError):
            load_access_token_pepper(secret_path=p)

    def test_reads_populated_file(self, tmp_path: Path) -> None:
        p = tmp_path / "pepper"
        p.write_text("super-secret-pepper\n")
        assert load_access_token_pepper(secret_path=p) == "super-secret-pepper"

    def test_falls_back_to_env_var_when_file_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACCESS_TOKEN_PEPPER", "env-pepper")
        assert load_access_token_pepper(secret_path=tmp_path / "nope") == "env-pepper"

    def test_whitespace_only_env_var_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Blocker/major fix (Codex round 2): a whitespace-only pepper is
        NOT a pepper — strip + reject fail-closed, never accept `"   "`."""
        monkeypatch.setenv("ACCESS_TOKEN_PEPPER", "   \t\n")
        with pytest.raises(PepperNotConfiguredError):
            load_access_token_pepper(secret_path=tmp_path / "nope")

    def test_whitespace_only_file_and_no_env_var_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ACCESS_TOKEN_PEPPER", raising=False)
        p = tmp_path / "pepper"
        p.write_text("   \n")
        with pytest.raises(PepperNotConfiguredError):
            load_access_token_pepper(secret_path=p)

    def test_env_var_is_stripped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACCESS_TOKEN_PEPPER", "  padded-pepper  ")
        got = load_access_token_pepper(secret_path=tmp_path / "nope")
        assert got == "padded-pepper"


def _principal(
    *,
    role: AccessTokenRole = AccessTokenRole.CONSUMER,
    station_ids: frozenset[StationId] = frozenset(),
    tenant_id: TenantId | None = None,
) -> Principal:
    return Principal(
        token_id=AccessTokenId(uuid4()),
        role=role,
        tenant_id=tenant_id,
        station_ids=station_ids,
    )


class TestPrincipalStationInScope:
    def test_admin_sees_every_station(self) -> None:
        p = _principal(role=AccessTokenRole.ADMIN)
        assert p.station_in_scope(StationId(uuid4())) is True

    def test_admin_sees_stationless_null(self) -> None:
        p = _principal(role=AccessTokenRole.ADMIN)
        assert p.station_in_scope(None) is True

    def test_consumer_in_scope_station_is_visible(self) -> None:
        sid = StationId(uuid4())
        p = _principal(station_ids=frozenset({sid}))
        assert p.station_in_scope(sid) is True

    def test_consumer_out_of_scope_station_is_hidden(self) -> None:
        sid = StationId(uuid4())
        other = StationId(uuid4())
        p = _principal(station_ids=frozenset({sid}))
        assert p.station_in_scope(other) is False

    def test_consumer_empty_scope_sees_nothing(self) -> None:
        """R2 LOCKED: empty scope = sees nothing, NOT 'all stations'."""
        p = _principal(station_ids=frozenset())
        assert p.station_in_scope(StationId(uuid4())) is False

    def test_consumer_never_sees_stationless_null(self) -> None:
        """F7 LOCKED: a null (stationless) station_id is never in a
        consumer's scope, fail-closed — independent of what stations the
        token IS scoped to."""
        sid = StationId(uuid4())
        p = _principal(station_ids=frozenset({sid}))
        assert p.station_in_scope(None) is False


# ---------- FastAPI-level enforcement: NO dependency overrides ---------------
# Uses a bare TestClient(app) — the shared tests/unit/api/conftest.py `client`
# fixture overrides require_principal/require_admin (those tests exercise
# route business logic, not auth). These prove the actual auth wiring.


@pytest.fixture
def bare_client():  # noqa: ANN201 - Generator[TestClient, None, None], see below
    from fastapi.testclient import TestClient

    from sapphire_flow.api import app

    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestEndpointEnforcement:
    """Plan 147 Slice C (G2/F3(a)): every endpoint except the shallow
    public health check requires a valid bearer key."""

    def test_health_is_public_without_any_key(self, bare_client) -> None:  # noqa: ANN001
        resp = bare_client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_health_detail_401_without_key(self, bare_client) -> None:  # noqa: ANN001
        resp = bare_client.get("/api/v1/health/detail")
        assert resp.status_code == 401

    def test_stations_401_without_key(self, bare_client) -> None:  # noqa: ANN001
        resp = bare_client.get("/api/v1/stations")
        assert resp.status_code == 401

    def test_forecast_detail_401_without_key(self, bare_client) -> None:  # noqa: ANN001
        resp = bare_client.get(f"/api/v1/forecasts/{uuid4()}")
        assert resp.status_code == 401

    def test_alerts_401_without_key(self, bare_client) -> None:  # noqa: ANN001
        resp = bare_client.get("/api/v1/alerts")
        assert resp.status_code == 401

    def test_legacy_tables_browser_401_without_key(self, bare_client) -> None:  # noqa: ANN001
        resp = bare_client.get("/tables/")
        assert resp.status_code == 401

    def test_legacy_stations_html_401_without_key(self, bare_client) -> None:  # noqa: ANN001
        resp = bare_client.get("/stations/")
        assert resp.status_code == 401

    def test_model_skill_chart_json_401_without_key(self, bare_client) -> None:  # noqa: ANN001
        resp = bare_client.get("/api/v1/models/some-model/skill-chart.json")
        assert resp.status_code == 401

    def test_malformed_bearer_header_is_401(self, bare_client) -> None:  # noqa: ANN001
        resp = bare_client.get(
            "/api/v1/stations", headers={"Authorization": "not-a-bearer-token"}
        )
        assert resp.status_code == 401

    def test_malformed_key_shape_is_401(self, bare_client) -> None:  # noqa: ANN001
        resp = bare_client.get(
            "/api/v1/stations", headers={"Authorization": "Bearer no-dot-here"}
        )
        assert resp.status_code == 401


def _flat_dependant_calls(dependant: object) -> list[object]:
    """Recursively flatten a FastAPI `Dependant` tree's `.call` targets."""
    call = getattr(dependant, "call", None)
    calls: list[object] = [call] if call is not None else []
    for sub in getattr(dependant, "dependencies", []):
        calls.extend(_flat_dependant_calls(sub))
    return calls


def _classify_routes(fastapi_app: object | None = None) -> dict[tuple[str, str], str]:
    """method+path -> "PUBLIC" | "PRINCIPAL" | "ADMIN", derived from each
    mounted route's actual dependency graph (not a hand-maintained belief
    about which router it lives in) — a route added to an existing router
    without `dependencies=[Depends(require_admin/require_principal)]`
    inherits whatever the router already declares, but a BRAND NEW router
    mounted with `app.include_router(...)` and no `dependencies=` at all
    would show up here as PUBLIC and fail `test_only_health_is_public`.

    Blocker fix (Codex round 2): this now walks EVERY entry in `app.routes`
    — APIRoute, Mount, and FastAPI's built-in `/openapi.json`/`/docs`/
    `/redoc` `Route`s — with NO escape hatch for routes lacking a
    `dependant`. A dependant-less route (a built-in doc endpoint, a static
    Mount, an ungated `include_router`) is classified PUBLIC, so it MUST be
    the single allowed public route or it fails the matrix. Re-enabling the
    OpenAPI schema or mounting any ungated route therefore trips the test."""
    if fastapi_app is None:
        from sapphire_flow.api import app as _app

        fastapi_app = _app

    result: dict[tuple[str, str], str] = {}
    for route in fastapi_app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", None)
        if path is None:
            continue
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            # No auth dependency at all (built-in docs/openapi, a static
            # Mount, or an ungated router) — fail-closed as PUBLIC.
            tag = "PUBLIC"
        else:
            calls = _flat_dependant_calls(dependant)
            tag = (
                "ADMIN"
                if require_admin in calls
                else ("PRINCIPAL" if require_principal in calls else "PUBLIC")
            )
        methods = getattr(route, "methods", None)
        if not methods:
            # A Mount / method-less route is still addressable — record it so
            # an ungated mount cannot slip past the matrix.
            result[("*", path)] = tag
            continue
        for method in methods:
            result[(method, path)] = tag
    return result


class TestRouteAuthMatrixExhaustive:
    """Major finding (Slice C fixer round): the plan requires EVERY
    endpoint, EVERY legacy HTML route, and EVERY `.json` export to be
    classified — not just a hand-picked sample. This walks the live
    `app.routes` table and pins the complete method/path/auth
    classification, so a newly added or accidentally-ungated router fails
    here immediately (`test_only_health_is_public`) even before any
    behavioral test would catch it."""

    _EXPECTED: dict[tuple[str, str], str] = {
        ("GET", "/api/v1/health"): "PUBLIC",
        ("GET", "/api/v1/health/detail"): "ADMIN",
        ("GET", "/health/detail/"): "ADMIN",
        ("GET", "/"): "ADMIN",
        ("GET", "/tables/"): "ADMIN",
        ("GET", "/tables/{table_name}/"): "ADMIN",
        ("GET", "/tables/{table_name}/rows"): "ADMIN",
        ("GET", "/observations/"): "ADMIN",
        ("GET", "/stations/"): "ADMIN",
        ("GET", "/stations/{station_id}/"): "ADMIN",
        ("GET", "/api/v1/stations/{station_id}/observations.json"): "ADMIN",
        ("GET", "/api/v1/stations/{station_id}/forcing.json"): "ADMIN",
        ("GET", "/api/v1/stations/{station_id}/baselines.json"): "ADMIN",
        ("GET", "/api/v1/stations/{station_id}/hindcasts.json"): "ADMIN",
        ("GET", "/forecasts/"): "ADMIN",
        ("GET", "/forecasts/{forecast_id}/"): "ADMIN",
        ("GET", "/api/v1/forecasts/{forecast_id}/data.json"): "ADMIN",
        ("GET", "/models/"): "ADMIN",
        ("GET", "/models/{model_id}/"): "ADMIN",
        ("GET", "/api/v1/models/{model_id}/skill-chart.json"): "ADMIN",
        ("GET", "/api/v1/stations"): "PRINCIPAL",
        ("GET", "/api/v1/stations/{station_id}"): "PRINCIPAL",
        ("GET", "/api/v1/stations/{station_id}/observations"): "PRINCIPAL",
        ("GET", "/api/v1/stations/{station_id}/forecasts"): "PRINCIPAL",
        ("GET", "/api/v1/forecasts/{forecast_id}"): "PRINCIPAL",
        ("GET", "/api/v1/alerts"): "PRINCIPAL",
        ("POST", "/api/v1/alerts/{alert_id}/acknowledge"): "PRINCIPAL",
    }

    def test_every_mounted_route_matches_the_expected_classification(self) -> None:
        assert _classify_routes() == self._EXPECTED

    def test_only_health_is_public(self) -> None:
        actual = _classify_routes()
        public_routes = {path for path, tag in actual.items() if tag == "PUBLIC"}
        assert public_routes == {("GET", "/api/v1/health")}


class TestRouteMatrixCatchesUngatedRoutes:
    """Blocker red-first proof (Codex round 2): the exhaustive matrix must
    FAIL the instant an unauthenticated route is mounted — FastAPI's built-in
    `/openapi.json`/`/docs`/`/redoc` (which the real app disables) or any
    hand-added ungated route. The old matrix skipped dependant-less routes,
    so these evaded it entirely."""

    def test_reenabling_openapi_docs_is_caught_as_ungated_public(self) -> None:
        from fastapi import FastAPI

        # Defaults: openapi_url/docs_url/redoc_url ENABLED — i.e. the state
        # the real app used to ship in. These carry no `dependant`.
        leaky = FastAPI(title="leaky")
        classified = _classify_routes(leaky)
        public = {path for path, tag in classified.items() if tag == "PUBLIC"}
        # openapi.json / docs / redoc all surface as ungated PUBLIC — so a
        # `test_only_health_is_public`-shaped assertion would fail here.
        assert ("GET", "/openapi.json") in public
        assert public != {("GET", "/api/v1/health")}

    def test_added_ungated_route_is_caught_as_public(self) -> None:
        from fastapi import FastAPI

        leaky = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @leaky.get("/leak")
        def _leak() -> dict[str, str]:  # pragma: no cover - never called
            return {}

        classified = _classify_routes(leaky)
        assert classified[("GET", "/leak")] == "PUBLIC"

    def test_real_app_disables_builtin_docs(self) -> None:
        """Green side: the real app mounts NO openapi/docs/redoc route."""
        classified = _classify_routes()
        paths = {path for _method, path in classified}
        assert "/openapi.json" not in paths
        assert "/docs" not in paths
        assert "/redoc" not in paths
