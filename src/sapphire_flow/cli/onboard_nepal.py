from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from sapphire_flow import __version__
from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.ingest_recap_era5_reanalysis import (
    ingest_recap_era5_reanalysis_flow,
)
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.basin_package_loader import load_basin_package
from sapphire_flow.services.model_registry import discover_models
from sapphire_flow.services.nepal_onboarding import (
    import_nepal_package,
    qualify_discharge_targets,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.nepal_onboarding import (
    HistoryWindow,
    ReadinessStatus,
    TrainingWindow,
)

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.adapters.recap_gateway import (
        RecapClientLike,
        RecapGatewayReanalysisAdapter,
    )

log = structlog.get_logger(__name__)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Prepare the six CHWRR gauges without activating forecasts"
    )
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("basins", "history", "qualify"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--tenant", choices=["chwrr"], required=True)
        command.add_argument(
            "--confirm-t8",
            action="store_true",
            required=True,
            help="Confirm Plan 268 staging acceptance is recorded",
        )
        command.add_argument("--dry-run", action="store_true")
        if name == "basins":
            command.add_argument("--package-dir", type=Path, required=True)
            command.add_argument(
                "--confirm-package-joins", action="store_true", required=True
            )
            command.add_argument(
                "--confirm-snow-scope",
                action="store_true",
                required=True,
                help=(
                    "Confirm the snow deployment is scoped and unscoped runs drained"
                ),
            )
        else:
            command.add_argument(
                "--start", required=True, help="Inclusive ISO timestamp with timezone"
            )
            command.add_argument(
                "--end", required=True, help="Exclusive ISO timestamp with timezone"
            )
        if name == "qualify":
            command.add_argument("--model", required=True)
            command.add_argument("--time-step-hours", type=int, required=True)
            command.add_argument(
                "--minimum-samples",
                type=int,
                required=True,
                help="Minimum complete training windows, selected for the model",
            )
    return result


def build_history_adapter(
    conn: sa.Connection, config_path: Path
) -> RecapGatewayReanalysisAdapter:
    from recap_client import RecapClient

    from sapphire_flow.adapters.recap_gateway import (
        RecapGatewayReanalysisAdapter,
        StoreBackedGatewayPolygonResolver,
    )
    from sapphire_flow.config.recap_gateway import (
        build_recap_client_config,
        load_recap_api_key,
        load_recap_gateway_config,
    )
    from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore

    config = load_recap_gateway_config(config_path)
    client = RecapClient(
        build_recap_client_config(api_key=load_recap_api_key(), config=config)
    )
    return RecapGatewayReanalysisAdapter(
        client=cast("RecapClientLike", client),
        resolver=StoreBackedGatewayPolygonResolver(RecapGatewayPolygonStore(conn)),
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    configure_cli_logging()
    try:
        identity = load_deployment_identity_config(args.config)
        if identity.global_admin or identity.writable_tenants != frozenset({"chwrr"}):
            raise ConfigurationError(
                "Nepal onboarding requires CHWRR-only write authority"
            )
        now = ensure_utc(datetime.now(UTC))
        window = None
        model = None
        if args.command != "basins":
            start = ensure_utc(datetime.fromisoformat(args.start))
            end = ensure_utc(datetime.fromisoformat(args.end))
            window = HistoryWindow(start=start, end=end)
        if args.command == "qualify" and window is not None:
            model = discover_models().get(ModelId(args.model))
            if model is None:
                raise ConfigurationError("requested model is not installed")
            window = TrainingWindow(
                start=window.start,
                end=window.end,
                time_step=timedelta(hours=args.time_step_hours),
                minimum_samples=args.minimum_samples,
            )
            requirements = model.data_requirements
            log.info(
                "nepal_onboarding.requirements",
                model=args.model,
                software_version=__version__,
                past_features=sorted(requirements.past_dynamic_features),
                future_features=sorted(requirements.future_dynamic_features),
                static_features=sorted(requirements.static_features),
                spatial_type=requirements.spatial_input_type.value,
                lookback_steps=requirements.lookback_steps,
                horizon_steps=requirements.forecast_horizon_steps,
            )
        loaded = (
            load_basin_package(args.package_dir) if args.command == "basins" else None
        )
        engine = create_engine_from_env()
        held = 0
        try:
            batches = (
                window.batches()
                if args.command == "history" and window is not None
                else (window,)
            )
            with engine.connect() as conn:
                for batch in batches:
                    with conn.begin() as transaction:
                        if loaded is not None:
                            report = import_nepal_package(
                                conn, loaded, identity, clock=lambda: now
                            )
                            log.info(
                                "nepal_onboarding.basins",
                                outcome=report.outcome,
                                basins=len(report.accepted),
                            )
                        elif args.command == "history" and batch is not None:
                            # Commit or roll back each history batch separately.
                            coverage = ingest_recap_era5_reanalysis_flow.fn(
                                conn,
                                identity,
                                build_history_adapter(conn, args.config),
                                batch,
                                now=now,
                            )
                            for item in coverage:
                                log.info(
                                    "nepal_onboarding.history",
                                    station=item.code,
                                    parameter=item.parameter,
                                    rows=item.rows,
                                    start=item.start,
                                    end=item.end,
                                )
                            held += sum(item.rows == 0 for item in coverage)
                        elif model is not None and isinstance(window, TrainingWindow):
                            reports = qualify_discharge_targets(
                                conn, identity, model, window, now=now
                            )
                            for item in reports:
                                log.info(
                                    "nepal_onboarding.readiness",
                                    station=item.code,
                                    status=item.status.value,
                                    reasons=item.reasons,
                                    qc_counts=item.qc_counts,
                                    qc_versions=item.qc_versions,
                                    usable_observations=item.usable_observations,
                                    complete_samples=item.complete_samples,
                                    overlap_start=item.overlap_start,
                                    overlap_end=item.overlap_end,
                                    model=args.model,
                                    start=window.start,
                                    end=window.end,
                                    time_step_hours=args.time_step_hours,
                                    minimum_samples=window.minimum_samples,
                                    source="recap_era5_land_reanalysis",
                                    publication="unresolved_plan268_d5_d9",
                                    operational="not_activated",
                                    live_feed="not_assessed",
                                    current_rating="not_available",
                                )
                            held = sum(
                                item.status is ReadinessStatus.HELD for item in reports
                            )
                        if args.dry_run:
                            transaction.rollback()
            log.info(
                "nepal_onboarding.completed",
                command=args.command,
                dry_run=args.dry_run,
                held=held,
            )
        finally:
            engine.dispose()
        return 1 if held else 0
    except Exception as exc:
        # Errors can carry measurements or credentials; do not emit their messages.
        log.error("nepal_onboarding.failed", error_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
