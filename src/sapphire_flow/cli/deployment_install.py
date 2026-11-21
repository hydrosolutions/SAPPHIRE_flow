"""Guarded checks for an owned persistent installation's paused phase."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

from sapphire_flow.cli.deployment_checks import (
    PREFECT_URL,
    DeploymentCheckError,
    jobs,
    pools,
    workers,
)
from sapphire_flow.cli.deployment_rehearsal import request

MARKER = "SAPPHIRE_INSTALLATION_ID"
ACTIONS = ("prepare-pools", "pause-jobs", "verify-state", "workers", "api")


def require_environment(environment: Mapping[str, str]) -> None:
    if (
        not re.fullmatch(r"[0-9a-f]{32}", environment.get(MARKER, ""))
        or environment.get("PREFECT_API_URL") != PREFECT_URL
        or environment.get("SAPPHIRE_REHEARSAL_RUN_ID")
    ):
        raise DeploymentCheckError("helper_environment_refused")


def api(now: datetime, *, id_gen: Callable[[], UUID] = uuid4) -> None:
    import sqlalchemy as sa

    from sapphire_flow.api.security import load_access_token_pepper
    from sapphire_flow.cli.access_tokens import create_token, revoke_token
    from sapphire_flow.config.deployment_identity import load_deployment_identity_config
    from sapphire_flow.db.engine import create_engine_from_env
    from sapphire_flow.store.access_token_store import PgAccessTokenStore
    from sapphire_flow.store.tenant_store import PgTenantStore
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.enums import AccessTokenRole
    from sapphire_flow.types.ids import AccessTokenId

    url = sa.engine.make_url(os.environ.get("DATABASE_URL", ""))
    if (url.host, url.database, url.username) != (
        "postgres",
        "sapphire",
        "sapphire_api",
    ):
        raise DeploymentCheckError("api_database_refused")
    identity = load_deployment_identity_config("/app/config.toml")
    if identity.global_admin or len(identity.writable_tenants) != 1:
        raise DeploymentCheckError("api_tenant_refused")
    if request("/api/v1/health")[0] != 200 or request("/api/v1/stations")[0] != 401:
        raise DeploymentCheckError("api_unauthenticated_check_failed")
    tenant_code = next(iter(identity.writable_tenants))
    token_id = AccessTokenId(id_gen())
    raw_token: str | None = None
    engine = create_engine_from_env()
    try:
        with engine.begin() as conn:
            tenant = PgTenantStore(conn).fetch_tenant_by_code(tenant_code)
            if tenant is None:
                raise DeploymentCheckError("api_tenant_missing")
            generated = create_token(
                conn,
                name="installation-probe",
                role=AccessTokenRole.CONSUMER,
                tenant_id=tenant.id,
                tenant_code=tenant_code,
                station_ids=frozenset(),
                expires_at=ensure_utc(now + timedelta(minutes=10)),
                now=ensure_utc(now),
                pepper=load_access_token_pepper(),
                id_gen=lambda: token_id,
            )
        raw_token = generated
        status, body = request("/api/v1/stations", raw_token)
        if status != 200 or len(body) > 65536:
            raise DeploymentCheckError("api_authenticated_check_failed")
        result = json.loads(body)
        if result.get("items") != [] or result.get("total") != 0:
            raise DeploymentCheckError("api_scope_check_failed")
    finally:
        try:
            if raw_token is not None:
                with engine.begin() as conn:
                    revoke_token(conn, token_id=token_id, now=ensure_utc(now))
                with engine.connect() as conn:
                    saved = PgAccessTokenStore(conn).fetch_token(token_id)
                    if saved is None or saved.disabled_at is None:
                        raise DeploymentCheckError("probe_token_revocation_failed")
                if request("/api/v1/stations", raw_token)[0] != 401:
                    raise DeploymentCheckError("probe_token_revocation_failed")
        finally:
            engine.dispose()


async def check(action: str, now: datetime) -> None:
    from prefect.client.orchestration import get_client

    require_environment(os.environ)
    async with get_client() as client:
        match action:
            case "prepare-pools":
                await pools(client, "create")
            case "pause-jobs":
                await jobs(client, "pause")
            case "verify-state":
                await jobs(client, "verify")
            case "workers":
                await workers(client, now)
            case "api":
                await jobs(client, "verify")
                api(now)
                await jobs(client, "verify")
            case _:
                raise DeploymentCheckError("helper_action_refused")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=ACTIONS)
    args = parser.parse_args(argv)
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        try:
            asyncio.run(check(args.action, datetime.now(UTC)))
            result = {"status": "ok", "stage": args.action}
        except Exception as exc:
            result = {
                "status": "failed",
                "code": str(exc)
                if isinstance(exc, DeploymentCheckError)
                else "helper_check_failed",
            }
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
