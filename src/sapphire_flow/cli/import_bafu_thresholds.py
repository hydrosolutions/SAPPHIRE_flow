from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import structlog
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.config.deployment import load_config
from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.bafu_threshold_import import import_one
from sapphire_flow.services.threshold_import import (
    ThresholdImportMode,
)
from sapphire_flow.services.write_principal import resolve_run_principal
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.threshold_import import ThresholdImportStatus

log = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview or import official BAFU discharge thresholds"
    )
    parser.add_argument(
        "--config", default=os.environ.get("SAPPHIRE_CONFIG", "config.toml")
    )
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--operator")
    parser.add_argument("--station", action="append", default=[])
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    engine = create_engine_from_env()
    with engine.connect() as conn:
        principal = resolve_run_principal(
            PgTenantStore(conn),
            load_deployment_identity_config(args.config),
            tenant_code=args.tenant,
            operator=args.operator,
        )
        assert principal.tenant_id is not None
        store = PgStationStore(conn)
        roster = store.fetch_all_stations()
        if args.station:
            selected = [
                store.fetch_station_by_code(code, "bafu") for code in args.station
            ]
            if any(s is None or s.tenant_id != principal.tenant_id for s in selected):
                raise ConfigurationError(
                    "a requested station is missing or outside the tenant"
                )
            stations = [s for s in selected if s is not None]
        else:
            stations = [
                s
                for s in roster
                if s.tenant_id == principal.tenant_id
                and s.network == "bafu"
                and "discharge" in s.measured_parameters
            ]
    results: list[dict[str, object]] = []
    with httpx.Client() as client:
        for station in stations:
            result: dict[str, object]
            try:
                result = import_one(
                    station=station,
                    tenant_id=principal.tenant_id,
                    config=config,
                    engine=engine,
                    client=client,
                    mode=ThresholdImportMode.APPLY
                    if args.apply
                    else ThresholdImportMode.PREVIEW,
                    report_dir=args.report_dir,
                    operator=args.operator,
                    clock=lambda: ensure_utc(datetime.now(UTC)),
                )
            except (
                httpx.HTTPError,
                ValueError,
                ConfigurationError,
                SQLAlchemyError,
                OSError,
            ) as exc:
                result = {
                    "station_code": station.code,
                    "status": "error",
                    "reason": type(exc).__name__,
                }
            results.append(result)
            log.info("bafu_threshold_import", **result)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "report.json").write_text(json.dumps(results, indent=2) + "\n")
    if any(
        r["status"]
        in {ThresholdImportStatus.ERROR.value, ThresholdImportStatus.CONFLICT.value}
        for r in results
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
