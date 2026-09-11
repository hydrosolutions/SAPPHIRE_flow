#!/usr/bin/env python3
# ruff: noqa: T201
"""Create a named station group from station codes (Plan 262 T3a).

Group **creation** had no route at all before this: outside tests nothing in
``src/`` or ``scripts/`` called ``store_group`` or ``add_station_to_group``,
and the staging database held zero groups. Group **assignment** is different
and already routed (``create_group_assignment`` has production callers) — this
script deliberately does NOT assign a model. That is T3b, and it cannot run
until the artifact import has created the ``models`` row its foreign key needs.

**Dry run by default.** Nothing is written unless ``--apply`` is passed. The
dry run performs every lookup and validation the real run does and prints the
exact changes it would make, so "it worked in dry run" means the same thing it
does in the real run.

**Idempotent.** Re-running against an existing group of the same name (within
the tenant) reuses it and adds only the members it is missing. Running twice is
a no-op, not an error — which is what makes it safe to re-run after a partial
failure.

⛔ **This script never writes ``station_status``.** It reads stations to resolve
codes and nothing else. On this deployment ``operational`` was already applied
to 111 stations by direct DB write, and that status gates ingest as well as
forecasting — so a tool that "helpfully" promoted a station to make a group
valid would cost observations permanently. A station that is not eligible is
reported, never fixed.

Usage:
    # dry run (default) — prints what would change, writes nothing
    uv run python scripts/create_station_group.py \
        --name swiss-cmal-small-pilot --station-code 2009 --station-code 2091

    # the real write
    uv run python scripts/create_station_group.py \
        --name swiss-cmal-small-pilot --station-code 2009 --station-code 2091 --apply

    # inside the runtime image, the ENTRYPOINT must be explicit:
    #   docker compose exec -T prefect-worker /entrypoint.sh \
    #       python /app/scripts/create_station_group.py --name ... --station-code ...

Environment:
    DATABASE_URL   PostgreSQL connection string (required)
    SAPPHIRE_ENV   Set to "dev" for human-readable console log output

Exit codes: 0 on success (including a no-op re-run), 1 on any unresolved
station code, missing configuration, or write failure.
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

import structlog

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import AuditEventType
from sapphire_flow.types.ids import StationGroupId, StationId, TenantId
from sapphire_flow.types.station import StationConfig, StationGroup
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

_DEFAULT_NETWORK = "bafu"


class _StationLookup(Protocol):
    def fetch_station_by_code(
        self, code: str, network: str
    ) -> StationConfig | None: ...


class _GroupStore(Protocol):
    def fetch_group_by_name(
        self, tenant_id: TenantId, name: str
    ) -> StationGroup | None: ...
    def store_group(self, group: StationGroup) -> None: ...
    def add_station_to_group(
        self, group_id: StationGroupId, station_id: StationId
    ) -> None: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupPlan:
    """What a run WOULD change. The dry run prints this; the real run applies
    exactly it — one shape for both, so the preview cannot drift from the write.
    """

    name: str
    tenant_id: TenantId
    group_id: StationGroupId
    group_exists: bool
    members_to_add: tuple[tuple[str, StationId], ...]
    already_members: tuple[str, ...]
    unresolved_codes: tuple[str, ...]
    wrong_tenant_codes: tuple[str, ...] = ()

    @property
    def is_noop(self) -> bool:
        return self.group_exists and not self.members_to_add

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        """Every code that makes this run unsafe to apply. One property so a new
        rejection reason cannot be added to the plan and forgotten at the gate."""
        return self.unresolved_codes + self.wrong_tenant_codes


def plan_station_group(
    *,
    name: str,
    station_codes: Sequence[str],
    network: str,
    tenant_id: TenantId,
    station_store: _StationLookup,
    group_store: _GroupStore,
) -> GroupPlan:
    """Resolve codes and diff against what exists. Pure of writes — every
    caller, dry run or not, goes through this first.
    """
    existing = group_store.fetch_group_by_name(tenant_id, name)
    current_members: frozenset[StationId] = (
        existing.station_ids if existing is not None else frozenset()
    )

    resolved: list[tuple[str, StationId]] = []
    unresolved: list[str] = []
    already: list[str] = []
    wrong_tenant: list[str] = []
    for code in station_codes:
        station = station_store.fetch_station_by_code(code, network)
        if station is None:
            unresolved.append(code)
            continue
        # A station resolvable but owned by ANOTHER tenant would pass the dry run
        # and then fail on the membership's composite tenant FK at apply time
        # (independent review 2026-09-11). Refuse it here, where nothing is written.
        if station.tenant_id != tenant_id:
            wrong_tenant.append(code)
            continue
        if station.id in current_members:
            already.append(code)
        else:
            resolved.append((code, station.id))

    return GroupPlan(
        name=name,
        tenant_id=tenant_id,
        group_id=existing.id if existing is not None else StationGroupId(uuid4()),
        group_exists=existing is not None,
        members_to_add=tuple(resolved),
        already_members=tuple(already),
        unresolved_codes=tuple(unresolved),
        wrong_tenant_codes=tuple(wrong_tenant),
    )


def apply_station_group(
    plan: GroupPlan,
    *,
    group_store: _GroupStore,
    clock: Callable[[], UtcDatetime],
    description: str | None = None,
    audit_log_store: object | None = None,
    operator: str | None = None,
) -> None:
    """Create the group if absent, then add the missing members. Never touches
    station status, and never removes a member.
    """
    if plan.blocking_codes:
        raise ValueError(
            "refusing to write: unusable station codes "
            f"{', '.join(plan.blocking_codes)}"
        )

    if not plan.group_exists:
        group_store.store_group(
            StationGroup(
                id=plan.group_id,
                name=plan.name,
                station_ids=frozenset(),
                description=description,
                created_at=clock(),
                tenant_id=plan.tenant_id,
            )
        )

    for _code, station_id in plan.members_to_add:
        group_store.add_station_to_group(plan.group_id, station_id)

    if audit_log_store is not None:
        from sapphire_flow.types.auth import AuditEntry

        audit_log_store.append_entry(  # type: ignore[attr-defined]
            AuditEntry.system(
                event_type=AuditEventType.STATION_GROUP_CREATED,
                target_type="station_group",
                target_id=str(plan.group_id),
                detail={
                    "name": plan.name,
                    "created": not plan.group_exists,
                    "members_added": [code for code, _ in plan.members_to_add],
                    "operator": operator,
                },
                ip_address=None,
                created_at=clock(),
            )
        )


def _render(plan: GroupPlan, *, applied: bool) -> str:
    verb = "APPLIED" if applied else "DRY RUN (nothing written)"
    lines = [
        f"{verb}",
        f"  group:   {plan.name}  [{plan.group_id}]",
        f"  tenant:  {plan.tenant_id}",
        f"  exists:  {'yes — reusing' if plan.group_exists else 'no — would create'}",
    ]
    if plan.members_to_add:
        joined = ", ".join(code for code, _ in plan.members_to_add)
        lines.append(f"  add:     {joined}")
    if plan.already_members:
        lines.append(f"  already: {', '.join(plan.already_members)}")
    if plan.unresolved_codes:
        lines.append(f"  UNKNOWN: {', '.join(plan.unresolved_codes)}")
    if plan.wrong_tenant_codes:
        lines.append(f"  OTHER TENANT: {', '.join(plan.wrong_tenant_codes)}")
    if plan.is_noop:
        lines.append("  -> no change (idempotent re-run)")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a station group from station codes (Plan 262 T3a).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--name", required=True, help="group name (unique per tenant)")
    parser.add_argument(
        "--station-code",
        action="append",
        dest="station_codes",
        required=True,
        metavar="CODE",
        help="station code; repeat for each member",
    )
    parser.add_argument(
        "--network", default=_DEFAULT_NETWORK, help=f"default: {_DEFAULT_NETWORK}"
    )
    parser.add_argument("--description", default=None)
    parser.add_argument("--operator", default=None, help="recorded in the audit entry")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write; omit for a dry run (the default)",
    )
    return parser


def _engine_for(database_url: str) -> object:
    """A seam, not an abstraction: it exists so `main`'s dry-run guard can be tested
    without a database. Every other test calls the planning/apply functions directly,
    so nothing else would notice if the write moved above that guard.
    """
    import sqlalchemy as sa

    return sa.create_engine(database_url, pool_pre_ping=True)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        return 1

    from sapphire_flow.store.audit_log_store import PgAuditLogStore
    from sapphire_flow.store.station_group_store import PgStationGroupStore
    from sapphire_flow.store.station_store import PgStationStore

    clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731
    engine = _engine_for(database_url)

    try:
        with engine.connect() as conn:
            read_conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            plan = plan_station_group(
                name=args.name,
                station_codes=args.station_codes,
                network=args.network,
                tenant_id=DEFAULT_TENANT_ID,
                station_store=PgStationStore(read_conn),
                group_store=PgStationGroupStore(read_conn),
            )

            if plan.blocking_codes:
                print(_render(plan, applied=False))
                print(
                    "\nERROR: unusable station codes — nothing written.",
                    file=sys.stderr,
                )
                return 1

            if not args.apply:
                print(_render(plan, applied=False))
                print("\nRe-run with --apply to write.")
                return 0

            # 🪤 The group store must be told to JOIN this transaction. Left to its
            # default it sets `self._begin = conn.engine.begin` — a NEW engine-level
            # transaction — so `store_group` would commit independently while
            # `add_station_to_group` and the audit INSERT (both plain
            # `self._conn.execute`) stayed in ours. A failure after the group row
            # would then roll those back and leave an EMPTY, UNAUDITED group behind
            # while the CLI reported failure. Independent review found this; the
            # success-only tests could not.
            with engine.begin() as write_conn:
                apply_station_group(
                    plan,
                    group_store=PgStationGroupStore(
                        write_conn,
                        transaction_factory=lambda: nullcontext(write_conn),
                    ),
                    clock=clock,
                    description=args.description,
                    audit_log_store=PgAuditLogStore(write_conn),
                    operator=args.operator,
                )
    except Exception as exc:  # noqa: BLE001 - operator CLI reports, never traces
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(_render(plan, applied=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
