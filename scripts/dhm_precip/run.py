#!/usr/bin/env python3
# ruff: noqa: T201
"""M-A1 runner — reproducible DHM precipitation ingest and baseline statistics
(Plan 170, `docs/plans/archive/170-dhm-precip-reproducible-baseline.md`).

Loads the sha256-pinned production workbook and the D12 coordinate table,
builds the RAW and ON_GRID views (D6), computes every statistic family
(Phase 2), and writes parquet tables + a Markdown summary + `results.json`
(the D9 `RunManifest`) to `--out`.

Usage:
    uv run python scripts/dhm_precip/run.py --out <dir>

Environment:
    DHM_PRECIP_XLSX    path to the source workbook (required, D9b: the CLI
                       always verifies against the pinned production sha256 —
                       there is no override)
    DHM_PRECIP_COORDS  path to the D12 coordinate table (optional, defaults
                       to data/dhm_precip/station_coordinates.csv)

Exit codes (D9):
    0  success — ingestion, computation and serialisation all completed.
       Expectation evaluation is the gate's job (evaluate.py), not this
       CLI's — a value mismatch never changes this exit code.
    2  DHM_PRECIP_XLSX unset, or the workbook/coordinate path is unreadable
    3  sha256 mismatch against the pinned production digest
    4  schema mismatch (workbook column inventory or coordinate table)
    5  parse failure (workbook or coordinate table)
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, fields
from datetime import UTC, datetime
from pathlib import Path

# Bootstrap: running this file directly (`python scripts/dhm_precip/run.py`,
# per the Usage line above) puts this file's own directory at sys.path[0],
# not the repo root — `scripts.dhm_precip.*` would not resolve. Insert the
# repo root ahead of it so the package imports below work either way (direct
# script execution or `-m scripts.dhm_precip.run`).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import structlog  # noqa: E402

from sapphire_flow.types.datetime import ensure_utc  # noqa: E402
from scripts.dhm_precip.domain_types import RunManifest, Station, View  # noqa: E402
from scripts.dhm_precip.loader import (  # noqa: E402
    PRODUCTION_SOURCE_SHA256,
    DhmPrecipLoaderError,
    ParseFailureError,
    SchemaMismatchError,
    Sha256MismatchError,
    SourcePathUnsetError,
    SourceUnreadableError,
    load_long_frame,
    load_station_coordinates,
    resolve_coords_path,
    resolve_source_path,
)
from scripts.dhm_precip.manifest_io import write_manifest  # noqa: E402
from scripts.dhm_precip.params import DEFAULT_PARAMS  # noqa: E402
from scripts.dhm_precip.pipeline import (  # noqa: E402
    ComputedTables,
    compute_all,
    extract_values,
    table_declarations,
)
from scripts.dhm_precip.views import (  # noqa: E402
    compute_view_counts,
    on_grid_view,
    raw_view,
)

log = structlog.get_logger(__name__)

_EXIT_BY_ERROR: dict[type[DhmPrecipLoaderError], int] = {
    SourcePathUnsetError: 2,
    SourceUnreadableError: 2,
    Sha256MismatchError: 3,
    SchemaMismatchError: 4,
    ParseFailureError: 5,
}


def _exit_code_for(exc: DhmPrecipLoaderError) -> int:
    return _EXIT_BY_ERROR.get(type(exc), 5)


def run(out: Path) -> int:
    try:
        source_path = resolve_source_path()
        long_frame, inventory = load_long_frame(
            source_path, expected_sha256=PRODUCTION_SOURCE_SHA256
        )
        live_stations = frozenset(
            Station(name)
            for name in inventory.all_columns
            if name not in inventory.empty_columns
        )
        coords_path = resolve_coords_path()
        stations = load_station_coordinates(
            coords_path, expected_stations=live_stations
        )
    except DhmPrecipLoaderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _exit_code_for(exc)

    # D9/CLAUDE.md — a single injected clock reading, never a bare
    # `datetime.now()` inside `compute_all`'s business logic; reused for
    # both the manifest's timestamps and `Observation.created_at` (task 2a).
    generated_at = datetime.now(UTC)

    raw = raw_view(long_frame)
    on_grid = on_grid_view(long_frame, DEFAULT_PARAMS)
    tables = compute_all(
        raw, on_grid, inventory, stations, DEFAULT_PARAMS, now=ensure_utc(generated_at)
    )
    values = extract_values(tables)
    declarations = table_declarations(tables)

    out.mkdir(parents=True, exist_ok=True)
    tables_dir = out / "tables"
    tables_dir.mkdir(exist_ok=True)
    for field in fields(ComputedTables):
        frame = getattr(tables, field.name)
        frame.write_parquet(tables_dir / f"{field.name}.parquet")

    manifest = RunManifest(
        run_id=generated_at.strftime("%Y%m%dT%H%M%SZ"),
        source_path=str(source_path),
        source_sha256=PRODUCTION_SOURCE_SHA256,
        generated_at=generated_at,
        parameters=asdict(DEFAULT_PARAMS),
        counts_by_view={
            View.RAW.value: compute_view_counts(raw),
            View.ON_GRID.value: compute_view_counts(on_grid),
        },
        tables=declarations,
        values=values,
    )
    write_manifest(manifest, out / "results.json")
    _write_summary(out / "summary.md", manifest)
    log.info("dhm_precip.run.complete", out=str(out), n_values=len(values))
    print(f"wrote {out}")
    return 0


def _write_summary(path: Path, manifest: RunManifest) -> None:
    lines = [
        "# DHM precipitation — M-A1 baseline run",
        "",
        f"- run id: `{manifest.run_id}`",
        f"- source: `{manifest.source_path}`",
        f"- sha256: `{manifest.source_sha256}`",
        "",
        "## Computed values",
        "",
    ]
    lines.extend(
        f"- `{key}`: {value}" for key, value in sorted(manifest.values.items())
    )
    path.write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="output directory for artefacts"
    )
    args = parser.parse_args(argv)
    return run(args.out)


if __name__ == "__main__":
    sys.exit(main())
