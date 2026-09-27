from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlsplit

import jwt
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Depends, HTTPException, Request
from jwt import InvalidTokenError, PyJWKClient, PyJWKClientError

from sapphire_flow.api.deps import get_connection
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store.human_identity_store import PgHumanIdentityStore

if TYPE_CHECKING:
    from sapphire_flow.types.human_auth import HumanPermission, HumanPrincipal
    from sapphire_flow.types.ids import StationId

_PREFIX = "SAPPHIRE_HUMAN_OIDC_"
_MAX_LIFETIME_SECONDS = 1800
_CLOCK_SKEW_SECONDS = 60
_ALGORITHMS = frozenset({"RS256", "ES256"})


class HumanAuthError(ValueError):
    pass


@dataclass(frozen=True, kw_only=True, slots=True)
class OidcConfig:
    issuer: str
    audience: str
    jwks_url: str
    algorithm: str
    mfa_amr_values: frozenset[str]
    mfa_acr_values: frozenset[str]

    def __post_init__(self) -> None:
        for name, url in (("issuer", self.issuer), ("jwks_url", self.jwks_url)):
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username:
                raise ConfigurationError(f"human OIDC {name} must be an HTTPS URL")
        if not self.audience.strip():
            raise ConfigurationError("human OIDC audience is required")
        if self.algorithm not in _ALGORITHMS:
            raise ConfigurationError("human OIDC algorithm must be RS256 or ES256")
        if not self.mfa_amr_values and not self.mfa_acr_values:
            raise ConfigurationError("human OIDC MFA assurance values are required")


def _csv_env(name: str) -> frozenset[str]:
    return frozenset(
        value.strip() for value in os.environ.get(name, "").split(",") if value.strip()
    )


def load_oidc_config() -> OidcConfig | None:
    names = ("ISSUER", "AUDIENCE", "JWKS_URL", "ALGORITHM", "MFA_AMR", "MFA_ACR")
    values = {name: os.environ.get(f"{_PREFIX}{name}", "").strip() for name in names}
    if not any(values.values()):
        return None
    return OidcConfig(
        issuer=values["ISSUER"],
        audience=values["AUDIENCE"],
        jwks_url=values["JWKS_URL"],
        algorithm=values["ALGORITHM"],
        mfa_amr_values=_csv_env(f"{_PREFIX}MFA_AMR"),
        mfa_acr_values=_csv_env(f"{_PREFIX}MFA_ACR"),
    )


class SigningKey(Protocol):
    @property
    def key(self) -> RSAPublicKey | EllipticCurvePublicKey: ...


class SigningKeyClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class OidcIdentity:
    issuer: str
    subject: str


class HumanTokenVerifier:
    def __init__(
        self,
        config: OidcConfig,
        *,
        key_client: SigningKeyClient,
        clock: Callable[[], datetime],
    ) -> None:
        self._config = config
        self._keys = key_client
        self._clock = clock

    def verify(self, token: str) -> OidcIdentity:
        try:
            header = jwt.get_unverified_header(token)
            if (
                header.get("alg") != self._config.algorithm
                or not isinstance(header.get("kid"), str)
                or not header["kid"]
            ):
                raise HumanAuthError("invalid OIDC token header")
            key = self._keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[self._config.algorithm],
                issuer=self._config.issuer,
                audience=self._config.audience,
                options={
                    "require": ["iss", "aud", "sub", "iat", "exp", "nbf"],
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except (InvalidTokenError, PyJWKClientError) as exc:
            raise HumanAuthError("invalid OIDC access token") from exc
        self._check_claims(claims)
        return OidcIdentity(issuer=self._config.issuer, subject=claims["sub"])

    def _check_claims(self, claims: dict[str, object]) -> None:
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise HumanAuthError("OIDC subject is missing")
        times = (claims.get("iat"), claims.get("exp"), claims.get("nbf"))
        if any(type(value) is not int for value in times):
            raise HumanAuthError("OIDC token times must be integer timestamps")
        iat, exp, nbf = times
        assert isinstance(iat, int) and isinstance(exp, int) and isinstance(nbf, int)
        now = self._clock().astimezone(UTC).timestamp()
        if (
            exp <= iat
            or exp - iat > _MAX_LIFETIME_SECONDS
            or iat > now + _CLOCK_SKEW_SECONDS
            or now - iat > _MAX_LIFETIME_SECONDS + _CLOCK_SKEW_SECONDS
            or nbf > now + _CLOCK_SKEW_SECONDS
            or nbf > exp
            or exp <= now - _CLOCK_SKEW_SECONDS
        ):
            raise HumanAuthError("OIDC token is outside its allowed time window")
        amr = claims.get("amr")
        acr = claims.get("acr")
        valid_amr = False
        if isinstance(amr, list):
            amr_values = cast("list[object]", amr)
            valid_amr = all(isinstance(value, str) for value in amr_values) and any(
                value in self._config.mfa_amr_values for value in amr_values
            )
        valid_acr = isinstance(acr, str) and acr in self._config.mfa_acr_values
        if not (valid_amr or valid_acr):
            raise HumanAuthError("OIDC token lacks required MFA assurance")


def build_human_token_verifier(config: OidcConfig) -> HumanTokenVerifier:
    return HumanTokenVerifier(
        config,
        key_client=PyJWKClient(
            config.jwks_url,
            cache_jwk_set=True,
            cache_keys=False,
            lifespan=300,
            timeout=3,
            cooldown_duration=60,
        ),
        clock=lambda: datetime.now(UTC),
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Missing or invalid human access token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_human_principal(
    request: Request,
    conn: sa.Connection = Depends(get_connection),
) -> HumanPrincipal:
    verifier: HumanTokenVerifier | None = getattr(
        request.app.state, "human_oidc_verifier", None
    )
    if verifier is None:
        raise HTTPException(status_code=503, detail="Human authentication is disabled")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()
    try:
        identity = verifier.verify(token)
    except HumanAuthError:
        raise _unauthorized() from None
    principal = PgHumanIdentityStore(conn).resolve_principal(
        issuer=identity.issuer, subject=identity.subject
    )
    if principal is None:
        raise _unauthorized()
    return principal


def require_human_station_permission(
    principal: HumanPrincipal,
    station_id: StationId,
    permission: HumanPermission,
) -> None:
    if not principal.allows(station_id, permission):
        raise HTTPException(status_code=404, detail="Station not found")
