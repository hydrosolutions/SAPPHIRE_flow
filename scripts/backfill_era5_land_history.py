#!/usr/bin/env python3
# ruff: noqa: T201
"""ERA5-Land (sloth-dynamic store) reanalysis binding + chunked historical
backfill (Plan 183 T2, fixer round M4).

Mirrors ``scripts/backfill_meteoswiss_history.py``'s two-phase, idempotent
pattern: without an entrypoint like this one, selecting
``DeploymentConfig.reanalysis_source = "era5_land"`` (T4) only wires a
*reader* (``adapters/hybrid_reanalysis_factories.py``) — nothing populates
``historical_forcing`` under ``ForcingSource.ERA5_LAND`` on a fresh
deployment, so the reader silently returns nothing.

Two phases, both idempotent — safe to re-run either or both:

1. **Bind** — insert the ERA5-Land REANALYSIS binding for every eligible
   EXISTING station (a valid basin polygon). Cheap, no network fetch beyond
   the station/basin read.
2. **Backfill** — for every eligible station, fetch and store
   precipitation/temperature from 1981-01-01 through the store's own
   published high-water mark (``Era5LandReanalysisAdapter.discover_boundary()``),
   chunked by (year, station-batch, parameter) and resumable
   (``services/era5_land_backfill.py``).

Usage:
    # Bind + backfill the whole eligible fleet:
    uv run python scripts/backfill_era5_land_history.py

    # Bind only — skip the network-heavy backfill:
    uv run python scripts/backfill_era5_land_history.py --bind-only

    # Custom database URL:
    DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \
        uv run python scripts/backfill_era5_land_history.py

Environment:
    DATABASE_URL     PostgreSQL connection string (required)
    SAPPHIRE_ENV     Set to "dev" for human-readable console log output
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog

from sapphire_flow.logging import configure_api_logging
from sapphire_flow.services.era5_land_backfill import (
    BackfillResult,
    BindingBackfillResult,
    bind_era5_land_reanalysis_fleet,
    eligible_era5_land_configs,
    run_era5_land_backfill,
)
from sapphire_flow.types.datetime import ensure_utc

if TYPE_CHECKING:
    from sapphire_flow.types.basin import Basin
    from sapphire_flow.types.ids import StationId

configure_api_logging()
log = structlog.get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_migrations(engine: sa.Engine) -> None:
    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(alembic_cfg, "head")


def _print_binding_result(result: BindingBackfillResult) -> None:
    print()
    print("=== ERA5-Land Binding Backfill ===")
    print(f"Stations bound:     {result.stations_bound:,}")
    print(f"Stations excluded:  {result.stations_excluded:,}  (no valid basin polygon)")


def _print_backfill_result(result: BackfillResult) -> None:
    print()
    print("=== ERA5-Land Chunked Backfill ===")
    print(f"Stations:           {result.stations:,}")
    print(f"Chunks processed:   {result.chunks_processed:,}")
    print(f"Chunks skipped:     {result.chunks_skipped:,}  (already fully covered)")
    print(f"Rows written:       {result.rows_written:,}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bind + backfill the ERA5-Land (sloth-dynamic) reanalysis series "
            "(Plan 183)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bind-only",
        action="store_true",
        default=False,
        help="Run the binding phase only — skip the chunked backfill.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be done without writing to the database.",
    )
    parser.add_argument(
        "--station-batch-size",
        type=int,
        default=None,
        metavar="N",
        help="Stations per backfill work-unit chunk (default: module default).",
    )
    parser.add_argument(
        "--store-root",
        type=str,
        default="s3://sloth-dynamic/v1/era5",
        help="ERA5-Land store root (default: s3://sloth-dynamic/v1/era5).",
    )
    parser.add_argument(
        "--min-land-fraction",
        type=float,
        default=0.5,
        help="Minimum required land-cell coverage fraction per basin (default: 0.5).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.station_batch_size is not None and args.station_batch_size <= 0:
        print(
            "ERROR: --station-batch-size must be positive, got "
            f"{args.station_batch_size}.",
            file=sys.stderr,
        )
        return 1
    if not (0.0 < args.min_land_fraction <= 1.0):
        print(
            "ERROR: --min-land-fraction must be in (0.0, 1.0], got "
            f"{args.min_land_fraction}.",
            file=sys.stderr,
        )
        return 1

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "ERROR: DATABASE_URL environment variable is not set.\n"
            "Example: postgresql+psycopg://postgres:postgres@localhost:5432/sapphire",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print("--- DRY RUN --- (no database writes will occur)")
        print(f"  bind_only:          {args.bind_only}")
        print(f"  station_batch_size: {args.station_batch_size or '(default)'}")
        print(f"  store_root:         {args.store_root}")
        print(f"  min_land_fraction:  {args.min_land_fraction}")
        return 0

    log.info("database_connecting", url=database_url.split("@")[-1])
    engine = sa.create_engine(database_url, pool_pre_ping=True)

    log.info("migrations_running")
    _run_migrations(engine)
    log.info("migrations_complete")

    clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    try:
        with engine.connect() as conn:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")

            from sapphire_flow.store.basin_store import PgBasinStore
            from sapphire_flow.store.historical_forcing_store import (
                PgHistoricalForcingStore,
            )
            from sapphire_flow.store.station_store import PgStationStore

            basin_store = PgBasinStore(conn)
            station_store = PgStationStore(conn)
            forcing_store = PgHistoricalForcingStore(conn)

            log.info("era5_land_binding_starting")
            binding_result = bind_era5_land_reanalysis_fleet(station_store, basin_store)
            log.info(
                "era5_land_binding_complete",
                stations_bound=binding_result.stations_bound,
                stations_excluded=binding_result.stations_excluded,
            )
            _print_binding_result(binding_result)

            if args.bind_only:
                return 0

            from sapphire_flow.adapters.era5_land_reanalysis import (
                Era5LandReanalysisAdapter,
            )
            from sapphire_flow.preprocessing.exact_extract_grid_extractor import (
                ExactExtractGridExtractor,
            )

            basins: dict[StationId, Basin] = {}
            for station in station_store.fetch_all_stations():
                if station.basin_id is None:
                    continue
                basin = basin_store.fetch_basin(station.basin_id)
                if basin is not None:
                    basins[station.id] = basin

            adapter = Era5LandReanalysisAdapter(
                store_root=args.store_root,
                extractor=ExactExtractGridExtractor(),
                basins=basins,
                clock=clock,
                min_land_fraction=args.min_land_fraction,
            )

            all_stations = station_store.fetch_all_stations()
            station_configs = eligible_era5_land_configs(all_stations, basin_store)
            log.info("era5_land_backfill_starting", stations=len(station_configs))

            backfill_kwargs: dict[str, object] = {}
            if args.station_batch_size is not None:
                backfill_kwargs["station_batch_size"] = args.station_batch_size

            backfill_result = run_era5_land_backfill(
                adapter=adapter,
                forcing_store=forcing_store,
                station_configs=station_configs,
                **backfill_kwargs,  # type: ignore[arg-type]
            )
            log.info(
                "era5_land_backfill_complete",
                stations=backfill_result.stations,
                rows_written=backfill_result.rows_written,
                chunks_processed=backfill_result.chunks_processed,
                chunks_skipped=backfill_result.chunks_skipped,
            )
            _print_backfill_result(backfill_result)
    except Exception as exc:
        log.error("era5_land_backfill_failed", error=str(exc))
        print(f"\nERROR: ERA5-Land backfill failed — {exc}", file=sys.stderr)
        print("Partial data may have been committed (safe to re-run).", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(
            "\nAborted (safe to re-run — the backfill is resumable).",
            file=sys.stderr,
        )
        sys.exit(130)
