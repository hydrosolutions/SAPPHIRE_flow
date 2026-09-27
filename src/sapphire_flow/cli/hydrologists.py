# pyright: reportUnknownMemberType=false
"""Host-operator management of locally granted CHWRR human identities."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.human_identity_store import PgHumanIdentityStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import AuditEventType
from sapphire_flow.types.human_auth import HumanPermission
from sapphire_flow.types.ids import StationId, TenantId, UserId

if TYPE_CHECKING:
    import sqlalchemy as sa


def _audit(
    conn: sa.Connection,
    *,
    event: AuditEventType,
    user_id: UserId,
    operator: str,
    detail: dict[str, str],
    now: UtcDatetime,
) -> None:
    PgAuditLogStore(conn).append_entry(
        AuditEntry.system(
            event_type=event,
            target_type="user",
            target_id=str(user_id),
            detail={"operator": operator, **detail},
            ip_address=None,
            created_at=now,
        )
    )


def _require_operator(operator: str) -> None:
    if not operator.strip():
        raise ValueError("non-empty operator handle is required")


def create_hydrologist(
    conn: sa.Connection,
    *,
    tenant_code: str,
    display_name: str,
    issuer: str,
    subject: str,
    operator: str,
    now: UtcDatetime,
    user_id: UserId,
) -> UserId:
    _require_operator(operator)
    tenant = PgTenantStore(conn).fetch_tenant_by_code(tenant_code)
    if tenant is None:
        raise ValueError("unknown tenant")
    PgHumanIdentityStore(conn).create_user(
        user_id=user_id,
        tenant_id=TenantId(tenant.id),
        display_name=display_name,
        issuer=issuer,
        subject=subject,
        now=now,
    )
    _audit(
        conn,
        event=AuditEventType.USER_CREATED,
        user_id=user_id,
        operator=operator,
        detail={"tenant_id": str(tenant.id), "issuer": issuer},
        now=now,
    )
    return user_id


def change_hydrologist_grant(
    conn: sa.Connection,
    *,
    user_id: UserId,
    station_id: StationId,
    permission: HumanPermission,
    action: Literal["grant", "revoke"],
    operator: str,
    now: UtcDatetime,
) -> bool:
    _require_operator(operator)
    store = PgHumanIdentityStore(conn)
    if action == "grant":
        changed = store.grant(user_id, station_id, permission, now=now)
    elif action == "revoke":
        changed = store.revoke(user_id, station_id, permission)
    else:
        raise ValueError("grant action must be grant or revoke")
    if changed:
        _audit(
            conn,
            event=AuditEventType.HUMAN_GRANT_CHANGED,
            user_id=user_id,
            operator=operator,
            detail={
                "action": action,
                "station_id": str(station_id),
                "permission": permission.value,
                "also_revoked_publish": str(
                    action == "revoke" and permission is HumanPermission.REVIEW
                ).lower(),
            },
            now=now,
        )
    return changed


def disable_hydrologist(
    conn: sa.Connection, *, user_id: UserId, operator: str, now: UtcDatetime
) -> None:
    _require_operator(operator)
    PgHumanIdentityStore(conn).disable_user(user_id, now=now)
    _audit(
        conn,
        event=AuditEventType.USER_DEACTIVATED,
        user_id=user_id,
        operator=operator,
        detail={},
        now=now,
    )


def link_hydrologist_identity(
    conn: sa.Connection,
    *,
    user_id: UserId,
    issuer: str,
    subject: str,
    operator: str,
    now: UtcDatetime,
) -> None:
    _require_operator(operator)
    PgHumanIdentityStore(conn).link_identity(
        user_id, issuer=issuer, subject=subject, now=now
    )
    _audit(
        conn,
        event=AuditEventType.HUMAN_IDENTITY_LINKED,
        user_id=user_id,
        operator=operator,
        detail={"issuer": issuer},
        now=now,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage CHWRR hydrologist grants")
    parser.add_argument(
        "--operator", required=True, help="host operator handle for audit"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--tenant", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--issuer", required=True)
    create.add_argument("--subject", required=True)
    link = commands.add_parser("link-identity")
    link.add_argument("user_id", type=UUID)
    link.add_argument("--issuer", required=True)
    link.add_argument("--subject", required=True)
    disable = commands.add_parser("disable")
    disable.add_argument("user_id", type=UUID)
    for action in ("grant", "revoke"):
        change = commands.add_parser(action)
        change.add_argument("user_id", type=UUID)
        change.add_argument("station_id", type=UUID)
        change.add_argument("permission", choices=[p.value for p in HumanPermission])
    args = parser.parse_args()
    now = ensure_utc(datetime.now(UTC))
    engine = create_engine_from_env()
    try:
        with engine.begin() as conn:
            if args.command == "create":
                user_id = create_hydrologist(
                    conn,
                    tenant_code=args.tenant,
                    display_name=args.display_name,
                    issuer=args.issuer,
                    subject=args.subject,
                    operator=args.operator,
                    now=now,
                    user_id=UserId(uuid4()),
                )
                print(user_id)  # noqa: T201 - CLI output
            elif args.command == "link-identity":
                link_hydrologist_identity(
                    conn,
                    user_id=UserId(args.user_id),
                    issuer=args.issuer,
                    subject=args.subject,
                    operator=args.operator,
                    now=now,
                )
            elif args.command == "disable":
                disable_hydrologist(
                    conn, user_id=UserId(args.user_id), operator=args.operator, now=now
                )
            else:
                change_hydrologist_grant(
                    conn,
                    user_id=UserId(args.user_id),
                    station_id=StationId(args.station_id),
                    permission=HumanPermission(args.permission),
                    action=args.command,
                    operator=args.operator,
                    now=now,
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
