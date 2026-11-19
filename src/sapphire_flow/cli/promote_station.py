"""Plan 540 M7a: promote an ONBOARDING station to OPERATIONAL.

``python -m sapphire_flow.cli.promote_station --tenant T --network N --code C
--operator O [--target discharge] [--weather-scope S] [--apply]``. Dry run by
default and always allowed; ``--apply`` also needs ``[deployment]
promotion_allowed = true`` on the host and writes the targets, the status and
one ``STATION_STATUS_CHANGE`` audit row in one transaction. Exit codes: 0 ok
(including already operational), 2 usage, 3 refused.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.cli.station_readiness import resolve_scope
from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.station_promotion import (
    PromotionAction,
    PromotionPlan,
    apply_promotion,
    plan_promotion,
)
from sapphire_flow.services.station_readiness import ReadinessStores
from sapphire_flow.services.write_principal import resolve_run_principal
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import AuditLogStore, TenantStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ModelId

log = structlog.get_logger(__name__)

EXIT_OK, EXIT_USAGE, EXIT_REFUSED = 0, 2, 3
NOT_ALLOWED = "promotion on a shared host is the owner's decision"


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def render(plan: PromotionPlan, *, allowed: bool, applied: bool) -> str:
    station = plan.station
    lines = [
        "APPLIED" if applied else "DRY RUN (nothing written)",
        f"  station {station.code} ({station.network}) [{station.id}]",
    ]
    if plan.action is PromotionAction.ALREADY_OPERATIONAL:
        return "\n".join([*lines, "  already operational; nothing to do"])
    lines += [
        f"  {'OK  ' if c.ok else 'FAIL'}  {c.number}. {c.name}: {c.detail}"
        for c in plan.conditions
    ]
    lines += [f"  WARNING: {w}" for w in plan.warnings]
    if plan.action is PromotionAction.REFUSED:
        lines.append(f"  REFUSED: {len(plan.failing())} blocking condition(s) failed")
        return "\n".join(lines)
    lines.append(
        f"  would set status operational and forecast_targets {{{plan.target!r}}}"
        if not applied
        else f"  status operational, forecast_targets {{{plan.target!r}}}"
    )
    if not applied and not allowed:
        lines.append(f"  NOTE: --apply would be refused: {NOT_ALLOWED}")
    return "\n".join(lines)


def run_promote(
    args: argparse.Namespace,
    *,
    identity: DeploymentIdentityConfig,
    tenants: TenantStore,
    stores: ReadinessStores,
    audit: AuditLogStore,
    resolve_model: Callable[[ModelId], ForecastModel | None],
    scope: tuple[str, str | None] | None,
    clock: Callable[[], UtcDatetime],
) -> int:
    allowed = identity.promotion_allowed
    try:
        principal = resolve_run_principal(
            tenants, identity, tenant_code=args.tenant, operator=args.operator
        )
        plan = plan_promotion(
            station_code=args.code,
            network=args.network,
            target=args.target,
            principal=principal,
            stores=stores,
            audit=audit,
            resolve_model=resolve_model,
            scope_hru_name=scope[0] if scope else None,
            scope_polygon_name=scope[1] if scope else None,
            clock=clock,
        )
    except TenantIsolationError as exc:
        log.error("promote_station_refused", error=str(exc))
        _out(f"REFUSED: {exc}")
        return EXIT_REFUSED
    except ConfigurationError as exc:
        log.error("promote_station_invalid", error=str(exc))
        return EXIT_USAGE
    if args.apply and not allowed and plan.action is PromotionAction.PROMOTE:
        _out(render(plan, allowed=allowed, applied=False))
        _out(f"REFUSED: {NOT_ALLOWED} (set [deployment] promotion_allowed)")
        return EXIT_REFUSED
    if plan.action is PromotionAction.REFUSED:
        _out(render(plan, allowed=allowed, applied=False))
        return EXIT_REFUSED
    do_apply = args.apply and plan.action is PromotionAction.PROMOTE
    if do_apply:
        apply_promotion(
            plan, stores=stores, audit=audit, principal=principal, clock=clock
        )
    _out(render(plan, allowed=allowed, applied=do_apply))
    if plan.action is PromotionAction.PROMOTE and not args.apply:
        _out("\nRe-run with --apply to write.")
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Promote an onboarding station to operational (Plan 540 M7a). "
        "Dry run unless --apply."
    )
    parser.add_argument("--config", default=None, help="overrides SAPPHIRE_CONFIG")
    parser.add_argument("--tenant", required=True, metavar="CODE")
    parser.add_argument("--network", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--target", default="discharge")
    parser.add_argument("--weather-scope", default=None)
    parser.add_argument("--apply", action="store_true")
    return parser


def _config_path(override: str | None) -> str:
    path = override or os.environ.get("SAPPHIRE_CONFIG")
    if not path:
        raise ConfigurationError("SAPPHIRE_CONFIG is not set (or pass --config)")
    return path


def _installed_model(model_id: ModelId) -> ForecastModel | None:
    from sapphire_flow.services.model_registry import discover_models

    return discover_models().get(model_id)


def main(argv: Sequence[str] | None = None) -> int:
    configure_cli_logging()
    try:
        args = _build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
        config_path = _config_path(args.config)
        os.environ["SAPPHIRE_CONFIG"] = config_path
        identity = load_deployment_identity_config(config_path)
        with create_engine_from_env().begin() as conn:
            station_store = PgStationStore(conn)
            station = station_store.fetch_station_by_code(args.code, args.network)
            scope = resolve_scope(
                config_path,
                args.weather_scope,
                str(station.id) if station is not None else "",
            )
            return run_promote(
                args,
                identity=identity,
                tenants=PgTenantStore(conn),
                stores=ReadinessStores(
                    stations=station_store,
                    basins=PgBasinStore(conn),
                    observations=PgObservationStore(conn),
                    forcing=PgHistoricalForcingStore(conn),
                    ratings=PgRatingCurveStore(conn),
                    groups=PgStationGroupStore(conn),
                    polygons=RecapGatewayPolygonStore(conn),
                ),
                audit=PgAuditLogStore(conn),
                resolve_model=_installed_model,
                scope=scope,
                clock=lambda: ensure_utc(datetime.now(UTC)),
            )
    except ConfigurationError as exc:
        log.error("promote_station_invalid", error=str(exc))
        return EXIT_USAGE
    except SQLAlchemyError as exc:
        log.error("promote_station_failed", cause=type(exc).__name__)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
