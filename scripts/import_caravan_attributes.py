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
# NOT POPULATED HERE. The concrete 148-code list lives only in the
# PRODUCTION database (station config is DB-seeded, never TOML/repo-checked
# -- CLAUDE.md "Station+model config in DB"); this dev checkout has no
# access to it, and only Plan 155's 21-station EXCLUSION list (20 lakes +
# `2446`) is published in-repo -- the base 169-station set it is drawn from
# is not. Populate this constant from the production DB before running T4
# (`SELECT code FROM stations WHERE ...` -- the query
# `derive_swiss_caravan_manifest` below runs, read back and pinned), review
# it once against Plan 155's published table, then remove the `main()` guard
# below that refuses to run while this is empty.
SWISS_CARAVAN_MANIFEST_CODES: Final[frozenset[str]] = frozenset()


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

    if not SWISS_CARAVAN_MANIFEST_CODES:
        print(
            "ERROR: SWISS_CARAVAN_MANIFEST_CODES is an empty placeholder -- "
            "populate it from the production DB before running this CLI "
            "(see the constant's docstring in this script, Plan 188 D1).",
            file=sys.stderr,
        )
        return 1

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

    log.info("database_connecting", url=database_url.split("@")[-1])
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
