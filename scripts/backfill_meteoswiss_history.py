#!/usr/bin/env python3
# ruff: noqa: T201
"""MeteoSwiss reanalysis binding + chunked historical backfill (Plan 115b2).

A supervised batch job, not a request path — this writes up to ~80-100M rows
at the v0 ~1000-station target, chunked and resumable (interrupt and re-run;
already-backfilled (station, product, year) chunks are skipped, no
re-download).

Two phases, both idempotent — safe to re-run either or both:

1. **Bind (§2A)** — insert the four-field MeteoSwiss reanalysis binding for
   every eligible EXISTING station (a valid basin polygon; §3D). Cheap, no
   network fetch beyond the station/basin read.
2. **Backfill (§3A-§3D)** — for every eligible station, fetch and store
   RhiresD/RprelimD/TabsD/TminD/TmaxD/SrelD from 1981-01-01 through each
   product's own STAC-published high-water mark.

Usage:
    # Bind + backfill the whole eligible fleet:
    uv run python scripts/backfill_meteoswiss_history.py

    # Bind only (§2A) — skip the network-heavy backfill:
    uv run python scripts/backfill_meteoswiss_history.py --bind-only

    # Custom database URL:
    DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db \
        uv run python scripts/backfill_meteoswiss_history.py

Environment:
    DATABASE_URL     PostgreSQL connection string (required)
    SAPPHIRE_CONFIG  Path to TOML config file with [adapters.weather_reanalysis]
                     STAC overrides (optional; defaults to MeteoSwiss open data)
    SAPPHIRE_ENV     Set to "dev" for human-readable console log output
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
from sapphire_flow.services.reanalysis_backfill import (
    BackfillResult,
    BindingBackfillResult,
    bind_meteoswiss_reanalysis_fleet,
    eligible_meteoswiss_configs,
    run_backfill,
)
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


def _print_binding_result(result: BindingBackfillResult) -> None:
    print()
    print("=== MeteoSwiss Binding Backfill (§2A) ===")
    print(f"Stations bound:     {result.stations_bound:,}")
    print(f"Stations excluded:  {result.stations_excluded:,}  (no valid basin polygon)")


def _print_backfill_result(result: BackfillResult) -> None:
    print()
    print("=== MeteoSwiss Chunked Backfill (§3) ===")
    print(f"Stations:           {result.stations:,}")
    print(f"Chunks processed:   {result.chunks_processed:,}")
    print(f"Chunks skipped:     {result.chunks_skipped:,}  (already fully covered)")
    print(f"Rows written:       {result.rows_written:,}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bind + backfill the MeteoSwiss reanalysis series (Plan 115b2).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bind-only",
        action="store_true",
        default=False,
        help="Run §2A (binding) only — skip the §3 chunked backfill.",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

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

            log.info("meteoswiss_binding_starting")
            binding_result = bind_meteoswiss_reanalysis_fleet(
                station_store, basin_store
            )
            log.info(
                "meteoswiss_binding_complete",
                stations_bound=binding_result.stations_bound,
                stations_excluded=binding_result.stations_excluded,
            )
            _print_binding_result(binding_result)

            if args.bind_only:
                return 0

            from sapphire_flow.flows.ingest_weather_history import (
                _load_reanalysis_stac_config,  # pyright: ignore[reportPrivateUsage]
                build_production_reanalysis_adapter,
            )

            reanalysis_adapter = build_production_reanalysis_adapter(
                config=_load_reanalysis_stac_config(),
                station_store=station_store,
                basin_store=basin_store,
                clock=clock,
            )

            all_stations = station_store.fetch_all_stations()
            station_configs = eligible_meteoswiss_configs(all_stations, basin_store)
            log.info("meteoswiss_backfill_starting", stations=len(station_configs))

            backfill_kwargs: dict[str, object] = {}
            if args.station_batch_size is not None:
                backfill_kwargs["station_batch_size"] = args.station_batch_size

            backfill_result = run_backfill(
                adapter=reanalysis_adapter,
                forcing_store=forcing_store,
                station_configs=station_configs,
                **backfill_kwargs,  # type: ignore[arg-type]
            )
            log.info(
                "meteoswiss_backfill_complete",
                stations=backfill_result.stations,
                rows_written=backfill_result.rows_written,
                chunks_processed=backfill_result.chunks_processed,
                chunks_skipped=backfill_result.chunks_skipped,
            )
            _print_backfill_result(backfill_result)
    except Exception as exc:
        log.error("meteoswiss_backfill_failed", error=str(exc))
        print(f"\nERROR: MeteoSwiss backfill failed — {exc}", file=sys.stderr)
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
