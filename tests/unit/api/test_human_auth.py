# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnusedFunction=false
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from jwt import PyJWKClient
from jwt.algorithms import RSAAlgorithm

from sapphire_flow.api.cors import ScopedCorsMiddleware, parse_exact_origin
from sapphire_flow.api.human_auth import (
    HumanAuthError,
    HumanTokenVerifier,
    OidcConfig,
    build_human_token_verifier,
    load_oidc_config,
    require_human_principal,
)
from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Iterator

    import sqlalchemy as sa
    from cryptography.hazmat.primitives.asymmetric.rsa import (
        RSAPrivateKey,
        RSAPublicKey,
    )

_NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
_ISSUER = "https://idp.example.org/"
_AUDIENCE = "sapphire-flow-api"


@dataclass(frozen=True)
class _Key:
    key: RSAPublicKey


class _KeyClient:
    def __init__(self, public_key: RSAPublicKey) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> _Key:
        return _Key(key=self.public_key)


@pytest.fixture
def token_setup() -> tuple[HumanTokenVerifier, RSAPrivateKey]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    config = OidcConfig(
        issuer=_ISSUER,
        audience=_AUDIENCE,
        jwks_url=f"{_ISSUER}.well-known/jwks.json",
        algorithm="RS256",
        mfa_amr_values=frozenset({"mfa"}),
        mfa_acr_values=frozenset(),
    )
    verifier = HumanTokenVerifier(
        config, key_client=_KeyClient(private.public_key()), clock=lambda: _NOW
    )
    return verifier, private


def _claims(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": "hydrologist-17",
        "iat": int((_NOW - timedelta(minutes=5)).timestamp()),
        "nbf": int((_NOW - timedelta(minutes=5)).timestamp()),
        "exp": int((_NOW + timedelta(minutes=25)).timestamp()),
        "amr": ["pwd", "mfa"],
    }
    values.update(overrides)
    return values


def _token(
    private_key: RSAPrivateKey, claims: dict[str, object], *, kid: str = "k1"
) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


class TestHumanTokenVerifier:
    def test_unknown_key_ids_do_not_refetch_jwks_each_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        config = OidcConfig(
            issuer=_ISSUER,
            audience=_AUDIENCE,
            jwks_url=f"{_ISSUER}.well-known/jwks.json",
            algorithm="RS256",
            mfa_amr_values=frozenset({"mfa"}),
            mfa_acr_values=frozenset(),
        )
        calls: list[int] = []

        def fetch_data(_client: PyJWKClient) -> dict[str, object]:
            calls.append(1)
            key = RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
            key["kid"] = "known"
            return {"keys": [key]}

        monkeypatch.setattr(PyJWKClient, "fetch_data", fetch_data)
        verifier = build_human_token_verifier(config)
        now = datetime.now(UTC)
        claims = _claims(
            iat=int((now - timedelta(minutes=5)).timestamp()),
            nbf=int((now - timedelta(minutes=5)).timestamp()),
            exp=int((now + timedelta(minutes=25)).timestamp()),
        )
        assert verifier.verify(_token(private, claims, kid="known")).subject == (
            "hydrologist-17"
        )
        assert len(calls) == 1
        for kid in ("unknown-1", "unknown-2"):
            with pytest.raises(HumanAuthError):
                verifier.verify(_token(private, claims, kid=kid))
        assert len(calls) == 2
        assert verifier.verify(_token(private, claims, kid="known")).subject == (
            "hydrologist-17"
        )

    def test_accepts_signed_mfa_token(
        self, token_setup: tuple[HumanTokenVerifier, RSAPrivateKey]
    ) -> None:
        verifier, private = token_setup
        identity = verifier.verify(_token(private, _claims()))
        assert (identity.issuer, identity.subject) == (_ISSUER, "hydrologist-17")

    @pytest.mark.parametrize(
        "change",
        [
            {"iss": "https://wrong.example.org/"},
            {"aud": "wrong-api"},
            {"sub": ""},
            {"amr": ["pwd"]},
            {"iat": int((_NOW - timedelta(hours=2)).timestamp())},
            {"exp": int((_NOW - timedelta(minutes=2)).timestamp())},
            {"nbf": int((_NOW + timedelta(minutes=3)).timestamp())},
            {
                "iat": int((_NOW - timedelta(minutes=5)).timestamp()),
                "exp": int((_NOW + timedelta(minutes=31)).timestamp()),
            },
            {"iat": int((_NOW + timedelta(minutes=3)).timestamp())},
        ],
    )
    def test_rejects_invalid_claims(
        self,
        token_setup: tuple[HumanTokenVerifier, RSAPrivateKey],
        change: dict[str, object],
    ) -> None:
        verifier, private = token_setup
        with pytest.raises(HumanAuthError):
            verifier.verify(_token(private, _claims(**change)))

    @pytest.mark.parametrize("missing", ["iss", "aud", "sub", "iat", "exp", "nbf"])
    def test_rejects_missing_required_claim(
        self, token_setup: tuple[HumanTokenVerifier, RSAPrivateKey], missing: str
    ) -> None:
        verifier, private = token_setup
        claims = _claims()
        del claims[missing]
        with pytest.raises(HumanAuthError):
            verifier.verify(_token(private, claims))

    def test_rejects_wrong_signature(
        self, token_setup: tuple[HumanTokenVerifier, RSAPrivateKey]
    ) -> None:
        verifier, _ = token_setup
        other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(HumanAuthError):
            verifier.verify(_token(other_private, _claims()))

    def test_rejects_wrong_algorithm(
        self, token_setup: tuple[HumanTokenVerifier, RSAPrivateKey]
    ) -> None:
        verifier, _ = token_setup
        with pytest.raises(HumanAuthError):
            verifier.verify(
                jwt.encode(
                    _claims(), "s" * 32, algorithm="HS256", headers={"kid": "k1"}
                )
            )


