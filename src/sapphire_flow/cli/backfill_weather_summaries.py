"""Build immutable read summaries from retained forecast weather; dry run by default."""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.write_principal import resolve_run_principal
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.store.weather_summary_backfill import (
    BackfillMode,
    backfill_weather_summary,
    check_backfill_scope,
)
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.ids import ForecastId

if TYPE_CHECKING:
    from collections.abc import Sequence

log = structlog.get_logger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    configure_cli_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="overrides SAPPHIRE_CONFIG")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--operator", default=None)
    parser.add_argument("--forecast-id", type=UUID, action="append", required=True)
    parser.add_argument(
        "--data-use", choices=["standard", "provisional_rating"], default="standard"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        config_path = args.config or os.environ.get("SAPPHIRE_CONFIG")
        if not config_path:
            raise ConfigurationError("SAPPHIRE_CONFIG is not set (or pass --config)")
        os.environ["SAPPHIRE_CONFIG"] = config_path
        identity = load_deployment_identity_config(config_path)
        data_use = ForecastDataUse(args.data_use)
        ids = [ForecastId(value) for value in args.forecast_id]
        mode = BackfillMode.APPLY if args.apply else BackfillMode.DRY_RUN
        engine = create_engine_from_env()
        try:
            with engine.begin() as conn:
                principal = resolve_run_principal(
                    PgTenantStore(conn),
                    identity,
                    tenant_code=args.tenant,
                    operator=args.operator,
                )
                assert principal.tenant_id is not None
                check_backfill_scope(
                    conn, ids, tenant_id=principal.tenant_id, data_use=data_use
                )
            for forecast_id in ids:
                with engine.begin() as conn:
                    result = backfill_weather_summary(
                        conn,
                        forecast_id,
                        tenant_id=principal.tenant_id,
                        data_use=data_use,
                        mode=mode,
                    )
                log.info(
                    "weather_summary.backfill",
                    forecast_id=str(forecast_id),
                    mode=mode.value,
                    result=result.value,
                )
        finally:
            engine.dispose()
    except (
        ConfigurationError,
        TenantIsolationError,
        SQLAlchemyError,
        ValueError,
    ) as exc:
        # Do not expose database parameters, evidence payloads or configuration secrets.
        log.error("weather_summary.backfill_refused", cause=type(exc).__name__)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
