"""Pause or resume observation polling for one station (#501).

``python -m sapphire_flow.cli.ingest_pause --tenant T --network N --code C
--operator O --reason "..." (--pause | --resume) [--apply]``. Dry run by
default; ``--apply`` calls ``set_station_ingest_paused``, which writes the
flag and one audit row in the same transaction. The write gate is the host's
``[deployment]`` identity (``writable_tenants`` or ``global_admin``). Exit
codes: 0 ok (including no change), 2 usage or invalid, 3 tenant refused.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.station_ingest_pause import set_station_ingest_paused
from sapphire_flow.services.write_principal import (
    TENANT_REFUSAL_FOR_CLI,
    resolve_run_principal,
)
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        StationStore,
        TenantStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

EXIT_OK, EXIT_USAGE, EXIT_REFUSED = 0, 2, 3


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def run_ingest_pause(
    args: argparse.Namespace,
    *,
    identity: DeploymentIdentityConfig,
    tenants: TenantStore,
    stations: StationStore,
    audit: AuditLogStore,
    clock: Callable[[], UtcDatetime],
) -> int:
    if not args.reason.strip() or not args.operator.strip():
        log.error("ingest_pause_invalid", error="reason and operator are required")
        return EXIT_USAGE
    paused: bool = args.pause
    try:
        principal = resolve_run_principal(
            tenants,
            identity,
            tenant_code=args.tenant,
            operator=args.operator.strip(),
        )
        station = stations.fetch_station_by_code(args.code, args.network)
        if station is None:
            raise ConfigurationError(f"unknown station {args.network}/{args.code}")
        label = f"station {station.code} ({station.network}) [{station.id}]"
        if not args.apply:
            if (
                principal.tenant_id is not None
                and principal.tenant_id != station.tenant_id
            ):
                _out(f"REFUSED: {TENANT_REFUSAL_FOR_CLI}")
                return EXIT_REFUSED
            if station.ingest_paused is paused:
                _out(f"NO CHANGE\n  {label}\n  ingest_paused is already {paused}")
                return EXIT_OK
            _out(
                f"DRY RUN (nothing written)\n  {label}\n"
                f"  would set ingest_paused {station.ingest_paused} -> {paused}\n"
                f"  audit: operator={args.operator.strip()} "
                f"reason={args.reason.strip()!r}\n\nRe-run with --apply to write."
            )
            return EXIT_OK
        changed = set_station_ingest_paused(
            station_id=station.id,
            paused=paused,
            reason=args.reason,
            principal=principal,
            stations=stations,
            audit=audit,
            clock=clock,
        )
    except TenantIsolationError as exc:
        log.error("ingest_pause_refused", error=str(exc))
        _out(f"REFUSED: {TENANT_REFUSAL_FOR_CLI}")
        return EXIT_REFUSED
    except ConfigurationError as exc:
        log.error("ingest_pause_invalid", error=str(exc))
        return EXIT_USAGE
    if not changed:
        _out(f"NO CHANGE\n  {label}\n  ingest_paused is already {paused}")
        return EXIT_OK
    log.info(
        "ingest_pause_applied",
        station_id=str(station.id),
        to=paused,
        operator=args.operator.strip(),
    )
    _out(f"APPLIED\n  {label}\n  ingest_paused {not paused} -> {paused}")
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pause or resume observation polling for a station. "
        "Dry run unless --apply."
    )
    parser.add_argument("--config", default=None, help="overrides SAPPHIRE_CONFIG")
    parser.add_argument("--tenant", required=True, metavar="CODE")
    parser.add_argument("--network", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reason", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pause", dest="pause", action="store_true")
    mode.add_argument("--resume", dest="pause", action="store_false")
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
        with create_engine_from_env().begin() as conn:
            return run_ingest_pause(
                args,
                identity=identity,
                tenants=PgTenantStore(conn),
                stations=PgStationStore(conn),
                audit=PgAuditLogStore(conn),
                clock=lambda: ensure_utc(datetime.now(UTC)),
            )
    except ConfigurationError as exc:
        log.error("ingest_pause_invalid", error=str(exc))
        return EXIT_USAGE
    except SQLAlchemyError as exc:
        log.error("ingest_pause_failed", cause=type(exc).__name__)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
