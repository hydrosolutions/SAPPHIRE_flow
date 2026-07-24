# pyright: reportUnknownMemberType=false
"""Plan 147 Slice C: access-token CLI management (`042:69`, trimmed to
`create`/`list`/`revoke` + a `create-admin` bootstrap for v1.0 — in-place
`rotate`/`scope`-edit are deferred to v1.x; rotation = revoke + create).

Run via: docker compose exec api python -m sapphire_flow.cli.access_tokens <command> ...
(needs DATABASE_URL + the access_token_pepper secret mounted — the same `api`
service the CLI shares with the running API, per `security.md` bootstrap).

Token create/revoke and their `audit_log` insert share ONE RW transaction
(Slice B atomicity rule) — a failed audit insert rolls back the token write.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from sqlalchemy.exc import IntegrityError

from sapphire_flow.api.security import (
    generate_raw_token,
    hash_token,
    load_access_token_pepper,
)
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.store.access_token_store import PgAccessTokenStore
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AccessToken, AuditEntry
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AccessTokenRole, AuditEventType
from sapphire_flow.types.ids import AccessTokenId, StationId

if TYPE_CHECKING:
    from collections.abc import Callable

    import sqlalchemy as sa

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
        f"tenant={t.tenant_id or '-':36}  {status:8}  "
        f"expires={t.expires_at.isoformat()}  scope={len(t.station_ids)} station(s)"
    )


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

    args = parser.parse_args(argv)

    from sapphire_flow.logging import configure_cli_logging

    configure_cli_logging()

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

    # create / create-admin
    pepper = load_access_token_pepper()
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
