"""Plan 404 T3 — `require_reviewer_or_human`: the gate for a route that
admits BOTH principal kinds — a reviewer/admin service token (Plan 401) or a
named human with a current station `review` grant (Plan 341). The two
principals use different dependencies (`api/security.py::require_reviewer`
accepts service-token principals only; `api/human_auth.py
::require_human_principal` accepts OIDC humans only), so this route is
registered OUTSIDE Plan 402's `require_reviewer` router, with its own
route-level dependency here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request

from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.human_auth import require_human_principal
from sapphire_flow.api.security import Principal, require_principal, require_reviewer

if TYPE_CHECKING:
    from sapphire_flow.types.human_auth import HumanPrincipal

# Textually identical to `api/security.py`'s own `_UNAUTHORIZED` (same detail
# and header) — every authentication failure on this route reads the same,
# whichever verifier ran, or neither (D4/T3): a malformed bearer, a bad or
# expired service token, a bad/deactivated human token, and an OIDC-shaped
# bearer while human authentication is disabled all collapse to this one
# body. The route never surfaces `require_human_principal`'s 503, so an
# unauthenticated caller learns nothing about whether human auth is
# configured.
_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Missing or invalid access token",
    headers={"WWW-Authenticate": "Bearer"},
)


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def require_reviewer_or_human(
    request: Request,
    conn: sa.Connection = Depends(get_connection),
) -> Principal | HumanPrincipal:
    """The bearer's SHAPE decides which verifier runs, with NO fallback to
    the other after a failure: exactly one `.` is a service token
    (`prefix.secret`, both `secrets.token_urlsafe` output, which never
    contains a dot itself — `api/security.py::generate_raw_token`); exactly
    two `.` (three JWT segments) is an OIDC access token; any other shape is
    refused WITHOUT calling either verifier."""
    token = _bearer_token(request)
    if token is None:
        raise _UNAUTHORIZED
    dots = token.count(".")
    if dots == 1:
        # Called directly (not via a second `Depends`): a service token
        # never reaches the OIDC verifier. `require_principal`'s own 401
        # already carries this exact body; a role mismatch (e.g. a consumer
        # token) still raises `require_reviewer`'s 403, untouched.
        return require_reviewer(require_principal(request, conn))
    if dots == 2:
        # An OIDC token never reaches the access-token lookup. Every
        # failure — a bad/expired/deactivated token, OR human auth disabled
        # (503) — collapses to the SAME 401 this route always returns.
        try:
            return require_human_principal(request, conn)
        except HTTPException:
            raise _UNAUTHORIZED from None
    raise _UNAUTHORIZED
