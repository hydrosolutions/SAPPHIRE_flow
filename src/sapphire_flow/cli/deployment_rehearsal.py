"""Guarded checks inside disposable, ownership-verified rehearsal containers."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prefect.client.orchestration import PrefectClient

POOLS = ("default", "ingest", "backup")
JOBS = {"forecast-cycle": "default", "backup-database": "backup"}
PREFECT_URL = "http://prefect-server:4200/api"
API_URL = "http://caddy:80"
MARKER = "SAPPHIRE_REHEARSAL_RUN_ID"


class RehearsalCheckError(RuntimeError):
    pass


def require_environment(environment: Mapping[str, str]) -> str:
    marker = environment.get(MARKER, "")
    if (
        not re.fullmatch(r"[0-9a-f]{32}", marker)
        or environment.get("PREFECT_API_URL") != PREFECT_URL
    ):
        raise RehearsalCheckError("helper_environment_refused")
    return marker


async def pools(client: PrefectClient, action: Literal["create", "verify"]) -> None:
    inventory = await client.read_work_pools(limit=4)
    if action == "create":
        if inventory:
            raise RehearsalCheckError("existing_pools_refused")
        from prefect.client.schemas.actions import WorkPoolCreate

        for name in POOLS:
            await client.create_work_pool(
                WorkPoolCreate(name=name, type="process", is_paused=True)
            )
        inventory = await client.read_work_pools(limit=4)
    if {item.name for item in inventory} != set(POOLS) or len(inventory) != 3:
        raise RehearsalCheckError("pool_inventory_invalid")
    for name in POOLS:
        item = await client.read_work_pool(name)
        if item.type != "process" or item.is_paused is not True:
            raise RehearsalCheckError("pool_pause_not_confirmed")


async def no_execution(client: PrefectClient) -> None:
    from prefect.client.schemas.objects import StateType

    runs = await client.read_flow_runs(limit=101)
    if len(runs) > 100:
        raise RehearsalCheckError("flow_inventory_overflow")
    for run in runs:
        history = await client.read_flow_run_states(run.id)
        if len(history) > 1000:
            raise RehearsalCheckError("flow_history_overflow")
        states = [run.state, *history]
        if run.start_time is not None or any(
            state is None or state.type not in {StateType.SCHEDULED, StateType.PENDING}
            for state in states
        ):
            raise RehearsalCheckError("unexpected_flow_execution")


async def jobs(client: PrefectClient, action: Literal["pause", "verify"]) -> None:
    await pools(client, "verify")
    inventory = await client.read_deployments(limit=3)
    if (
        len(inventory) != 2
        or {item.name: item.work_pool_name for item in inventory} != JOBS
    ):
        raise RehearsalCheckError("deployment_inventory_invalid")
    for deployment in inventory:
        schedules = await client.read_deployment_schedules(deployment.id)
        if not schedules or len(schedules) > 4:
            raise RehearsalCheckError("schedule_inventory_invalid")
        if action == "pause":
            for schedule in schedules:
                await client.update_deployment_schedule(
                    deployment.id, schedule.id, active=False
                )
        readback = await client.read_deployment_schedules(deployment.id)
        if {item.id for item in readback} != {item.id for item in schedules} or any(
            item.active for item in readback
        ):
            raise RehearsalCheckError("schedule_pause_not_confirmed")
    await pools(client, "verify")
    await no_execution(client)


async def workers(client: PrefectClient, now: datetime) -> None:
    await jobs(client, "verify")
    for name in POOLS:
        inventory = await client.read_workers_for_work_pool(name, limit=11)
        if len(inventory) != 1:
            raise RehearsalCheckError("workers_not_ready")
        heartbeat = inventory[0].last_heartbeat_time
        if heartbeat is None or not -5 <= (now - heartbeat).total_seconds() <= 90:
            raise RehearsalCheckError("workers_not_ready")
    await pools(client, "verify")
    await no_execution(client)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def request(path: str, token: str | None = None) -> tuple[int, bytes]:
    if path not in {"/api/v1/health", "/api/v1/stations"}:
        raise RehearsalCheckError("api_path_refused")
    headers = {"Authorization": "Bearer " + token} if token else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(
            urllib.request.Request(API_URL + path, headers=headers), timeout=10
        ) as response:
            return response.status, response.read(65537)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(65537)


def api(now: datetime) -> None:
    import sqlalchemy as sa

    from sapphire_flow.api.security import load_access_token_pepper
    from sapphire_flow.cli.access_tokens import create_token
    from sapphire_flow.config.deployment_identity import load_deployment_identity_config
    from sapphire_flow.db.engine import create_engine_from_env
    from sapphire_flow.store.tenant_store import PgTenantStore
    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.enums import AccessTokenRole

    url = sa.engine.make_url(os.environ.get("DATABASE_URL", ""))
    if (url.host, url.database, url.username) != (
        "postgres",
        "sapphire",
        "sapphire_api",
    ):
        raise RehearsalCheckError("api_database_refused")
    identity = load_deployment_identity_config("/app/config.toml")
    if identity.global_admin or len(identity.writable_tenants) != 1:
        raise RehearsalCheckError("api_tenant_refused")
    if request("/api/v1/health")[0] != 200 or request("/api/v1/stations")[0] != 401:
        raise RehearsalCheckError("api_unauthenticated_check_failed")
    tenant_code = next(iter(identity.writable_tenants))
    engine = create_engine_from_env()
    try:
        with engine.begin() as conn:
            tenant = PgTenantStore(conn).fetch_tenant_by_code(tenant_code)
            if tenant is None:
                raise RehearsalCheckError("api_tenant_missing")
            raw_token = create_token(
                conn,
                name="disposable-rehearsal",
                role=AccessTokenRole.CONSUMER,
                tenant_id=tenant.id,
                tenant_code=tenant_code,
                station_ids=frozenset(),
                expires_at=ensure_utc(now + timedelta(minutes=10)),
                now=ensure_utc(now),
                pepper=load_access_token_pepper(),
            )
        status, body = request("/api/v1/stations", raw_token)
        if status != 200 or len(body) > 65536:
            raise RehearsalCheckError("api_authenticated_check_failed")
        result = json.loads(body)
        if (
            result.get("items") != []
            or result.get("total") != 0
            or result.get("limit") != 50
            or result.get("offset") != 0
        ):
            raise RehearsalCheckError("api_collection_not_empty")
    finally:
        engine.dispose()


async def check(action: str, now: datetime) -> None:
    from prefect.client.orchestration import get_client

    require_environment(os.environ)
    if action == "api":
        api(now)
    async with get_client() as client:
        if action == "prepare-pools":
            await pools(client, "create")
        elif action == "pause-jobs":
            await jobs(client, "pause")
        elif action == "workers":
            await workers(client, now)
        elif action == "api":
            await jobs(client, "verify")
        else:
            raise RehearsalCheckError("helper_action_refused")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("prepare-pools", "pause-jobs", "workers", "api")
    )
    args = parser.parse_args(argv)
    # Dependency logs and exceptions never become a token/config output channel.
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        try:
            asyncio.run(check(args.action, datetime.now(UTC)))
            result = {"status": "ok", "stage": args.action}
        except Exception as exc:  # Boundary: never print dependency exception text.
            result = {
                "status": "failed",
                "code": str(exc)
                if isinstance(exc, RehearsalCheckError)
                else "helper_check_failed",
            }
    sys.stdout.write(json.dumps(result) + "\n")
    return 0  # The host consumes the fixed JSON status, including failures.


if __name__ == "__main__":
    raise SystemExit(main())
