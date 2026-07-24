"""Plan 147 Slice C: access-token authentication + authorization.

R1 LOCKED = HMAC-SHA-256 over the raw key with a server-side pepper (NOT
bcrypt — see `security.md` § Authentication (v1.0 headless subset)).
Fail-closed: the API refuses to serve authenticated routes without a
readable, non-empty pepper (`load_access_token_pepper`, mirroring the
existing `load_recap_api_key` Docker-secret pattern — file first, env var
fallback for local dev only).

Two roles only (G4): `consumer` (read, station-scoped) and `admin` (read,
unscoped + CLI token/tenant management). No bearer key of any role may
mutate state — that is enforced by simply never `Depends`-ing security on a
POST/PATCH/DELETE route (the sole exception, alert acknowledgement, is
removed from the v1.0 surface, see `routes/api_alerts.py`).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request

from sapphire_flow.api.deps import get_connection
from sapphire_flow.db.metadata import access_tokens
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.store.access_token_store import (
    CrossTenantScopeError,
    PgAccessTokenStore,
)
from sapphire_flow.types.enums import AccessTokenRole

if TYPE_CHECKING:
    from sapphire_flow.types.auth import AccessToken
    from sapphire_flow.types.ids import AccessTokenId, StationId, TenantId

# Docker secret mount point (docker-compose.yml `secrets.access_token_pepper`,
# mounted into the `api` service only — auth verification + the token CLI,
# which runs via `docker compose exec api`).
DEFAULT_ACCESS_TOKEN_PEPPER_PATH = Path("/run/secrets/access_token_pepper")
_ENV_VAR = "ACCESS_TOKEN_PEPPER"  # noqa: S105 - env var NAME, not a secret value

# Raw key format: `key_prefix` (fast lookup) + `.` + the high-entropy secret.
_PREFIX_NBYTES = 6  # 8 url-safe base64 chars
_SECRET_NBYTES = 32


class PepperNotConfiguredError(ConfigurationError):
    """Fail-closed: no fallback to an unpeppered hash (R1)."""


def load_access_token_pepper(*, secret_path: Path | None = None) -> str:
    """Read the server-side pepper from a Docker secret file.

    Falls back to the `ACCESS_TOKEN_PEPPER` env var when the file is absent
    (local dev only, matching `load_recap_api_key`). Raises
    `PepperNotConfiguredError` — never returns an empty/placeholder pepper —
    when neither source is available or the file is unreadable/empty.
    """
    path = secret_path if secret_path is not None else DEFAULT_ACCESS_TOKEN_PEPPER_PATH
    if path.is_file():
        try:
            value = path.read_text().strip()
        except OSError as exc:
            raise PepperNotConfiguredError(
                f"access_token_pepper secret at {path} is unreadable: {exc}"
            ) from exc
        if value:
            return value
    env_value = os.environ.get(_ENV_VAR)
    if env_value is not None:
        # Strip + reject whitespace-only: `ACCESS_TOKEN_PEPPER="   "` is NOT a
        # pepper (fail-closed, matching the file branch above).
        env_value = env_value.strip()
        if env_value:
            return env_value
    raise PepperNotConfiguredError(
        f"access_token_pepper not found: neither {path} nor {_ENV_VAR} is set "
        "(fail-closed — no unpeppered fallback)"
    )


def hash_token(raw_secret: str, *, pepper: str) -> str:
    """HMAC-SHA-256(pepper, raw_secret) hex digest — R1 LOCKED."""
    return hmac.new(
        pepper.encode("utf-8"), raw_secret.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def generate_raw_token() -> tuple[str, str, str]:
    """Mint a new raw key. Returns (raw_key, key_prefix, raw_secret) — the
    caller hashes `raw_secret` with the pepper and stores `key_prefix` +
    the hash; only `raw_key` (prefix + secret) is ever shown to the operator,
    once, at creation time."""
    key_prefix = secrets.token_urlsafe(_PREFIX_NBYTES)
    raw_secret = secrets.token_urlsafe(_SECRET_NBYTES)
    raw_key = f"{key_prefix}.{raw_secret}"
    return raw_key, key_prefix, raw_secret


def split_raw_token(raw_key: str) -> tuple[str, str] | None:
    """Split a presented bearer key into (key_prefix, raw_secret), or None
    if malformed."""
    if "." not in raw_key:
        return None
    key_prefix, _, raw_secret = raw_key.partition(".")
    if not key_prefix or not raw_secret:
        return None
    return key_prefix, raw_secret


@dataclass(frozen=True, kw_only=True, slots=True)
class Principal:
    """The resolved auth context for a request — an API-boundary construct,
    not a persisted domain type (compare `types.auth.AccessToken`, the row
    it is resolved from)."""

    token_id: AccessTokenId
    role: AccessTokenRole
    tenant_id: TenantId | None
    station_ids: frozenset[StationId]

    @property
    def is_admin(self) -> bool:
        return self.role is AccessTokenRole.ADMIN

    def station_in_scope(self, station_id: StationId | None) -> bool:
        """G4/R2: admin sees everything; a consumer sees only its scoped
        stations — a null (stationless) station_id is NEVER in a consumer's
        scope (fail-closed, F7)."""
        if self.is_admin:
            return True
        if station_id is None:
            return False
        return station_id in self.station_ids


_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Missing or invalid access token",
    headers={"WWW-Authenticate": "Bearer"},
)


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value


def require_principal(
    request: Request,
    conn: sa.Connection = Depends(get_connection),
) -> Principal:
    """FastAPI dependency: Bearer header -> Principal, or 401.

    Uses the request's SINGLE RW-capable connection (`get_connection`) for
    BOTH the token lookup AND the `last_used_at` write — one connection per
    request (`042:105-111`), never a second. `get_connection` is now
    transactional (`engine.begin()`), so the `last_used_at` update (security's
    "updated on every authenticated request" contract, for inactive-key
    monitoring) commits with the request. The same connection is reused
    downstream by every route handler's stores.

    Fail-closed on a corrupt stored scope: if loading the token re-validates
    its station scope against its tenant and finds a cross-tenant row
    (`CrossTenantScopeError`), the request is rejected 401 — a scope row
    introduced out-of-band never becomes an authorized principal scope.
    """
    raw_key = _extract_bearer(request)
    if raw_key is None:
        raise _UNAUTHORIZED
    parts = split_raw_token(raw_key)
    if parts is None:
        raise _UNAUTHORIZED
    key_prefix, raw_secret = parts

    pepper: str = request.app.state.access_token_pepper
    store = PgAccessTokenStore(conn)
    try:
        token: AccessToken | None = store.fetch_by_key_prefix(key_prefix)
    except CrossTenantScopeError:
        # Corrupt/out-of-band cross-tenant scope row — fail closed, never
        # authorize the cross-tenant station.
        raise _UNAUTHORIZED from None
    if token is None:
        raise _UNAUTHORIZED

    candidate_hash = hash_token(raw_secret, pepper=pepper)
    if not hmac.compare_digest(candidate_hash, token.token_hash):
        raise _UNAUTHORIZED

    now = datetime.now(UTC)
    if token.disabled_at is not None:
        raise _UNAUTHORIZED
    if token.expires_at <= now:
        raise _UNAUTHORIZED

    conn.execute(
        sa.update(access_tokens)
        .where(access_tokens.c.id == token.id)
        .values(last_used_at=now)
    )

    return Principal(
        token_id=token.id,
        role=token.role,
        tenant_id=token.tenant_id,
        station_ids=token.station_ids,
    )


def require_admin(principal: Principal = Depends(require_principal)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin role required")
    return principal


def ensure_station_in_scope(principal: Principal, station_id: StationId | None) -> None:
    """404 (not 403) on an out-of-scope station — R2: do not reveal
    existence of stations outside the caller's scope."""
    if not principal.station_in_scope(station_id):
        raise HTTPException(status_code=404, detail="Station not found")
