# pyright: reportUnknownMemberType=false
"""Plan 147 Slice C + Plan 215: access-token CLI management (`042:69`,
`create`/`list`/`revoke` + a `create-admin` bootstrap, plus Plan 215's
`grant`/`revoke-station`/`show`/`set-scope-mode` — a token's station scope
now has a supported lifecycle; in-place edit is no longer deferred to v1.x.
In-place `rotate` (key rotation) is still deferred to v1.x.

Run via (note the `/entrypoint.sh` wrapper — REQUIRED):

    docker compose exec api /entrypoint.sh \
        python -m sapphire_flow.cli.access_tokens <command> ...

`docker compose exec` bypasses the image ENTRYPOINT, so `DATABASE_URL` (which
`entrypoint.sh` builds from the mounted DB-password secret) is otherwise unset
and the CLI raises `KeyError: 'DATABASE_URL'`. The access_token_pepper secret is
mounted into the same `api` service, per `security.md` bootstrap.

Every write subcommand (create/revoke/grant/revoke-station/set-scope-mode) and
its `audit_log` insert share ONE RW transaction (Slice B atomicity rule) — a
failed audit insert rolls back the whole change.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from sqlalchemy.exc import IntegrityError

from sapphire_flow.api.security import (
    generate_raw_token,
    hash_token,
    load_access_token_pepper,
)
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.db.metadata import access_token_stations, access_tokens, stations
from sapphire_flow.store.access_token_store import PgAccessTokenStore
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AccessToken, AuditEntry
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole, AuditEventType, ScopeMode
from sapphire_flow.types.ids import AccessTokenId, StationId

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import TenantId

log = structlog.get_logger(__name__)

DEFAULT_EXPIRES_DAYS = 365
# `key_prefix` is now DB-unique (alembic 0047 fixer round) — a collision on
# mint is astronomically unlikely (48-bit prefix) but must not crash token
# creation; retry with a freshly generated prefix a bounded number of times.
_MAX_KEY_PREFIX_COLLISION_RETRIES = 5


def _resolve_tenant(conn: sa.Connection, tenant_code: str | None) -> TenantId | None:
    if tenant_code is None:
        return None
    tenant = PgTenantStore(conn).fetch_tenant_by_code(tenant_code)
    if tenant is None:
        raise SystemExit(f"unknown tenant code: {tenant_code!r}")
    return tenant.id


def _is_key_prefix_collision(exc: IntegrityError) -> bool:
    orig = exc.orig
    diag = getattr(orig, "diag", None)
    constraint_name = (
        getattr(diag, "constraint_name", None) if diag is not None else None
    )
    if constraint_name:
        return "key_prefix" in constraint_name
    return "key_prefix" in str(orig)


def create_token(
    conn: sa.Connection,
    *,
    name: str,
    role: AccessTokenRole,
    tenant_id: TenantId | None,
    tenant_code: str | None,
    station_ids: frozenset[StationId],
    expires_at: UtcDatetime,
    now: UtcDatetime,
    pepper: str,
    id_gen: Callable[[], UUID] = uuid4,
    token_generator: Callable[[], tuple[str, str, str]] = generate_raw_token,
) -> str:
    """Create a token row + its `API_KEY_CREATED` audit row atomically.

    Returns the raw key — shown to the operator ONCE, never persisted.

    Retries `token_generator` (default `generate_raw_token`) on a
    `key_prefix` collision — the DB-unique index (alembic 0047) is the
    source of truth; a failed insert attempt is rolled back to a SAVEPOINT
    so the outer (CLI-owned) transaction stays usable for the retry and the
    subsequent audit-log insert.
    """
    store = PgAccessTokenStore(conn)
    token_id = AccessTokenId(id_gen())
    raw_key = ""
    last_exc: IntegrityError | None = None
    for attempt in range(_MAX_KEY_PREFIX_COLLISION_RETRIES):
        raw_key, key_prefix, raw_secret = token_generator()
        token_hash = hash_token(raw_secret, pepper=pepper)
        token = AccessToken(
            id=token_id,
            token_hash=token_hash,
            key_prefix=key_prefix,
            name=name,
            role=role,
            tenant_id=tenant_id,
            pepper_version=1,
            expires_at=expires_at,
            disabled_at=None,
            created_at=now,
            last_used_at=None,
            station_ids=station_ids,
        )
        try:
            with conn.begin_nested():
                store.create_token(token, station_ids=station_ids)
        except IntegrityError as exc:
            if not _is_key_prefix_collision(exc):
                raise
            last_exc = exc
            log.warning(
                "access_token.key_prefix_collision",
                attempt=attempt,
                key_prefix=key_prefix,
            )
            continue
        break
    else:
        raise RuntimeError(
            f"exhausted {_MAX_KEY_PREFIX_COLLISION_RETRIES} key_prefix "
            "collision retries while creating an access token"
        ) from last_exc

    entry = AuditEntry.system(
        event_type=AuditEventType.API_KEY_CREATED,
        target_type="access_token",
        target_id=str(token_id),
        detail={
            "name": name,
            "role": role.value,
            "tenant_code": tenant_code,
            "station_count": len(station_ids),
        },
        ip_address=None,
        created_at=now,
    )
    PgAuditLogStore(conn).append_entry(entry)
    return raw_key


def revoke_token(
    conn: sa.Connection, *, token_id: AccessTokenId, now: UtcDatetime
) -> None:
    store = PgAccessTokenStore(conn)
    existing = store.fetch_token(token_id)
    if existing is None:
        raise SystemExit(f"no such access token: {token_id}")
    store.revoke_token(token_id, revoked_at=now)
    entry = AuditEntry.system(
        event_type=AuditEventType.API_KEY_REVOKED,
        target_type="access_token",
        target_id=str(token_id),
        detail={"name": existing.name},
        ip_address=None,
        created_at=now,
    )
    PgAuditLogStore(conn).append_entry(entry)


def list_tokens(conn: sa.Connection) -> list[AccessToken]:
    return PgAccessTokenStore(conn).fetch_all_tokens()


def _print_token_row(t: AccessToken) -> None:
    status = "disabled" if t.disabled_at is not None else "active"
    print(  # noqa: T201 - CLI output, not application logging
        f"{t.id}  {t.name!r:30}  role={t.role.value:8}  "
        f"tenant={t.tenant_id or '-'!s:36}  {status:8}  "
        f"expires={t.expires_at.isoformat()}  scope_mode={t.scope_mode.value:8}  "
        f"scope={len(t.station_ids)} station(s)"
    )


def _refuse_admin_scope_change(existing: AccessToken, *, verb: str) -> None:
    if existing.role is AccessTokenRole.ADMIN:
        raise SystemExit(
            f"cannot {verb} on an admin token — admin is always "
            "unscoped/global and never carries a station scope (G4)"
        )


def grant_station(
    conn: sa.Connection,
    *,
    token_id: AccessTokenId,
    station_id: StationId,
    now: UtcDatetime,
) -> str:
    """Plan 215 T1: widen a token's scope by one station. Idempotent — a
    repeat grant is a no-op (`ON CONFLICT DO NOTHING`, store layer)."""
    store = PgAccessTokenStore(conn)
    existing = store.fetch_token(token_id)
    if existing is None:
        raise SystemExit(f"no such access token: {token_id}")
    _refuse_admin_scope_change(existing, verb="grant a station scope")
    if existing.scope_mode is ScopeMode.TENANT:
        raise SystemExit(
            "this token is in 'tenant' scope mode — its scope already "
            "follows every station in its tenant, so `grant` has nothing "
            "to add. Use `set-scope-mode <token-id> stations` first if you "
            "want an explicit per-station grant list instead."
        )
    store.grant_station(token_id, station_id, tenant_id=existing.tenant_id)
    entry = AuditEntry.system(
        event_type=AuditEventType.API_KEY_SCOPE_CHANGED,
        target_type="access_token",
        target_id=str(token_id),
        detail={"action": "grant", "station_id": str(station_id)},
        ip_address=None,
        created_at=now,
    )
    PgAuditLogStore(conn).append_entry(entry)
    return f"granted station {station_id} to token {token_id}"


def revoke_station(
    conn: sa.Connection,
    *,
    token_id: AccessTokenId,
    station_id: StationId,
    now: UtcDatetime,
) -> str:
    """Plan 215 T1: narrow a token's scope by one station. Idempotent —
    revoking a station already out of scope succeeds and reports as much,
    rather than claiming a change that did not happen."""
    store = PgAccessTokenStore(conn)
    existing = store.fetch_token(token_id)
    if existing is None:
        raise SystemExit(f"no such access token: {token_id}")
    _refuse_admin_scope_change(existing, verb="revoke a station scope")
    if existing.scope_mode is ScopeMode.TENANT:
        raise SystemExit(
            "this token is in 'tenant' scope mode — there is no per-station "
            "grant to revoke; its scope is the whole tenant. Use "
            "`set-scope-mode <token-id> stations` to leave tenant mode "
            "first (this empties its scope — see that command's output)."
        )
    was_in_scope = store.revoke_station(token_id, station_id)
    entry = AuditEntry.system(
        event_type=AuditEventType.API_KEY_SCOPE_CHANGED,
        target_type="access_token",
        target_id=str(token_id),
        detail={
            "action": "revoke_station",
            "station_id": str(station_id),
            "was_in_scope": was_in_scope,
        },
        ip_address=None,
        created_at=now,
    )
    PgAuditLogStore(conn).append_entry(entry)
    if was_in_scope:
        return f"revoked station {station_id} from token {token_id}"
    return f"station {station_id} was not in scope for token {token_id}"


def show_token(conn: sa.Connection, *, token_id: AccessTokenId) -> str:
    """Plan 215 T2: read-only. Prints station UUID + `network/code` per
    in-scope station so the output round-trips straight into
    `grant`/`revoke-station`; a `tenant`-mode token's listing is labelled
    as derived at load time, not a materialised grant list."""
    store = PgAccessTokenStore(conn)
    token = store.fetch_token(token_id)
    if token is None:
        raise SystemExit(f"no such access token: {token_id}")

    status = "disabled" if token.disabled_at is not None else "active"
    lines = [
        f"id:          {token.id}",
        f"name:        {token.name!r}",
        f"role:        {token.role.value}",
        f"tenant:      {token.tenant_id or '-'}",
        f"status:      {status}",
        f"expires_at:  {token.expires_at.isoformat()}",
        f"scope_mode:  {token.scope_mode.value}",
    ]
    if token.role is AccessTokenRole.ADMIN:
        lines.append("scope:       unscoped (admin sees every station)")
        return "\n".join(lines)

    if token.scope_mode is ScopeMode.TENANT:
        lines.append(
            f"scope:       DERIVED at load time from tenant membership — "
            f"{len(token.station_ids)} station(s) currently"
        )
    else:
        lines.append(f"scope:       {len(token.station_ids)} station(s) granted")

    if token.station_ids:
        rows = (
            conn.execute(
                sa.select(stations.c.id, stations.c.network, stations.c.code)
                .where(stations.c.id.in_(token.station_ids))
                .order_by(stations.c.network, stations.c.code)
            )
            .mappings()
            .all()
        )
        lines.extend(f"  {row['id']}  {row['network']}/{row['code']}" for row in rows)

    return "\n".join(lines)


def set_scope_mode(
    conn: sa.Connection,
    *,
    token_id: AccessTokenId,
    target_mode: ScopeMode,
    now: UtcDatetime,
    confirmed: bool,
) -> str:
    """Plan 215 T6: change a token's `scope_mode`. `stations -> tenant`
    deletes the now-obsolete materialised grant rows in the SAME
    transaction as the mode flip and the audit write (one `engine.begin()`
    block, caller-owned) — a failed audit insert rolls back the whole
    change. `tenant -> stations` does NOT snapshot the tenant back into
    grants: the token's scope goes to empty and the caller re-grants with
    `grant` (a silent re-materialization is the drift this plan removes)."""
    store = PgAccessTokenStore(conn)
    existing = store.fetch_token(token_id)
    if existing is None:
        raise SystemExit(f"no such access token: {token_id}")
    _refuse_admin_scope_change(existing, verb="change scope mode")

    station_count = conn.execute(
        sa.select(sa.func.count())
        .select_from(stations)
        .where(stations.c.tenant_id == existing.tenant_id)
    ).scalar_one()

    if target_mode is ScopeMode.TENANT and not confirmed:
        raise SystemExit(
            "refusing to switch to 'tenant' scope mode without "
            "--yes-follow-the-whole-tenant — this token would follow all "
            f"{station_count} station(s) currently in its tenant, and every "
            "station added to the tenant afterward, with no further "
            "confirmation."
        )

    previous_mode = existing.scope_mode
    conn.execute(
        sa.update(access_tokens)
        .where(access_tokens.c.id == token_id)
        .values(scope_mode=target_mode.value)
    )
    deleted_grants = 0
    if target_mode is ScopeMode.TENANT and previous_mode is ScopeMode.STATIONS:
        result = conn.execute(
            sa.delete(access_token_stations).where(
                access_token_stations.c.token_id == token_id
            )
        )
        deleted_grants = result.rowcount

    entry = AuditEntry.system(
        event_type=AuditEventType.API_KEY_SCOPE_CHANGED,
        target_type="access_token",
        target_id=str(token_id),
        detail={
            "action": "set_scope_mode",
            "previous_scope_mode": previous_mode.value,
            "new_scope_mode": target_mode.value,
            "deleted_grant_count": deleted_grants,
        },
        ip_address=None,
        created_at=now,
    )
    PgAuditLogStore(conn).append_entry(entry)

    if target_mode is ScopeMode.TENANT:
        return (
            f"scope mode set to 'tenant' — token {token_id} now follows all "
            f"{station_count} station(s) in its tenant, and every station "
            f"added afterward; {deleted_grants} materialised grant row(s) "
            "deleted"
        )
    if previous_mode is ScopeMode.TENANT:
        return (
            f"scope mode set to 'stations' — token {token_id}'s scope is "
            "now EMPTY (the materialised grants were deleted when it "
            "entered tenant mode); re-grant stations with `grant`"
        )
    return f"scope mode set to 'stations' for token {token_id} (no change)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sapphire-access-tokens",
        description="Plan 147 Slice C: access-token CLI management.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a consumer access token.")
    p_create.add_argument("--name", required=True)
    p_create.add_argument(
        "--tenant", default=None, help="Tenant code (required for a consumer token)."
    )
    p_create.add_argument(
        "--station",
        action="append",
        default=[],
        dest="stations",
        help="Station UUID to scope this token to (repeatable).",
    )
    p_create.add_argument("--expires-days", type=int, default=DEFAULT_EXPIRES_DAYS)

    p_admin = sub.add_parser(
        "create-admin",
        help="Bootstrap/mint an unscoped admin token.",
        description=(
            "G4 LOCKED: admin is always unscoped/global — there is no "
            "tenant-bound admin variant, so this subcommand takes no "
            "--tenant flag (AccessToken.__post_init__ + the DB CHECK "
            "constraint both reject role=admin with a non-null tenant_id)."
        ),
    )
    p_admin.add_argument("--name", required=True)
    p_admin.add_argument("--expires-days", type=int, default=DEFAULT_EXPIRES_DAYS)

    sub.add_parser("list", help="List all access tokens.")

    p_revoke = sub.add_parser("revoke", help="Revoke an access token.")
    p_revoke.add_argument("token_id")

    p_show = sub.add_parser(
        "show", help="Show a token's scope in full (Plan 215 T2, read-only)."
    )
    p_show.add_argument("token_id")

    p_grant = sub.add_parser(
        "grant", help="Widen a token's scope by one station (Plan 215 T1)."
    )
    p_grant.add_argument("token_id")
    p_grant.add_argument("station_id")

    p_revoke_station = sub.add_parser(
        "revoke-station",
        help="Narrow a token's scope by one station (Plan 215 T1).",
    )
    p_revoke_station.add_argument("token_id")
    p_revoke_station.add_argument("station_id")

    p_scope_mode = sub.add_parser(
        "set-scope-mode",
        help="Change a token's scope_mode (Plan 215 T6).",
        description=(
            "'stations' (the default) reads access_token_stations, the "
            "normal grant list edited with grant/revoke-station. 'tenant' "
            "derives the scope from tenant membership at load time instead "
            "— no grant to edit, and a station added to the tenant later "
            "is in scope with no further action. Switching stations -> "
            "tenant DELETES the token's existing grant rows (now "
            "redundant); switching tenant -> stations leaves the scope "
            "EMPTY (it does not snapshot the tenant back into grants) — "
            "re-grant with `grant`."
        ),
    )
    p_scope_mode.add_argument("token_id")
    p_scope_mode.add_argument("mode", choices=["stations", "tenant"])
    p_scope_mode.add_argument(
        "--yes-follow-the-whole-tenant",
        action="store_true",
        dest="confirmed",
        help=(
            "Required to switch TO tenant mode — confirms you understand "
            "the token will follow every station in its tenant, present "
            "and future, with no further confirmation."
        ),
    )

    args = parser.parse_args(argv)

    from sapphire_flow.logging import configure_cli_logging

    configure_cli_logging()

    # Fail-closed for EVERY subcommand (Plan 147 Slice C, Codex round 2): the
    # token CLI refuses to run without a readable, non-empty pepper — loaded
    # BEFORE touching the DB so list/revoke also fail closed, not just create.
    pepper = load_access_token_pepper()

    engine = create_engine_from_env()
    now = ensure_utc(datetime.now(UTC))

    if args.command == "list":
        with engine.connect() as conn:
            for token in list_tokens(conn):
                _print_token_row(token)
        return 0

    if args.command == "revoke":
        token_id = AccessTokenId(UUID(args.token_id))
        with engine.begin() as conn:
            revoke_token(conn, token_id=token_id, now=now)
        log.info("access_token.revoked", token_id=str(token_id))
        return 0

    if args.command == "show":
        token_id = AccessTokenId(UUID(args.token_id))
        with engine.connect() as conn:
            print(show_token(conn, token_id=token_id))  # noqa: T201
        return 0

    if args.command == "grant":
        token_id = AccessTokenId(UUID(args.token_id))
        station_id = StationId(UUID(args.station_id))
        with engine.begin() as conn:
            summary = grant_station(
                conn, token_id=token_id, station_id=station_id, now=now
            )
        print(summary)  # noqa: T201
        log.info(
            "access_token.station_granted",
            token_id=str(token_id),
            station_id=str(station_id),
        )
        return 0

    if args.command == "revoke-station":
        token_id = AccessTokenId(UUID(args.token_id))
        station_id = StationId(UUID(args.station_id))
        with engine.begin() as conn:
            summary = revoke_station(
                conn, token_id=token_id, station_id=station_id, now=now
            )
        print(summary)  # noqa: T201
        log.info(
            "access_token.station_revoked",
            token_id=str(token_id),
            station_id=str(station_id),
        )
        return 0

    if args.command == "set-scope-mode":
        token_id = AccessTokenId(UUID(args.token_id))
        target_mode = ScopeMode(args.mode)
        with engine.begin() as conn:
            summary = set_scope_mode(
                conn,
                token_id=token_id,
                target_mode=target_mode,
                now=now,
                confirmed=args.confirmed,
            )
        print(summary)  # noqa: T201
        log.info(
            "access_token.scope_mode_changed",
            token_id=str(token_id),
            mode=target_mode.value,
        )
        return 0

    # create / create-admin (pepper already loaded + required above)
    role = (
        AccessTokenRole.ADMIN
        if args.command == "create-admin"
        else AccessTokenRole.CONSUMER
    )
    expires_at = ensure_utc(now + timedelta(days=args.expires_days))
    station_ids = frozenset(StationId(UUID(s)) for s in getattr(args, "stations", []))

    # G4 LOCKED: admin is always unscoped/global — `create-admin` has no
    # --tenant flag (see `AccessToken.__post_init__`), so `tenant_code` is
    # only ever read for a consumer token below.
    tenant_code = args.tenant if role is AccessTokenRole.CONSUMER else None
    if role is AccessTokenRole.CONSUMER and tenant_code is None:
        raise SystemExit("--tenant is required for a consumer token")

    with engine.begin() as conn:
        tenant_id = _resolve_tenant(conn, tenant_code)
        raw_key = create_token(
            conn,
            name=args.name,
            role=role,
            tenant_id=tenant_id,
            tenant_code=tenant_code,
            station_ids=station_ids,
            expires_at=expires_at,
            now=now,
            pepper=pepper,
        )

    print(  # noqa: T201 - the raw key is shown ONCE, never persisted/logged
        f"Access token created ({role.value}). Store it now — it will not "
        f"be shown again:\n\n{raw_key}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
