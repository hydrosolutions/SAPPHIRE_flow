"""Plan 120 Task 3A — CLI entrypoint for importing an accepted basin/static
package.

Run via: python -m sapphire_flow.cli.import_basin_package --package-dir <dir>

Manual/onboarding-time invocation only for v1 (plan Task 3A "Scope out": no
scheduling/Prefect flow wraps this). Resolves stations against the live
`stations` table (`(code, network)`, network-scoped per contract §9 — Task
1B) and prints the resulting acceptance report. Exits non-zero when the
package (or an accepted decision at the write boundary) was rejected.

Also builds and passes a PRODUCTION `assigned_model_features` resolver
(`services.basin_importer.build_assigned_model_features_resolver`) — the
union of every ACTIVE station/group-assigned model's declared static
requirements for a basin's station. Without it, `evaluate_basin_acceptance`
defaults to treating no basin as verifiably assigned, downgrading a null
required-static-feature to a warning instead of an onboarding hold (fixer
round, major finding).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from sapphire_flow.types.basin_package import BasinPackageImportReport, BasinRecord
    from sapphire_flow.types.ids import StationId

log = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import an accepted basin/static package (Plan 120 Task 3A). "
            "Reads DATABASE_URL from the environment."
        )
    )
    parser.add_argument(
        "--package-dir",
        required=True,
        type=Path,
        help=(
            "Path to the package directory (manifest.json, basins.gpkg, "
            "static_attributes.parquet, feature_catalog.json, "
            "validation_report.json, and optionally bands.gpkg)."
        ),
    )
    args = parser.parse_args()

    from sapphire_flow.logging import configure_cli_logging

    configure_cli_logging()

    report = _run_import(args.package_dir)
    _log_report(report)
    if report.outcome == "rejected":
        sys.exit(1)


def _run_import(package_dir: Path) -> BasinPackageImportReport:
    from sapphire_flow.db.engine import create_engine_from_env
    from sapphire_flow.services.basin_importer import (
        build_assigned_model_features_resolver,
        import_basin_package_from_directory,
    )
    from sapphire_flow.services.model_registry import discover_models
    from sapphire_flow.store.station_group_store import PgStationGroupStore
    from sapphire_flow.store.station_store import PgStationStore
    from sapphire_flow.types.datetime import ensure_utc

    engine = create_engine_from_env()
    # Discovered ONCE (entry-point scan, no DB) and reused for every basin —
    # see build_assigned_model_features_resolver for why the default (None)
    # seam is never acceptable in production (Task 3A fixer round, major
    # finding).
    models = discover_models()

    def resolve_station(code: str, network: str) -> StationId | None:
        with engine.connect() as conn:
            station = PgStationStore(conn).fetch_station_by_code(code, network)
        return station.id if station is not None else None

    def assigned_model_features(basin: BasinRecord) -> frozenset[str]:
        with engine.connect() as conn:
            resolver = build_assigned_model_features_resolver(
                PgStationStore(conn),
                PgStationGroupStore(conn),
                resolve_station,
                models,
            )
            return resolver(basin)

    return import_basin_package_from_directory(
        package_dir,
        engine,
        resolve_station=resolve_station,
        assigned_model_features=assigned_model_features,
        clock=lambda: ensure_utc(datetime.now(UTC)),
    )


def _log_report(report: BasinPackageImportReport) -> None:
    log.info(
        "basin_importer.cli.report",
        package_id=report.package_id,
        outcome=report.outcome,
        accepted=len(report.accepted),
        onboarding_held=len(report.onboarding_held),
        imported_basins=len(report.imported_basins),
        rejection_reason=report.rejection_reason,
    )
    for decision in report.onboarding_held:
        log.warning(
            "basin_importer.cli.onboarding_held",
            network=decision.network,
            station_code=decision.station_code,
            basin_code=decision.basin_code,
            hold_reasons=decision.hold_reasons,
            warnings=decision.warnings,
        )
    for decision in report.accepted:
        if decision.warnings:
            log.warning(
                "basin_importer.cli.accepted_with_warnings",
                network=decision.network,
                station_code=decision.station_code,
                basin_code=decision.basin_code,
                warnings=decision.warnings,
            )
    for basin in report.imported_basins:
        if basin.material_change:
            log.warning(
                "basin_importer.cli.material_change",
                network=basin.network,
                basin_code=basin.basin_code,
                affected_artifact_ids=[str(a) for a in basin.affected_artifact_ids],
            )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("basin_importer.cli.failed")
        sys.exit(1)
