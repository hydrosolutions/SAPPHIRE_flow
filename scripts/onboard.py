#!/usr/bin/env python3
# ruff: noqa: T201
"""CAMELS-CH station onboarding script.

Downloads CAMELS-CH data (if needed) and onboards stations into PostgreSQL.

Usage:
    # Onboard all BAFU stations (downloads data if needed):
    uv run python scripts/onboard.py

    # Onboard specific stations:
    uv run python scripts/onboard.py --basin-ids 2004 2034 2135

    # Use a specific data directory (skip download):
    uv run python scripts/onboard.py --data-dir /path/to/CAMELS_CH

    # Custom database URL:
    DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \
        uv run python scripts/onboard.py

Environment:
    DATABASE_URL   PostgreSQL connection string (required)
                   Example: postgresql+psycopg://postgres:postgres@localhost:5432/sapphire
    SAPPHIRE_CONFIG  Path to TOML config file with qc_rules section (optional;
                   if unset, built-in Swiss defaults are used)
    SAPPHIRE_ENV   Set to "dev" for human-readable console log output
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
import structlog

from sapphire_flow.logging import configure_api_logging
from sapphire_flow.services.onboarding import onboard_from_camelsch
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
from sapphire_flow.store.flow_regime_config_store import PgFlowRegimeConfigStore
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc

configure_api_logging()
log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_migrations(engine: sa.Engine) -> None:
    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(alembic_cfg, "head")


def _load_qc_rules():  # type: ignore[no-untyped-def]
    from sapphire_flow.config.qc_rules import load_qc_rules

    config_path = os.environ.get("SAPPHIRE_CONFIG")
    if config_path is not None:
        return load_qc_rules(config_path)
    # No config file — use built-in Swiss defaults by side-stepping the env check
    from sapphire_flow.config.qc_rules import _default_swiss_qc_rules

    return _default_swiss_qc_rules()


def _print_result(result) -> None:  # type: ignore[no-untyped-def]
    print()
    print("=== Onboarding Complete ===")
    print(f"Stations created:  {result.stations_created:,}")
    print(f"Stations skipped:  {result.stations_skipped:,}")
    print(f"Basins created:    {result.basins_created:,}")
    print(f"Basins skipped:    {result.basins_skipped:,}")
    print(f"Observations:      {result.observations_imported:,}")
    print(f"Forcing records:   {result.forcing_records_imported:,}")
    print(f"QC passed:         {result.observations_qc_passed:,}")
    print(f"QC failed:         {result.observations_qc_failed:,}")
    print(f"QC suspect:        {result.observations_qc_suspect:,}")
    print(f"Baselines:         {result.baselines_computed:,}")
    print(f"Flow regimes:      {result.flow_regimes_computed:,}")
    print(f"Errors:            {len(result.errors):,}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Onboard CAMELS-CH stations into PostgreSQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Path to CAMELS-CH data (default: $SAPPHIRE_DATA_DIR/raw/CAMELS_CH)",
    )
    parser.add_argument(
        "--basin-ids",
        nargs="+",
        metavar="ID",
        default=None,
        help="Gauge IDs to onboard (default: all stations)",
    )
    parser.add_argument(
        "--start-date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Start date filter (inclusive)",
    )
    parser.add_argument(
        "--end-date",
        metavar="YYYY-MM-DD",
        default=None,
        help="End date filter (exclusive)",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        default=False,
        help="Download CAMELS-CH data before onboarding",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be done without writing to the database",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "ERROR: DATABASE_URL environment variable is not set.\n"
            "Example: postgresql+psycopg://postgres:postgres@localhost:5432/sapphire",
            file=sys.stderr,
        )
        return 1

    if args.data_dir is not None:
        data_dir: Path = args.data_dir
    else:
        from sapphire_flow.config.paths import resolve_data_dir

        config_data_dir: str | None = None
        config_path = os.environ.get("SAPPHIRE_CONFIG")
        if config_path is not None:
            from sapphire_flow.config.deployment import load_config

            config_data_dir = load_config(config_path).paths_data_dir
        data_dir = resolve_data_dir(config_data_dir) / "raw" / "CAMELS_CH"

    basin_ids: list[str] | None = args.basin_ids
    start_date: str | None = args.start_date
    end_date: str | None = args.end_date

    if args.dry_run:
        print("--- DRY RUN --- (no database writes will occur)")
        print(f"  data_dir:   {data_dir}")
        print(f"  basin_ids:  {basin_ids or 'all'}")
        print(f"  start_date: {start_date or 'default (1980-01-01)'}")
        print(f"  end_date:   {end_date or 'default (2030-01-01)'}")
        print(f"  download:   {args.download}")
        return 0

    # Optionally download data first
    if args.download:
        import camelsch

        log.info("download_starting", dest=str(data_dir))
        data_dir = camelsch.download_camels_ch(dest=data_dir)
        log.info("download_complete", data_dir=str(data_dir))

    log.info(
        "database_connecting",
        url=database_url.split("@")[-1],  # omit credentials from log
    )
    engine = sa.create_engine(database_url)

    log.info("migrations_running")
    _run_migrations(engine)
    log.info("migrations_complete")

    qc_rules = _load_qc_rules()
    clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    log.info(
        "onboarding_starting",
        data_dir=str(data_dir),
        basin_ids=basin_ids,
        start_date=start_date,
        end_date=end_date,
    )

    try:
        with engine.connect() as conn:
            # Use autocommit so each store operation commits independently.
            # This prevents a single FK violation from aborting all
            # subsequent operations within the same PostgreSQL transaction.
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")

            basin_store = PgBasinStore(conn)
            station_store = PgStationStore(conn)
            obs_store = PgObservationStore(conn)
            forcing_store = PgHistoricalForcingStore(conn)
            baseline_store = PgClimBaselineStore(conn)
            flow_regime_store = PgFlowRegimeConfigStore(conn)

            result = onboard_from_camelsch(
                data_dir=data_dir,
                basin_store=basin_store,
                station_store=station_store,
                obs_store=obs_store,
                forcing_store=forcing_store,
                baseline_store=baseline_store,
                flow_regime_store=flow_regime_store,
                qc_rules=qc_rules,
                clock=clock,
                basin_ids=basin_ids,
                start_date=start_date,
                end_date=end_date,
            )
    except Exception as exc:
        log.error("onboarding_failed", error=str(exc))
        print(f"\nERROR: Onboarding failed — {exc}", file=sys.stderr)
        print("Partial data may have been committed.", file=sys.stderr)
        return 1

    _print_result(result)

    if result.errors:
        print("\nWarnings (per-station errors):", file=sys.stderr)
        for err in result.errors:
            print(f"  - {err}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)
