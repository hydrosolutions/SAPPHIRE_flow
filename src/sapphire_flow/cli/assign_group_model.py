"""Plan 540 M5-assign: assign a forecast model to a station group.

``python -m sapphire_flow.cli.assign_group_model --tenant T --group-name G
--model M --time-step-hours H [--priority N] [--apply]``. Dry run by default;
``--apply`` writes the assignment and one ``MODEL_ASSIGNED`` audit row in one
transaction. Exit codes: 0 ok (including a no-op re-run), 2 usage, 3 refused.
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.group_model_assignment import (
    AssignmentAction,
    AssignmentPlan,
    AssignmentRefusedError,
    apply_group_assignment,
    plan_group_assignment,
)
from sapphire_flow.services.model_registry import discover_models
from sapphire_flow.services.write_principal import resolve_run_principal
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.model_store import PgModelStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import ModelId

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        ModelStore,
        StationGroupStore,
        TenantStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

EXIT_OK, EXIT_USAGE, EXIT_REFUSED = 0, 2, 3


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def render(plan: AssignmentPlan, *, applied: bool) -> str:
    verb = {
        AssignmentAction.CREATE: "assign",
        AssignmentAction.UPDATE: "REWRITE the ACTIVE assignment (settings differ) of",
        AssignmentAction.NOOP: "no change: identical ACTIVE assignment of",
    }[plan.action]
    head = "APPLIED" if applied else "DRY RUN (nothing written)"
    return (
        f"{head}\n  {verb} model {plan.model_id} to group {plan.group_name!r} "
        f"[{plan.group_id}]\n  time step: {plan.time_step}   priority: {plan.priority}"
        f"   status: active"
    )


def run_assign(
    args: argparse.Namespace,
    *,
    identity: DeploymentIdentityConfig,
    tenants: TenantStore,
    groups: StationGroupStore,
    models: ModelStore,
    audit: AuditLogStore,
    discovered_models: Collection[ModelId],
    clock: Callable[[], UtcDatetime],
) -> int:
    try:
        principal = resolve_run_principal(
            tenants, identity, tenant_code=args.tenant, operator=args.operator
        )
        assert principal.tenant_id is not None  # an explicit tenant always scopes
        plan = plan_group_assignment(
            tenant_id=principal.tenant_id,
            group_name=args.group_name,
            model_id=ModelId(args.model),
            time_step=timedelta(hours=args.time_step_hours),
            priority=args.priority,
            groups=groups,
            models=models,
            discovered_models=discovered_models,
            principal=principal,
            clock=clock,
        )
    except (AssignmentRefusedError, TenantIsolationError) as exc:
        log.error("assign_group_model_refused", error=str(exc))
        _out(f"REFUSED: {exc}")
        return EXIT_REFUSED
    except ConfigurationError as exc:
        log.error("assign_group_model_invalid", error=str(exc))
        return EXIT_USAGE
    if not args.apply:
        _out(render(plan, applied=False))
        if plan.action is not AssignmentAction.NOOP:
            _out("\nRe-run with --apply to write.")
        return EXIT_OK
    try:
        apply_group_assignment(
            plan, groups=groups, audit=audit, principal=principal, clock=clock
        )
    except ConfigurationError as exc:
        log.error("assign_group_model_invalid", error=str(exc))
        return EXIT_USAGE
    _out(render(plan, applied=True))
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assign a model to a station group (Plan 540 M5-assign). "
        "Dry run unless --apply."
    )
    parser.add_argument("--config", default=None, help="overrides SAPPHIRE_CONFIG")
    parser.add_argument("--tenant", required=True, metavar="CODE")
    parser.add_argument("--group-name", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--time-step-hours", required=True, type=float)
    parser.add_argument("--priority", type=int, default=1)
    parser.add_argument("--operator", default=None)
    parser.add_argument("--apply", action="store_true")
    return parser


def _config_path(override: str | None) -> str:
    path = override or os.environ.get("SAPPHIRE_CONFIG")
    if not path:
        raise ConfigurationError("SAPPHIRE_CONFIG is not set (or pass --config)")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    configure_cli_logging()
    try:
        args = _build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
        config_path = _config_path(args.config)
        os.environ["SAPPHIRE_CONFIG"] = config_path
        identity = load_deployment_identity_config(config_path)
        discovered = frozenset(discover_models())
        with create_engine_from_env().begin() as conn:
            return run_assign(
                args,
                identity=identity,
                tenants=PgTenantStore(conn),
                groups=PgStationGroupStore(
                    conn, transaction_factory=lambda: nullcontext(conn)
                ),
                models=PgModelStore(conn),
                audit=PgAuditLogStore(conn),
                discovered_models=discovered,
                clock=lambda: ensure_utc(datetime.now(UTC)),
            )
    except ConfigurationError as exc:
        log.error("assign_group_model_invalid", error=str(exc))
        return EXIT_USAGE
    except SQLAlchemyError as exc:
        log.error("assign_group_model_failed", cause=type(exc).__name__)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
