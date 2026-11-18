"""Plan 540 M2: create an ONBOARDING river station for any tenant.

``python -m sapphire_flow.cli.create_station --tenant ... --code ... [--apply]``
and ``python -m sapphire_flow.cli.create_station list``. Dry run by default.
The gauge type is asked first; the datum is the gauge-zero elevation for both
types, a bridge sensor additionally needs the sensor elevation.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
import structlog
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.db.metadata import stations as stations_table
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.write_principal import resolve_run_principal
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import GeoCoord
from sapphire_flow.types.enums import (
    AuditEventType,
    GaugingStatus,
    StationKind,
    StationOwnership,
    StationStatus,
    WaterLevelGaugeType,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.protocols.stores import AuditLogStore, StationStore, TenantStore
    from sapphire_flow.types.datetime import UtcDatetime

log = structlog.get_logger(__name__)

ALLOWED_PARAMETERS = frozenset({"water_level", "discharge"})
_SLUG = re.compile(r"^[a-z0-9_]+$")
_ELEVATION_BAND = (-500.0, 9000.0)
_GAUGE_CHOICES = {
    "staff": WaterLevelGaugeType.STAFF_OR_PRESSURE,
    "bridge-sensor": WaterLevelGaugeType.BRIDGE_SENSOR,
}


class StationExistsError(ConfigurationError):
    """The (code, network) pair is taken; the pair is unique across tenants."""


@dataclass(frozen=True, kw_only=True, slots=True)
class StationRequest:
    code: str
    network: str
    name: str
    location: GeoCoord
    tenant_code: str
    timezone: str
    parameters: frozenset[str]
    gauge_type: WaterLevelGaugeType | None
    datum_masl: float | None
    sensor_masl: float | None
    gateway_name: str


def _elevation(value: float, label: str) -> float:
    low, high = _ELEVATION_BAND
    if not (math.isfinite(value) and low <= value <= high):
        raise ConfigurationError(
            f"{label} {value} m a.s.l. is outside {low:g}..{high:g}: "
            "enter the elevation above sea level, not a gauge height"
        )
    return value


def _ask_float(ask: Callable[[str], str], prompt: str) -> float | None:
    raw = ask(prompt).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{raw!r} is not a number") from exc


def _prompt_gauge(
    ask: Callable[[str], str],
) -> tuple[WaterLevelGaugeType | None, float | None, float | None]:
    answer = ask(
        "Gauge type: 1) staff or pressure gauge, 2) bridge-mounted radar/ultrasonic "
        "sensor (empty = unknown): "
    ).strip()
    if not answer:
        return None, None, None
    choices = {
        "1": WaterLevelGaugeType.STAFF_OR_PRESSURE,
        "2": WaterLevelGaugeType.BRIDGE_SENSOR,
    }
    if answer not in choices:
        raise ConfigurationError(f"gauge type answer {answer!r} is not 1 or 2")
    gauge_type = choices[answer]
    datum = _ask_float(
        ask, "Elevation of the gauge zero in m a.s.l. (empty = unknown): "
    )
    if datum is None:
        return None, None, None
    if gauge_type is WaterLevelGaugeType.STAFF_OR_PRESSURE:
        return gauge_type, datum, None
    sensor = _ask_float(ask, "Elevation of the sensor in m a.s.l.: ")
    return gauge_type, datum, sensor


def resolve_gauge(
    args: argparse.Namespace,
    parameters: frozenset[str],
    ask: Callable[[str], str] | None,
) -> tuple[WaterLevelGaugeType | None, float | None, float | None]:
    gauge_type: WaterLevelGaugeType | None = args.gauge_type
    datum: float | None = args.datum_masl
    sensor: float | None = args.sensor_masl
    given = gauge_type is not None or datum is not None or sensor is not None
    if "water_level" not in parameters:
        if given or args.datum_unknown:
            raise ConfigurationError(
                "gauge type and datum apply only to stations measuring water_level"
            )
        return None, None, None
    if args.datum_unknown:
        if given:
            raise ConfigurationError("--datum-unknown contradicts the gauge options")
        return None, None, None
    if not given:
        if ask is None:
            raise ConfigurationError(
                "give --gauge-type with --datum-masl (and --sensor-masl for a bridge "
                "sensor), or --datum-unknown to leave water-level range checks off"
            )
        gauge_type, datum, sensor = _prompt_gauge(ask)
        if gauge_type is None:
            log.warning("station_datum_unknown_range_checks_off")
            return None, None, None
    if gauge_type is None:
        raise ConfigurationError("a datum or sensor elevation needs --gauge-type")
    if datum is None:
        raise ConfigurationError("the gauge-zero elevation (--datum-masl) is required")
    _elevation(datum, "gauge zero")
    if gauge_type is WaterLevelGaugeType.STAFF_OR_PRESSURE:
        if sensor is not None:
            raise ConfigurationError(
                "a sensor elevation applies to bridge sensors only"
            )
        return gauge_type, datum, None
    if sensor is None:
        raise ConfigurationError("a bridge sensor needs --sensor-masl as well")
    _elevation(sensor, "sensor")
    if sensor <= datum:
        raise ConfigurationError(
            f"sensor elevation {sensor:g} must be above the gauge zero {datum:g}"
        )
    return gauge_type, datum, sensor


def parse_station_request(
    args: argparse.Namespace, ask: Callable[[str], str] | None = None
) -> StationRequest:
    code, network, name = args.code.strip(), args.network.strip(), args.name.strip()
    if not (code and network and name):
        raise ConfigurationError("code, network and name must not be empty")
    try:
        location = GeoCoord(lon=args.lon, lat=args.lat)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    try:
        ZoneInfo(args.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigurationError(f"unknown IANA timezone {args.timezone!r}") from exc
    parameters = frozenset(p.strip() for p in args.parameters.split(",") if p.strip())
    if not parameters or not parameters <= ALLOWED_PARAMETERS:
        raise ConfigurationError(
            f"parameters must be a non-empty subset of {sorted(ALLOWED_PARAMETERS)}"
        )
    if not _SLUG.match(args.gateway_name):
        raise ConfigurationError("gateway name must match ^[a-z0-9_]+$")
    gauge_type, datum, sensor = resolve_gauge(args, parameters, ask)
    return StationRequest(
        code=code,
        network=network,
        name=name,
        location=location,
        tenant_code=args.tenant,
        timezone=args.timezone,
        parameters=parameters,
        gauge_type=gauge_type,
        datum_masl=datum,
        sensor_masl=sensor,
        gateway_name=args.gateway_name,
    )


def plan_station(
    store: StationStore,
    tenants: TenantStore,
    request: StationRequest,
    principal_tenant: UUID,
    *,
    reveal_owner: bool,
    new_id: Callable[[], UUID],
    clock: Callable[[], UtcDatetime],
) -> StationConfig:
    from sapphire_flow.types.ids import TenantId

    existing = store.fetch_station_by_code(request.code, request.network)
    if existing is not None:
        owner = tenants.fetch_tenant(existing.tenant_id) if reveal_owner else None
        owner_note = f", owned by tenant {owner.code!r}" if owner is not None else ""
        raise StationExistsError(
            f"station ({request.code!r}, {request.network!r}) already exists "
            f"(status {existing.station_status.value}{owner_note}); the pair is "
            "unique across all tenants"
        )
    now = clock()
    has_datum = request.datum_masl is not None
    return StationConfig(
        id=StationId(new_id()),
        code=request.code,
        name=request.name,
        location=request.location,
        station_kind=StationKind.RIVER,
        basin_id=None,
        timezone=request.timezone,
        regulation_type=None,
        forecast_targets=None,
        measured_parameters=request.parameters,
        station_status=StationStatus.ONBOARDING,
        created_at=now,
        updated_at=now,
        network=request.network,
        ownership=StationOwnership.OWN,
        wigos_id=None,
        gauging_status=GaugingStatus.GAUGED,
        water_level_datum_masl=request.datum_masl,
        water_level_unit="m" if has_datum else None,
        water_level_gauge_type=request.gauge_type,
        water_level_sensor_masl=request.sensor_masl,
        tenant_id=TenantId(principal_tenant),
    )


def conversion_example(station: StationConfig) -> str | None:
    datum = station.water_level_datum_masl
    if datum is None:
        return None
    sensor = station.water_level_sensor_masl
    if sensor is None:
        return f"reading 2.0 m -> stored {datum + 2.0:.1f} m a.s.l."
    stored = sensor - 3.0
    return (
        f"reading 3.0 m, sensor {sensor:.1f} -> stored {stored:.1f} m a.s.l., "
        f"{stored - datum:.1f} m over gauge zero {datum:.1f}"
    )


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def render(station: StationConfig, request: StationRequest, *, applied: bool) -> str:
    gauge = station.water_level_gauge_type
    lines = [
        "APPLIED" if applied else "DRY RUN (nothing written)",
        f"  station: {station.code} / {station.network}  [{station.id}]",
        f"  tenant:  {request.tenant_code}   name: {station.name}   status: onboarding",
        f"  where:   lon {station.location.lon}, lat {station.location.lat}, "
        f"{station.timezone}",
        f"  measures: {', '.join(sorted(station.measured_parameters))}",
    ]
    if gauge is None:
        lines.append(
            "  gauge:   unknown - water-level range checks stay OFF for this station"
        )
    else:
        lines.append(
            f"  gauge:   {gauge.value}, gauge zero "
            f"{station.water_level_datum_masl} m a.s.l."
        )
        if station.water_level_sensor_masl is not None:
            lines.append(f"  sensor:  {station.water_level_sensor_masl} m a.s.l.")
        lines.append(f"  example: {conversion_example(station)}")
        lines.append("  QC: range checks use the stored level minus the gauge zero.")
    lines += [
        "Next steps:",
        f"  1. Give the basin handover code {station.code!r} and network "
        f"{station.network!r} exactly.",
        f"  2. Register {request.gateway_name!r} in the Gateway.",
        f"  3. Add a [weather_history.scopes.{request.gateway_name}] section to the "
        "weather config.",
    ]
    return "\n".join(lines)


def run_create(
    args: argparse.Namespace,
    *,
    identity: DeploymentIdentityConfig,
    tenants: TenantStore,
    stations: StationStore,
    audit: AuditLogStore,
    ask: Callable[[str], str] | None,
    new_id: Callable[[], UUID],
    clock: Callable[[], UtcDatetime],
) -> int:
    try:
        request = parse_station_request(args, ask)
        principal = resolve_run_principal(
            tenants, identity, tenant_code=request.tenant_code, operator=args.operator
        )
        assert principal.tenant_id is not None  # an explicit tenant always scopes
        station = plan_station(
            stations,
            tenants,
            request,
            principal.tenant_id,
            reveal_owner=identity.global_admin,
            new_id=new_id,
            clock=clock,
        )
    except ConfigurationError as exc:
        log.error("create_station_refused", error=str(exc))
        return 1
    gauge_flags = (args.gauge_type, args.datum_masl, args.sensor_masl)
    prompted = (
        ask is not None
        and "water_level" in request.parameters
        and not args.datum_unknown
        and all(flag is None for flag in gauge_flags)
    )
    if not args.apply:
        _out(render(station, request, applied=False))
        _out("\nRe-run with --apply to write.")
        return 0
    example = conversion_example(station)
    if prompted and ask is not None and example is not None:
        _out(f"  example: {example}")
        if ask("Write this station? [y/N]: ").strip().lower() != "y":
            log.error("create_station_refused", error="not confirmed")
            return 1
    stations.store_station(station)
    audit.append_entry(
        AuditEntry.system(  # pyright: ignore[reportUnknownMemberType]
            event_type=AuditEventType.STATION_ONBOARDED,
            target_type="station",
            target_id=str(station.id),
            detail={
                "code": station.code,
                "network": station.network,
                "tenant_id": str(station.tenant_id),
                "gauge_type": station.water_level_gauge_type.value
                if station.water_level_gauge_type is not None
                else None,
                "datum_masl": station.water_level_datum_masl,
                "sensor_masl": station.water_level_sensor_masl,
                "operator": principal.id,
            },
            ip_address=None,
            created_at=clock(),
        )
    )
    log.info("station_created", station_id=str(station.id), code=station.code)
    _out(render(station, request, applied=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an ONBOARDING river station (Plan 540 M2). "
        "`list` prints tenants and networks. Dry run unless --apply."
    )
    parser.add_argument("--config", default=None, help="overrides SAPPHIRE_CONFIG")
    parser.add_argument("--tenant", required=True, metavar="CODE")
    parser.add_argument("--code", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--lon", required=True, type=float)
    parser.add_argument("--lat", required=True, type=float)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--parameters", required=True, help="water_level,discharge")
    parser.add_argument("--gauge-type", choices=list(_GAUGE_CHOICES), default=None)
    parser.add_argument("--datum-masl", type=float, default=None)
    parser.add_argument("--sensor-masl", type=float, default=None)
    parser.add_argument("--datum-unknown", action="store_true")
    parser.add_argument("--gateway-name", required=True)
    parser.add_argument("--operator", default=None)
    parser.add_argument("--apply", action="store_true")
    return parser


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    args = _build_parser().parse_args(argv)
    args.gauge_type = _GAUGE_CHOICES.get(args.gauge_type)
    return args


def _config_path(override: str | None) -> str:
    path = override or os.environ.get("SAPPHIRE_CONFIG")
    if not path:
        raise ConfigurationError("SAPPHIRE_CONFIG is not set (or pass --config)")
    return path


def run_list(conn: sa.Connection) -> int:
    _out("tenants:")
    for tenant in PgTenantStore(conn).fetch_all_tenants():
        _out(f"  {tenant.code}  {tenant.name}")
    networks = conn.execute(
        sa.select(stations_table.c.network)
        .distinct()
        .order_by(stations_table.c.network)
    )
    _out("networks:")
    for (network,) in networks:
        _out(f"  {network}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    configure_cli_logging()
    is_list = bool(raw) and raw[0] == "list"
    try:
        if is_list:
            list_parser = argparse.ArgumentParser(prog="create_station list")
            list_parser.add_argument("--config", default=None)
            list_parser.parse_args(raw[1:])
            with create_engine_from_env().connect() as conn:
                return run_list(conn)
        args = _parse_args(raw)
        identity = load_deployment_identity_config(_config_path(args.config))
        ask = input if sys.stdin.isatty() else None
        with create_engine_from_env().begin() as conn:
            return run_create(
                args,
                identity=identity,
                tenants=PgTenantStore(conn),
                stations=PgStationStore(conn),
                audit=PgAuditLogStore(conn),
                ask=ask,
                new_id=uuid4,
                clock=lambda: ensure_utc(datetime.now(UTC)),
            )
    except IntegrityError:
        log.error("create_station_refused", error="station already exists (race)")
        return 1
    except ConfigurationError as exc:
        log.error("create_station_refused", error=str(exc))
        return 1
    except SQLAlchemyError as exc:
        log.error("create_station_failed", cause=type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
