"""Plan 540 M4a: read-only station readiness report.

``python -m sapphire_flow.cli.station_readiness --tenant T --network N --code C
[--model M] [--weather-scope S] [--check-gateway]
[--start ISO --end ISO --time-step-hours H [--minimum-samples N]] [--json]``.
Writes nothing (the transaction is read-only and never committed). Facts only;
the last line is "N checks failed, M passed" and counts only the offline
operational checks. ``--check-gateway`` (one read-only Gateway request, off by
default) and the training window add a separate section that is never counted.
Exit codes: 0 report produced (whatever it says, including a Gateway error),
2 usage error, unknown station or station of another tenant.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.station_readiness import (
    ReadinessStores,
    TrainingWindowRequest,
    build_station_readiness,
)
from sapphire_flow.services.write_principal import resolve_run_principal
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import ModelId

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sapphire_flow.adapters.recap_gateway import EcmwfApiLike
    from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
    from sapphire_flow.protocols.forecast_model import ForecastModel
    from sapphire_flow.protocols.stores import TenantStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.station_readiness import StationReadinessReport

log = structlog.get_logger(__name__)

EXIT_OK, EXIT_USAGE = 0, 2


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


def summary_line(report: StationReadinessReport) -> str:
    return f"{report.failed()} checks failed, {report.passed()} passed"


def render_text(report: StationReadinessReport) -> str:
    lines = [f"station {report.code} ({report.network})"]
    lines += [f"{c.status.value}  {c.name}  {c.detail}" for c in report.checks]
    if report.supplementary:
        lines.append("optional sections (not counted in the line below):")
        lines += [
            f"{c.status.value}  {c.name}  {c.detail}" for c in report.supplementary
        ]
    lines.append(summary_line(report))
    return "\n".join(lines)


def render_json(report: StationReadinessReport) -> str:
    return json.dumps(
        {
            "station": {
                "id": str(report.station_id),
                "code": report.code,
                "network": report.network,
            },  # fmt: skip
            "checks": [
                {"name": c.name, "status": c.status.value, "detail": c.detail}
                for c in report.checks
            ],
            "supplementary": [
                {"name": c.name, "status": c.status.value, "detail": c.detail}
                for c in report.supplementary
            ],
            "failed": report.failed(),
            "passed": report.passed(),
        }
    )


def _parse_instant(text: str) -> UtcDatetime:
    moment = datetime.fromisoformat(text)
    return ensure_utc(moment if moment.tzinfo else moment.replace(tzinfo=UTC))


def _training_request(
    args: argparse.Namespace, model: ForecastModel | None
) -> TrainingWindowRequest | None:
    given = (args.start, args.end, args.time_step_hours)
    if all(v is None for v in given):
        return None
    if any(v is None for v in given):
        raise ConfigurationError("--start, --end and --time-step-hours go together")
    if model is None:
        raise ConfigurationError("the training window needs --model")
    request = TrainingWindowRequest(
        start=_parse_instant(args.start),
        end=_parse_instant(args.end),
        time_step=timedelta(hours=args.time_step_hours),
        minimum_samples=args.minimum_samples,
    )
    if request.time_step not in model.data_requirements.supported_time_steps:
        raise ConfigurationError("--time-step-hours is not supported by the model")
    return request


def run_readiness(
    args: argparse.Namespace,
    *,
    identity: DeploymentIdentityConfig,
    tenants: TenantStore,
    stores: ReadinessStores,
    model: ForecastModel | None,
    scope: tuple[str, str | None] | None,
    clock: Callable[[], UtcDatetime],
    gateway: Callable[[], EcmwfApiLike] | None = None,
) -> int:
    try:
        training = _training_request(args, model)
    except (ConfigurationError, ValueError) as exc:
        log.error("station_readiness_invalid", error=str(exc))
        return EXIT_USAGE
    try:
        principal = resolve_run_principal(
            tenants, identity, tenant_code=args.tenant, operator=None
        )
    except ConfigurationError as exc:
        log.error("station_readiness_invalid", error=str(exc))
        return EXIT_USAGE
    station = stores.stations.fetch_station_by_code(args.code, args.network)
    if station is None or station.tenant_id != principal.tenant_id:
        log.error(
            "station_readiness_unknown_station", code=args.code, network=args.network
        )
        return EXIT_USAGE
    report = build_station_readiness(
        station,
        stores,
        model=model,
        model_id=ModelId(args.model) if args.model else None,
        scope_hru_name=scope[0] if scope else None,
        scope_polygon_name=scope[1] if scope else None,
        now=clock(),
        training=training,
        gateway=gateway if args.check_gateway else None,
    )
    _out(render_json(report) if args.json else render_text(report))
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only station readiness report (Plan 540 M4a)."
    )
    parser.add_argument("--config", default=None, help="overrides SAPPHIRE_CONFIG")
    parser.add_argument("--tenant", required=True, metavar="CODE")
    parser.add_argument("--network", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--weather-scope", default=None)
    parser.add_argument(
        "--check-gateway",
        action="store_true",
        help="also ask the Gateway for the lookback days (read only, one request)",
    )
    parser.add_argument("--start", default=None, help="training window start (ISO)")
    parser.add_argument("--end", default=None, help="training window end (ISO)")
    parser.add_argument("--time-step-hours", type=float, default=None)
    parser.add_argument("--minimum-samples", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    return parser


def _config_path(override: str | None) -> str:
    path = override or os.environ.get("SAPPHIRE_CONFIG")
    if not path:
        raise ConfigurationError("SAPPHIRE_CONFIG is not set (or pass --config)")
    return path


def resolve_model(name: str | None) -> ForecastModel | None:
    if name is None:
        return None
    from sapphire_flow.services.model_registry import discover_models

    model = discover_models().get(ModelId(name))
    if model is None:
        raise ConfigurationError(f"model {name!r} is not installed")
    return model


def resolve_scope(
    config_path: str, name: str | None, station_id: str
) -> tuple[str, str | None] | None:
    if name is None:
        return None
    from sapphire_flow.config.nepal_weather import load_weather_scope

    scope = load_weather_scope(Path(config_path), name).scope
    polygon = next(
        (
            p.polygon_name
            for p in scope.station_polygons
            if str(p.station_id) == station_id
        ),
        None,
    )
    return str(scope.gateway_hru_name), polygon


def _gateway_api(config_path: str) -> EcmwfApiLike:
    import requests

    from sapphire_flow.adapters.recap_operational_http import (
        build_bounded_operational_api,
    )
    from sapphire_flow.config.operational_weather import OperationalWeatherConfig
    from sapphire_flow.config.recap_gateway import (
        build_recap_client_config,
        load_recap_api_key,
        load_recap_gateway_config,
    )

    return build_bounded_operational_api(
        config=build_recap_client_config(
            api_key=load_recap_api_key(),
            config=load_recap_gateway_config(Path(config_path)),
        ),
        max_bytes=OperationalWeatherConfig().max_response_bytes,
        session=requests.Session(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    configure_cli_logging()
    try:
        args = _build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
        config_path = _config_path(args.config)
        os.environ["SAPPHIRE_CONFIG"] = config_path
        identity = load_deployment_identity_config(config_path)
        model = resolve_model(args.model)
        with create_engine_from_env().connect() as conn:
            conn.execute(sa.text("SET TRANSACTION READ ONLY"))
            station_store = PgStationStore(conn)
            station = station_store.fetch_station_by_code(args.code, args.network)
            scope = resolve_scope(
                config_path,
                args.weather_scope,
                str(station.id) if station is not None else "",
            )
            code = run_readiness(
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
                model=model,
                scope=scope,
                clock=lambda: ensure_utc(datetime.now(UTC)),
                gateway=lambda: _gateway_api(config_path),
            )
            conn.rollback()
            return code
    except ConfigurationError as exc:
        log.error("station_readiness_invalid", error=str(exc))
        return EXIT_USAGE
    except SQLAlchemyError as exc:
        log.error("station_readiness_failed", cause=type(exc).__name__)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
