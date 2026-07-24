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