class TestOidcConfiguration:
    def test_missing_token_failures_use_fresh_exceptions(self) -> None:
        app = FastAPI()
        app.state.human_oidc_verifier = object()
        request = Request({"type": "http", "app": app, "headers": []})
        denials: list[HTTPException] = []
        for _ in range(2):
            with pytest.raises(HTTPException) as denial:
                require_human_principal(request, cast("sa.Connection", None))
            denials.append(denial.value)
        assert denials[0] is not denials[1]
        assert [denial.status_code for denial in denials] == [401, 401]

    def test_absent_configuration_disables_human_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in (
            "ISSUER",
            "AUDIENCE",
            "JWKS_URL",
            "ALGORITHM",
            "MFA_AMR",
            "MFA_ACR",
        ):
            monkeypatch.delenv(f"SAPPHIRE_HUMAN_OIDC_{name}", raising=False)
        assert load_oidc_config() is None

    def test_partial_configuration_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_HUMAN_OIDC_ISSUER", _ISSUER)
        with pytest.raises(ConfigurationError):
            load_oidc_config()


class TestScopedCors:
    @pytest.fixture
    def client(self) -> Iterator[TestClient]:
        app = FastAPI()

        @app.post("/api/v1/review/forecasts/one/publish")
        def publish() -> dict[str, str]:
            return {"result": "ok"}

        @app.post("/api/v1/other")
        def other() -> dict[str, str]:
            return {"result": "ok"}

        app.add_middleware(
            ScopedCorsMiddleware,
            consumer_origins=["https://consumer.example.org"],
            human_dashboard_origin="https://dashboard.example.org",
        )
        with TestClient(app) as client:
            yield client

    def test_review_post_preflight_requires_exact_dashboard_origin(
        self, client: TestClient
    ) -> None:
        allowed = client.options(
            "/api/v1/review/forecasts/one/publish",
            headers={
                "Origin": "https://dashboard.example.org",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,idempotency-key",
            },
        )
        assert allowed.status_code == 200
        assert (
            allowed.headers["access-control-allow-origin"]
            == "https://dashboard.example.org"
        )
        denied = client.options(
            "/api/v1/review/forecasts/one/publish",
            headers={
                "Origin": "https://evil.example.org",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert denied.status_code == 400

    def test_post_outside_review_path_has_no_cors_permission(
        self, client: TestClient
    ) -> None:
        response = client.options(
            "/api/v1/other",
            headers={
                "Origin": "https://dashboard.example.org",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code == 400

    def test_wildcard_and_origin_path_are_rejected(self) -> None:
        for origin in ("*", "https://dashboard.example.org/path"):
            with pytest.raises(ConfigurationError):
                parse_exact_origin(origin)
