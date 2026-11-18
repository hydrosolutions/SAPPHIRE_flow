"""Plan 540 M3a: load a CSV of observations for any existing station.

``python -m sapphire_flow.cli.load_station_observations water-level|discharge ...``.
Dry run by default; ``--apply`` writes the rows and exactly one audit row.
Stage 1 QC (Plan 540 M3b) runs after the store on ``--apply`` and in memory on a
dry run. Exit codes: 0 ok, 2 usage/validation, 3 refused (including QC that could
not fully judge the data, unless ``--allow-unchecked``).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.config.qc_rules import resolve_qc_rules
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.station_observation_load import (
    DatumRequiredError,
    ObservationCsvError,
    ObservationValueKind,
    days_with_value,
    parse_observation_csv,
    to_raw_observations,
)
from sapphire_flow.services.station_observation_qc import (
    DATUM_SKIP_VERSION_SUFFIX,
    USABLE_STATUSES,
    Stage1QcReport,
    run_stage1_qc,
)
from sapphire_flow.services.station_qc_overrides import (
    resolve_configured_station_qc,
)
from sapphire_flow.services.write_principal import (
    enforce_tenant_isolation,
    resolve_run_principal,
)
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AuditEventType,
    QcStatus,
    WaterLevelGaugeType,
)
from sapphire_flow.types.ids import ObservationId
from sapphire_flow.types.observation import Observation

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.protocols.stores import (
        AuditLogStore,
        ClimBaselineStore,
        ObservationStore,
        StationStore,
        TenantStore,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcRuleSet
    from sapphire_flow.types.observation import RawObservation
    from sapphire_flow.types.station import StationConfig

log = structlog.get_logger(__name__)

_QC_CONTEXT = timedelta(hours=2)

EXIT_OK, EXIT_USAGE, EXIT_REFUSED = 0, 2, 3


class LoadRolledBack(Exception):  # noqa: N818
    """QC refused an applied load: the caller must discard the transaction."""


_KIND_CHOICES = {
    "gauge-reading": ObservationValueKind.GAUGE_READING,
    "level-masl": ObservationValueKind.LEVEL_MASL,
}


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def _example(
    station: StationConfig,
    kind: ObservationValueKind,
    raw: RawObservation,
    reading: float,
) -> str:
    if kind is not ObservationValueKind.GAUGE_READING:
        return f"reading {reading:g} -> stored {raw.value:g} ({kind.value})"
    gauge = station.water_level_gauge_type
    if gauge is WaterLevelGaugeType.BRIDGE_SENSOR:
        return (
            f"reading {reading:g} m -> stored {raw.value:g} m a.s.l. "
            f"(bridge sensor at {station.water_level_sensor_masl})"
        )
    return (
        f"reading {reading:g} m -> stored {raw.value:g} m a.s.l. "
        f"(staff gauge, datum {station.water_level_datum_masl})"
    )


def _looks_absolute(
    station: StationConfig, kind: ObservationValueKind, values: list[float]
) -> bool:
    datum = station.water_level_datum_masl
    return (
        kind is ObservationValueKind.GAUGE_READING
        and datum is not None
        and datum > 100
        and all(v > datum - 1 for v in values)
    )


def run_load(
    args: argparse.Namespace,
    *,
    text: str,
    identity: DeploymentIdentityConfig,
    tenants: TenantStore,
    stations: StationStore,
    observations: ObservationStore,
    audit: AuditLogStore,
    baselines: ClimBaselineStore,
    qc_rules: QcRuleSet,
    clock: Callable[[], UtcDatetime],
    new_id: Callable[[], UUID] = uuid4,
) -> int:
    kind: ObservationValueKind = args.kind
    parameter = (
        "discharge" if kind is ObservationValueKind.DISCHARGE_M3S else "water_level"
    )
    try:
        station = stations.fetch_station_by_code(args.station_code, args.network)
        if station is None:
            raise ObservationCsvError(
                f"no station ({args.station_code!r}, {args.network!r}); "
                "create it first (create_station)"
            )
        zone = args.zone
        offset = (
            timedelta(minutes=args.utc_offset_minutes)
            if args.utc_offset_minutes is not None
            else None
        )
        if offset is None and zone is None:
            zone = ZoneInfo(station.timezone)
        rows = parse_observation_csv(
            text,
            time_column=args.time_column,
            value_column=args.value_column,
            utc_offset=offset,
            timezone=zone,
            now=clock(),
        )
    except ObservationCsvError as exc:
        log.error("load_observations_invalid", error=str(exc))
        return EXIT_USAGE
    try:
        principal = resolve_run_principal(
            tenants, identity, tenant_code=args.tenant, operator=args.operator
        )
        enforce_tenant_isolation(
            principal=principal,
            target_tenant_id=station.tenant_id,
            audit_log_store=audit,
            event_type=AuditEventType.OBSERVATIONS_LOADED,
            target_type="station",
            target_id=str(station.id),
            detail={"code": station.code, "parameter": parameter},
            now=clock(),
        )
        raws = to_raw_observations(rows, station, parameter, kind)
    except (DatumRequiredError, TenantIsolationError) as exc:
        log.error("load_observations_refused", error=str(exc))
        _out(f"REFUSED: {exc}")
        return EXIT_REFUSED
    except ConfigurationError as exc:
        log.error("load_observations_invalid", error=str(exc))
        return EXIT_USAGE

    values = [v for _, v in rows]
    if _looks_absolute(station, kind, values):
        _out(
            "WARNING: all values are above the gauge zero "
            f"({station.water_level_datum_masl}); they look already absolute. "
            "Did you mean --value-kind level-masl?"
        )
        if args.strict:
            return EXIT_USAGE
    first, last = min(r.timestamp for r in raws), max(r.timestamp for r in raws)
    tz_used = (
        f"UTC{args.utc_offset_minutes:+d} min"
        if args.utc_offset_minutes is not None
        else str(args.zone or station.timezone)
    )
    _out("APPLIED" if args.apply else "DRY RUN (nothing written)")
    _out(f"  station: {station.code} / {station.network}  [{station.id}]")
    _out(f"  parameter: {parameter}, kind: {kind.value}, rows: {len(raws)}")
    gauge = station.water_level_gauge_type
    _out(
        f"  gauge: {gauge.value if gauge else 'unknown'}, "
        f"datum: {station.water_level_datum_masl}, time zone used: {tz_used}"
    )
    _out(f"  first {first.isoformat()}  last {last.isoformat()} (UTC)")
    _out(f"  example: {_example(station, kind, raws[0], rows[0][1])}")
    window_end = ensure_utc(last + timedelta(microseconds=1))
    window_start = ensure_utc(first - _QC_CONTEXT)
    qc_store: ObservationStore | None = None
    if args.apply:
        written = observations.store_raw_observations(raws)
        _out(f"  rows written or changed: {len(written)}")
        audit.append_entry(
            AuditEntry.system(  # pyright: ignore[reportUnknownMemberType]
                event_type=AuditEventType.OBSERVATIONS_LOADED,
                target_type="station",
                target_id=str(station.id),
                detail={
                    "code": station.code,
                    "network": station.network,
                    "parameter": parameter,
                    "kind": kind.value,
                    "rows": len(raws),
                    "first_utc": first.isoformat(),
                    "last_utc": last.isoformat(),
                    "operator": principal.id,
                },
                ip_address=None,
                created_at=clock(),
            )
        )
        log.info(
            "station_observations_loaded", station_id=str(station.id), rows=len(raws)
        )
        qc_store = observations.qc_context_reader()
        all_obs = qc_store.fetch_observations(
            station.id, parameter, window_start, window_end
        )
        loaded = {(r.timestamp, r.source) for r in raws}
        target_ids = frozenset(
            o.id for o in all_obs if (o.timestamp, o.source) in loaded
        )
    else:
        all_obs = [
            Observation(
                id=ObservationId(new_id()),
                station_id=r.station_id,
                timestamp=r.timestamp,
                parameter=r.parameter,
                value=r.value,
                source=r.source,
                rating_curve_id=r.rating_curve_id,
                rating_curve_correction_version=r.rating_curve_correction_version,
                qc_status=QcStatus.RAW,
                qc_flags=[],
                qc_rule_version=None,
                created_at=clock(),
            )
            for r in raws
        ]
        target_ids = frozenset(o.id for o in all_obs)
    report = run_stage1_qc(
        all_obs,
        qc_store,
        baselines,
        station.id,
        parameter,
        qc_rules,
        station_networks={station.id: station.network},
        datum=station.water_level_datum_masl,
        overrides=resolve_configured_station_qc([station], qc_rules, tenants).overrides,
        target_ids=target_ids,
    )
    local_zone = ZoneInfo(station.timezone)
    end = args.as_of or last.astimezone(local_zone).date()
    usable = days_with_value(
        [o.timestamp for o in all_obs if report.statuses[o.id] in USABLE_STATUSES],
        local_zone,
        end=end,
        lookback_days=args.lookback_days,
    )
    code = _print_qc(report, usable, args)
    if code == EXIT_REFUSED and args.apply:
        _out(
            "NOTHING WAS SAVED: the load was rolled back. "
            + (
                "Run the same command again."
                if report.stale
                else "Re-run with --allow-unchecked to save it anyway."
            )
        )
        raise LoadRolledBack
    if not args.apply:
        refused_hint = (
            "run the dry run again."
            if report.stale
            else "add --allow-unchecked to save it anyway."
        )
        _out(
            "\nRe-run with --apply to write."
            if code != EXIT_REFUSED
            else "\nWith --apply this load would be refused and nothing saved; "
            + refused_hint
        )
    return code


def _print_qc(report: Stage1QcReport, usable: int, args: argparse.Namespace) -> int:
    counts = report.counts
    _out(f"  QC rule version: {report.rule_version}")
    _out(
        "  QC: "
        + ", ".join(
            f"{counts[key]} {key}"
            for key in ("passed", "failed", "suspect", "unchecked", "unjudged")
        )
        + " (unchecked and unjudged = no rule could judge the reading)"
    )
    if not report.baseline_ran:
        _out("  history-based check not run: no baseline yet")
    _out(
        f"  {usable} of {args.lookback_days} daily values usable (passed or unchecked)"
    )
    if report.stale:
        _out(
            "REFUSED: the stored data changed while quality control ran, so it was "
            "not recorded. Run the same command again."
        )
        return EXIT_REFUSED
    problems = [
        f"group {group.parameter}: no quality rule applies "
        f"({group.reason}, time step {group.inferred_time_step_seconds} s); too few "
        "readings per day for any rule to run; check the file's time step and "
        "time zone"
        for group in report.zero_rule_groups
    ]
    if report.rule_version.endswith(DATUM_SKIP_VERSION_SUFFIX):
        problems.append(
            "the station has no datum, so the range and outlier checks were skipped; "
            "set the datum first (create_station)"
        )
    if problems and not args.allow_unchecked:
        for problem in problems:
            _out(f"REFUSED: {problem}")
        return EXIT_REFUSED
    return EXIT_OK


def _zone_arg(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"unknown IANA time zone {name!r}") from exc


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="overrides SAPPHIRE_CONFIG")
    parser.add_argument("--tenant", required=True, metavar="CODE")
    parser.add_argument("--station-code", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--time-column", required=True)
    parser.add_argument("--value-column", required=True)
    zone = parser.add_mutually_exclusive_group()
    zone.add_argument("--utc-offset-minutes", type=int, default=None)
    zone.add_argument("--zone", type=_zone_arg, default=None)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--operator", default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--allow-unchecked", action="store_true")
    parser.add_argument("--apply", action="store_true")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load observations from a CSV (Plan 540 M3a). Dry run by default."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    water = sub.add_parser("water-level")
    _common(water)
    water.add_argument(
        "--value-kind", choices=list(_KIND_CHOICES), default="gauge-reading"
    )
    discharge = sub.add_parser("discharge")
    _common(discharge)
    return parser


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    args = _build_parser().parse_args(argv)
    args.kind = (
        ObservationValueKind.DISCHARGE_M3S
        if args.command == "discharge"
        else _KIND_CHOICES[args.value_kind]
    )
    if args.lookback_days < 1:
        _build_parser().error("--lookback-days must be at least 1")
    return args


def _config_path(override: str | None) -> str:
    path = override or os.environ.get("SAPPHIRE_CONFIG")
    if not path:
        raise ConfigurationError("SAPPHIRE_CONFIG is not set (or pass --config)")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    configure_cli_logging()
    try:
        args = _parse_args(list(sys.argv[1:] if argv is None else argv))
        text = args.csv.read_text(encoding="utf-8-sig")
        config_path = _config_path(args.config)
        os.environ["SAPPHIRE_CONFIG"] = config_path
        identity = load_deployment_identity_config(config_path)
        qc_rules, rule_source = resolve_qc_rules()
        _out(f"QC rule set: {rule_source}")
        try:
            with create_engine_from_env().begin() as conn:
                return run_load(
                    args,
                    text=text,
                    identity=identity,
                    tenants=PgTenantStore(conn),
                    stations=PgStationStore(conn),
                    observations=PgObservationStore(conn),
                    audit=PgAuditLogStore(conn),
                    baselines=PgClimBaselineStore(conn),
                    qc_rules=qc_rules,
                    clock=lambda: ensure_utc(datetime.now(UTC)),
                )
        except LoadRolledBack:
            return EXIT_REFUSED
    except (ConfigurationError, OSError) as exc:
        log.error("load_observations_invalid", error=str(exc))
        return EXIT_USAGE
    except SQLAlchemyError as exc:
        log.error("load_observations_failed", cause=type(exc).__name__)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
