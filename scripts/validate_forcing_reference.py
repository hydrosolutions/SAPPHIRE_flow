#!/usr/bin/env python3
# ruff: noqa: T201
"""Reference-comparison GO/NO-GO gate (Plan 115b3 §4A-§4D).

A read-only analysis run, NOT a production step — writes nothing to
``historical_forcing`` (the 4C/4D live-tail fetch is a one-off in-memory
measurement, never persisted). Runs *after* the 1981-present backfill (Plan
115b2) and *before* the reader flip (Plan 115b4).

Two independent comparisons (see ``services/validation_gate.py`` for the
exact tolerance-gate maths):

1. **4A/4B — reference comparison** of our self-derived MeteoSwiss basin
   means against CAMELS-CH, 1981-2020, per basin.
2. **4C/4D — the live-tail residual**, ``RprelimD`` vs ``RhiresD`` over their
   current STAC availability overlap (typically only a few weeks — see the
   caveat printed below).

Usage:
    uv run python scripts/validate_forcing_reference.py

    # Skip the live-tail fetch (4C/4D) — reference comparison only:
    uv run python scripts/validate_forcing_reference.py --skip-live-tail

Environment:
    DATABASE_URL     PostgreSQL connection string (required)
    SAPPHIRE_CONFIG  Path to TOML config file with [adapters.weather_reanalysis]
                     STAC overrides (optional; defaults to MeteoSwiss open data)
    SAPPHIRE_ENV     Set to "dev" for human-readable console log output

Exit code: 0 only when the 4A/4B reference comparison ran over a non-empty
result set AND every basin PASSes (Plan 115b3 exit-gate criterion — the run
must "record the per-basin results", not print a vacuous "All basins
PASS."). Any FLAGGED/ESCALATED/DATA_QUALITY_ESCALATE basin, or zero
stations/result rows, exits 1. The 4C/4D live-tail measurement is
diagnostic-only and never affects the exit code on its own.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

import sqlalchemy as sa
import structlog

from sapphire_flow.logging import configure_api_logging
from sapphire_flow.services.reanalysis_backfill import eligible_meteoswiss_configs
from sapphire_flow.services.validation_gate import (
    GateVerdict,
    LiveTailResidualResult,
    ReferenceComparisonReport,
    compute_live_tail_residual,
    discover_overlap_window,
    fetch_overlap_products,
    run_reference_comparison,
)
from sapphire_flow.types.datetime import ensure_utc

configure_api_logging()
log = structlog.get_logger(__name__)


def _print_reference_report(report: ReferenceComparisonReport) -> bool:
    """Prints the 4A/4B reference-comparison report and returns whether the
    run counts as a genuine PASS. An empty result set (zero stations, or a
    station store/store wiring bug that yields zero rows) is a data-quality
    FAILURE, not a vacuous pass — the gate must never print "All basins
    PASS." when nothing was actually compared (Plan 115b3 exit-gate
    criterion: "run 4A-4D ... and record the per-basin results")."""
    if not report.precipitation and not report.temperature:
        print()
        print("=== Reference comparison — precipitation/temperature ===")
        print(
            "NO BASINS EVALUATED — zero stations or zero result rows. This is "
            "a DATA-QUALITY FAILURE, not a pass: nothing was compared."
        )
        return False

    print()
    print("=== Reference comparison — precipitation (RhiresD vs CAMELS-CH) ===")
    print(f"{'basin':<20}{'rel_bias':>10}{'verdict':>24}")
    for r in report.precipitation:
        bias_str = f"{r.rel_bias:+.2%}" if r.rel_bias is not None else "n/a"
        print(f"{r.code:<20}{bias_str:>10}{r.verdict.value:>24}")

    print()
    print("=== Reference comparison — precipitation diagnostics (non-gating) ===")
    print(
        f"{'basin':<20}{'event_max_ours':>16}{'event_max_camels':>18}{'wet_rmse':>10}"
    )
    for r in report.precipitation:
        ours_max = f"{r.event_max_ours:.2f}" if r.event_max_ours is not None else "n/a"
        camels_max = (
            f"{r.event_max_camels:.2f}" if r.event_max_camels is not None else "n/a"
        )
        wet_rmse = f"{r.wet_day_rmse:.2f}" if r.wet_day_rmse is not None else "n/a"
        print(f"{r.code:<20}{ours_max:>16}{camels_max:>18}{wet_rmse:>10}")
        print(f"    season totals (ours):   {r.season_totals_ours}")
        print(f"    season totals (camels): {r.season_totals_camels}")

    print()
    print("=== Reference comparison — temperature (TabsD vs CAMELS-CH) ===")
    print(f"{'basin':<20}{'mean_bias':>10}{'rmse':>8}{'verdict':>24}")
    for t in report.temperature:
        bias_str = f"{t.mean_bias:+.2f}" if t.mean_bias is not None else "n/a"
        rmse_str = f"{t.rmse:.2f}" if t.rmse is not None else "n/a"
        print(f"{t.code:<20}{bias_str:>10}{rmse_str:>8}{t.verdict.value:>24}")

    _needs_disposition = (
        GateVerdict.FLAG,
        GateVerdict.ESCALATE,
        GateVerdict.DATA_QUALITY_ESCALATE,
    )
    flagged = [
        r.code for r in report.precipitation if r.verdict in _needs_disposition
    ] + [t.code for t in report.temperature if t.verdict in _needs_disposition]
    if flagged:
        print()
        print(
            f"FLAGGED/ESCALATED basins requiring disposition before 115b4: "
            f"{sorted(set(flagged))}"
        )
        return False

    print()
    print("All basins PASS.")
    return True


def _print_live_tail_result(result: LiveTailResidualResult | None) -> None:
    print()
    print("=== Live-tail residual — RprelimD vs RhiresD (4D, no confounds) ===")
    if result is None:
        print("No RhiresD/RprelimD availability overlap found — nothing to compare.")
        return
    print(f"Window:             {result.window_start} .. {result.window_end}")
    print(f"Paired samples:     {result.n_paired}")
    print(f"Excluded (RhiresD-only):  {result.n_excluded_rhiresd_only}")
    print(f"Excluded (RprelimD-only): {result.n_excluded_rprelimd_only}")
    if result.mean_bias is not None and result.rmse is not None:
        print(f"Mean bias (mm):     {result.mean_bias:+.3f}")
        print(f"RMSE (mm):          {result.rmse:.3f}")
    else:
        print("No paired samples — mean_bias/rmse undefined.")
    print(
        "NOTE (Plan 115b3 §4C): the overlap window is typically only a few "
        "weeks wide (~16 days observed 2026-07-15) and MOVES — a single run "
        "is only a small sample; accumulate over several monthly cycles "
        "before treating this number as stable."
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reference-comparison GO/NO-GO gate (Plan 115b3).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--skip-live-tail",
        action="store_true",
        default=False,
        help="Skip the 4C/4D live-tail fetch — reference comparison (4A/4B) only.",
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

    log.info("database_connecting", url=database_url.split("@")[-1])
    engine = sa.create_engine(database_url, pool_pre_ping=True)
    clock = lambda: ensure_utc(datetime.now(UTC))  # noqa: E731

    try:
        with engine.connect() as conn:
            from sapphire_flow.store.basin_store import PgBasinStore
            from sapphire_flow.store.historical_forcing_store import (
                PgHistoricalForcingStore,
            )
            from sapphire_flow.store.station_store import PgStationStore

            basin_store = PgBasinStore(conn)
            station_store = PgStationStore(conn)
            forcing_store = PgHistoricalForcingStore(conn)

            log.info("reference_comparison_starting")
            stations = station_store.fetch_all_stations()
            report = run_reference_comparison(forcing_store, stations)
            log.info(
                "reference_comparison_complete",
                basins=len(report.precipitation),
            )
            reference_ok = _print_reference_report(report)

            if args.skip_live_tail:
                return 0 if reference_ok else 1

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
            station_configs = eligible_meteoswiss_configs(stations, basin_store)

            log.info("live_tail_overlap_discovery_starting")
            window = discover_overlap_window(reanalysis_adapter)
            if window is None:
                log.warning("live_tail_no_overlap")
                _print_live_tail_result(None)
                return 0 if reference_ok else 1

            log.info(
                "live_tail_fetch_starting",
                window_start=window.start.isoformat(),
                window_end=window.end.isoformat(),
            )
            rhiresd_rows, rprelimd_rows = fetch_overlap_products(
                reanalysis_adapter, station_configs, window
            )
            live_tail_result = compute_live_tail_residual(
                rhiresd_rows, rprelimd_rows, window
            )
            log.info(
                "live_tail_complete",
                n_paired=live_tail_result.n_paired,
                mean_bias=live_tail_result.mean_bias,
                rmse=live_tail_result.rmse,
            )
            _print_live_tail_result(live_tail_result)
    except Exception as exc:
        log.error("validation_gate_failed", error=str(exc))
        print(f"\nERROR: validation gate run failed — {exc}", file=sys.stderr)
        return 1

    return 0 if reference_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)
