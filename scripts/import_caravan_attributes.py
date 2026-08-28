#!/usr/bin/env python3
# ruff: noqa: T201
"""The operator entrypoint for Plan 155's Caravan statics import (Plan 188 T1).

Plan 155 built and tested `store/caravan_import.py::run_operational_caravan_import` --
the manifest-scoped exit gate, transaction atomicity, collision-aware coverage
checks and identical-replay protection all live there and are NOT re-opened
here. This script only wires up the two things nothing yet calls it with:

- **D1** -- the T0a 148-station manifest, re-derived LIVE from station data
  (`derive_swiss_caravan_manifest`) and cross-checked by SET IDENTITY against
  one pinned literal, `SWISS_CARAVAN_MANIFEST_CODES` below.
- **D2** -- the required static names, read from the DISCOVERED `cmal_pool_pt`
  adapter (`resolve_required_static_names`) -- never a hand-typed literal.

Structured like `scripts/backfill_meteoswiss_history.py`, but NOT copying its
AUTOCOMMIT connection: `run_operational_caravan_import` refuses to run at all
unless the caller is inside a real, already-open transaction
(`store/_helpers.py::require_real_transaction`), so this script always opens
one via `engine.begin()`.

**D3**: `--dry-run` runs the FULL gate (a real write attempt, gated, inside
the same transaction) and then rolls back -- implemented by raising a private
sentinel from *inside* `with engine.begin():` so the rollback is structural,
not a flag the code must remember to honour.

Usage:
    uv run python scripts/import_caravan_attributes.py --parquet data/caravan.parquet
    uv run python scripts/import_caravan_attributes.py --parquet data/caravan.parquet \
        --dry-run

Environment:
    DATABASE_URL     PostgreSQL connection string (required)
    SAPPHIRE_ENV     Set to "dev" for human-readable console log output

Two cuts taken from review, deliberately (Plan 188 T1): no Alembic run (the
deployment's `init` service already migrates, and this script adds no
schema), and no coverage-gap count (a clean run's success proves it is zero;
a gate failure raises before returning a result that carries no coverage
field at all).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
import structlog

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.logging import configure_api_logging
from sapphire_flow.types.enums import StationKind
from sapphire_flow.types.ids import AQUACAST_CMAL_POOL_PT_MODEL_ID, ModelId
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import StationStore
    from sapphire_flow.types.caravan_attributes import CaravanImportResult

configure_api_logging()
log = structlog.get_logger(__name__)

# Plan 155 T0a's one owner-dropped survivor: 2446, Gampelen-Zihlbrücke, the
# regulated Zihlkanal outflow -- typed `stream` with real observed discharge
# (so it mechanically passes the two-signal freeze) but its "catchment" is
# the entire upstream lake system and its discharge is a regulation
# decision, not a rainfall-runoff response. See docs/plans/155, "T0a result".
_DROPPED_GAMPELEN_ZIHLBRUECKE_CODE: Final[str] = "2446"

# Plan 188 D1's pinned cross-check literal for the T0a 148-station manifest
# (docs/plans/155-swiss-data-readiness-for-aquacast.md, "T0a result"): the
# live-derived manifest (`derive_swiss_caravan_manifest`) must match this
# SET EXACTLY, not merely in cardinality -- a fleet that loses one station
# and gains another would pass a `len(...) == 148` check silently.
#
# Derived (not hand-typed) from `config.toml`'s `[onboarding] basin_ids`
# (169 codes, the onboarding manifest) minus Plan 155 T0a's two published
# exclusion sets: the 20 lake-typed gauges (zero observed discharge, listed
# by code in docs/plans/155-swiss-data-readiness-for-aquacast.md, "T0a
# result") and the one owner-dropped regulated canal, `2446`
# Gampelen-Zihlbrücke (`_DROPPED_GAMPELEN_ZIHLBRUECKE_CODE` above). 169 - 20
# - 1 = 148, matching T0a's frozen result exactly (Plan 188 fixer round,
# 2026-08-27, cross-checked against the published table). A future change
# to the onboarding manifest must update this literal by the same
# derivation, not by hand-editing individual codes.
SWISS_CARAVAN_MANIFEST_CODES: Final[frozenset[str]] = frozenset(
    {
        "2009",
        "2011",
        "2016",
        "2018",
        "2019",
        "2020",
        "2024",
        "2029",
        "2033",
        "2041",
        "2044",
        "2056",
        "2063",
        "2068",
        "2084",
        "2085",
        "2086",
        "2087",
        "2091",
        "2099",
        "2104",
        "2106",
        "2109",
        "2110",
        "2112",
        "2116",
        "2117",
        "2119",
        "2122",
        "2126",
        "2130",
        "2132",
        "2135",
        "2139",
        "2141",
        "2143",
        "2152",
        "2155",
        "2160",
        "2167",
        "2170",
        "2174",
        "2176",
        "2179",
        "2181",
        "2185",
        "2187",
        "2199",
        "2200",
        "2202",
        "2203",
        "2206",
        "2210",
        "2215",
        "2219",
        "2232",
        "2239",
        "2243",
        "2251",
        "2252",
        "2256",
        "2262",
        "2263",
        "2265",
        "2268",
        "2269",
        "2276",
        "2283",
        "2288",
        "2289",
        "2290",
        "2300",
        "2303",
        "2305",
        "2307",
        "2308",
        "2312",
        "2319",
        "2321",
        "2342",
        "2343",
        "2346",
        "2347",
        "2355",
        "2356",
        "2364",
        "2366",
        "2368",
        "2369",
        "2370",
        "2371",
        "2372",
        "2374",
        "2378",
        "2392",
        "2409",
        "2410",
        "2412",
        "2414",
        "2416",
        "2417",
        "2418",
        "2419",
        "2420",
        "2426",
        "2430",
        "2433",
        "2434",
        "2457",
        "2458",
        "2461",
        "2471",
        "2473",
        "2474",
        "2475",
        "2477",
        "2478",
        "2480",
        "2481",
        "2485",
        "2486",
        "2488",
        "2490",
        "2491",
        "2493",
        "2494",
        "2497",
        "2498",
        "2500",
        "2602",
        "2603",
        "2604",
        "2605",
        "2607",
        "2608",
        "2609",
        "2610",
        "2612",
        "2613",
        "2615",
        "2617",
        "2620",
        "2623",
        "2629",
        "2630",
        "2631",
        "2634",
        "2640",
    }
)
# Fail fast on a typo'd literal above rather than silently drift from T0a's
# frozen 148-station count.
assert len(SWISS_CARAVAN_MANIFEST_CODES) == 148, (  # noqa: S101
    "SWISS_CARAVAN_MANIFEST_CODES must carry exactly 148 codes (Plan 155 "
    f"T0a: 169 onboarding codes - 20 lakes - 1 dropped canal), got "
    f"{len(SWISS_CARAVAN_MANIFEST_CODES)}"
)


def derive_swiss_caravan_manifest(station_store: StationStore) -> frozenset[str]:
    """Plan 188 D1's manifest rule, re-derived LIVE from station data --
    mirrors Plan 155 T0a's own two-signal freeze (docs/plans/155, "T0a --
    provisional manifest ... from station data alone"): a BAFU-network,
    `sapphire`-tenant, `StationKind.RIVER` station whose `forecast_targets`
    AND `measured_parameters` both carry `"discharge"`, minus the one
    owner-dropped regulated canal."""
    return frozenset(
        station.code
        for station in station_store.fetch_all_stations(kind=StationKind.RIVER)
        if station.network == "bafu"
        and station.tenant_id == DEFAULT_TENANT_ID
        and station.forecast_targets is not None
        and "discharge" in station.forecast_targets
        and "discharge" in station.measured_parameters
        and station.code != _DROPPED_GAMPELEN_ZIHLBRUECKE_CODE
    )


def check_manifest_matches_pinned(
    derived: frozenset[str], pinned: frozenset[str]
) -> None:
    """Plan 188 D1: SET IDENTITY, not cardinality -- `len(derived) ==
    len(pinned)` also passes a fleet that lost one station and gained
    another. Raises `ConfigurationError` naming the full symmetric
    difference (codes only on one side) before any parquet read or DB
    write."""
    if derived == pinned:
        return
    only_derived = sorted(derived - pinned)
    only_pinned = sorted(pinned - derived)
    raise ConfigurationError(
        "derived Caravan manifest does not match SWISS_CARAVAN_MANIFEST_CODES "
        f"(Plan 188 D1) -- derived has {len(derived)} station(s), pinned has "
        f"{len(pinned)}; only in derived (not pinned)={only_derived}; only in "
        f"pinned (not derived)={only_pinned}"
    )


def resolve_required_static_names(
    *, model_id: ModelId = AQUACAST_CMAL_POOL_PT_MODEL_ID
) -> frozenset[str]:
    """Plan 188 D2: read required statics from the DISCOVERED adapter,
    never a hand-typed literal -- the model object constructs its own
    `ModelDataRequirements.static_features`; the DB `models` table carries
    no requirements field at all. `discover_models()` swallows an
    entry-point import failure and omits the model from the returned dict
    (`services/model_registry.py`), so a missing `aquacast` extra makes the
    model simply absent -- this does the explicit named preflight so the
    operator sees which model and which extra, rather than a bare
    `KeyError`."""
    from sapphire_flow.services.model_registry import discover_models

    models = discover_models()
    if model_id not in models:
        raise ConfigurationError(
            f"model {model_id!r} is not registered -- discover_models() found "
            f"{sorted(models)} -- install the 'aquacast' extra "
            "(`uv sync --extra aquacast`) so this entry point resolves "
            "(Plan 188 D2)"
        )
    return frozenset(models[model_id].data_requirements.static_features)


class _DryRunRollbackError(Exception):
    """Plan 188 D3: raised from inside `with engine.begin():` so a dry run's
    rollback is structural -- the transaction context manager rolls back on
    any exception -- rather than a flag the code must remember to honour."""


def _print_result(result: CaravanImportResult, *, dry_run: bool) -> None:
    print()
    print(
        "=== DRY RUN (gate ran; rolled back -- no writes persisted) ==="
        if dry_run
        else "=== Caravan Statics Import ==="
    )
    print(f"Matched:                {len(result.matched_codes):,}")
    print(f"Unmatched (parquet):    {len(result.unmatched_codes):,}")
    print(f"Stations without basin: {len(result.stations_without_basin):,}")
    print(f"Missing from manifest:  {len(result.missing_from_manifest):,}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Caravan's CAMELS-CH statics for the Swiss fleet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--parquet",
        required=True,
        type=Path,
        metavar="PATH",
        help="Path to the delivered Caravan CAMELS-CH attributes parquet.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run the full gate and roll back -- no writes persist.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "ERROR: DATABASE_URL environment variable is not set.",
            file=sys.stderr,
        )
        return 1

    try:
        required_static_names = resolve_required_static_names()
    except ConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Independent review 2026-08-27: do NOT log any part of the DSN.
    # `split("@")[-1]` strips a userinfo password but leaks a query-parameter
    # one -- `postgresql://app@db/sapphire?password=hunter2` logs the password
    # verbatim (demonstrated). There is nothing here worth the risk: the
    # operator already knows which database they pointed the CLI at.
    log.info("database_connecting")
    engine = sa.create_engine(database_url, pool_pre_ping=True)

    result: CaravanImportResult | None = None
    try:
        with engine.begin() as conn:
            from sapphire_flow.store.basin_store import PgBasinStore
            from sapphire_flow.store.caravan_import import (
                run_operational_caravan_import,
            )
            from sapphire_flow.store.station_store import PgStationStore

            station_store = PgStationStore(conn)
            basin_store = PgBasinStore(conn)

            derived_manifest = derive_swiss_caravan_manifest(station_store)
            check_manifest_matches_pinned(
                derived_manifest, SWISS_CARAVAN_MANIFEST_CODES
            )

            log.info(
                "caravan_import_starting",
                manifest_size=len(derived_manifest),
                dry_run=args.dry_run,
            )
            result = run_operational_caravan_import(
                args.parquet,
                station_store=station_store,
                basin_store=basin_store,
                expected_codes=derived_manifest,
                required_static_names=required_static_names,
            )
            if args.dry_run:
                raise _DryRunRollbackError
    except _DryRunRollbackError:
        pass
    except ConfigurationError as exc:
        log.error("caravan_import_failed", error=str(exc))
        print(f"\nERROR: Caravan import failed -- {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    assert result is not None  # noqa: S101 -- unreachable with no exception above
    _print_result(result, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)
