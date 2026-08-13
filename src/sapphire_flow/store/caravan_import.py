"""Plan 155 T1/T1b — additively import Caravan's CAMELS-CH attributes.

Persistence orchestration for `adapters/caravan_attributes.py`'s pure parser:
join each parquet row onto its matching `(network, code)` station, and merge
the namespaced attributes into that station's basin via
`PgBasinStore.merge_namespaced_attributes` -- never the basin-PACKAGE
importer's correction branch (`store/basin_importer.py`), which is the WRONG
tool here (see that module's docstring and `merge_namespaced_attributes`'s):
this ADDS a disjoint keyspace, it never corrects an existing package.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import structlog

from sapphire_flow.adapters.caravan_attributes import load_caravan_attribute_rows
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.caravan_statics import (
    CARAVAN_PREFIX,
    StaticCoverageGap,
    verify_static_coverage,
)
from sapphire_flow.types.caravan_attributes import (
    CaravanImportProvenance,
    CaravanImportResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.store.basin_store import PgBasinStore
    from sapphire_flow.store.station_store import PgStationStore
    from sapphire_flow.types.ids import BasinId

log = structlog.get_logger(__name__)


def _fingerprint_rows(rows: dict[str, dict[str, Any]]) -> str:
    """A stable content identity for the parsed parquet rows (Plan 155
    fixer round: "no fingerprint and no immutability guard") -- a SHA-256
    of a canonical (sorted-keys) JSON serialisation, so re-running the
    import against byte-identical content always yields the same
    fingerprint, and any content change yields a different one."""
    canonical = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def import_caravan_attributes(
    path: str | Path,
    *,
    station_store: PgStationStore,
    basin_store: PgBasinStore,
    network: str = "bafu",
    extractor_version: str | None = None,
    source_dataset_version: str | None = None,
    expected_codes: frozenset[str] | None = None,
    required_static_names: frozenset[str] | None = None,
) -> CaravanImportResult:
    """T0a's frozen manifest is a station-level concept; this joins on
    station identity (T1b step 1: `caravan_camels_ch_<code>` ->
    `(network, code)`) and merges into whatever basin that station is
    ALREADY bound to. A station with no `basin_id` yet (should not occur
    for the T0a manifest -- onboarding assigns a basin before a station is
    a discharge candidate) is reported, not silently skipped-and-forgotten.

    `expected_codes` -- the T0a manifest, when the caller has it -- lets
    this function report a manifest station that never showed up as a row
    in the parquet AT ALL, a gap `matched_codes`/`unmatched_codes` alone
    cannot surface (they only ever see codes the parquet actually
    contains). `source_dataset_version` threads the modeller-confirmed
    release into the returned provenance once known (plan's own
    guidance: ask BEFORE a real import -- see
    `types/caravan_attributes.py::CaravanImportProvenance`); omitted, the
    provenance keeps its honest "unconfirmed" placeholder.

    `required_static_names` (Plan 155 fixer round, major finding: "T1's
    exit gate is neither enforced nor genuinely tested -- no production
    caller invokes it") makes T1's exit gate the OPERATIONAL import's own
    gate rather than an optional, disconnected script: when given, EVERY
    manifest station must end up matched, basin-bound, and resolving all
    of `required_static_names` to finite values via
    `services/caravan_statics.py::verify_static_coverage` (which is itself
    collision-aware -- Plan 155 fixer round: the old exit-gate check
    "does not run collision resolution"). Any shortfall -- an unmatched
    code, a station without a basin, a manifest station missing from the
    parquet, or a per-station coverage gap -- raises `ConfigurationError`
    BEFORE returning, naming every failure. This function does not manage
    its own transaction (the caller's connection does); raising here, in
    the middle of the per-row merge loop having already run, relies on the
    caller's transaction being rolled back on an unhandled exception (the
    standard `engine.begin()` / this repo's test `db_connection` fixture
    pattern) -- the writes already issued to `merge_namespaced_attributes`
    are not durably committed unless the caller commits after this
    function returns successfully.
    """
    rows = load_caravan_attribute_rows(path)

    matched: set[str] = set()
    unmatched: set[str] = set()
    no_basin: set[str] = set()
    matched_basin_ids: dict[str, BasinId] = {}

    for code, raw_attrs in rows.items():
        station = station_store.fetch_station_by_code(code, network)
        if station is None:
            unmatched.add(code)
            continue
        if station.basin_id is None:
            no_basin.add(code)
            continue
        namespaced = {f"{CARAVAN_PREFIX}{col}": val for col, val in raw_attrs.items()}
        basin_store.merge_namespaced_attributes(station.basin_id, attributes=namespaced)
        matched.add(code)
        matched_basin_ids[code] = station.basin_id

    missing_from_manifest: frozenset[str] = (
        frozenset(expected_codes) - rows.keys()
        if expected_codes is not None
        else frozenset()
    )

    coverage_gaps: tuple[StaticCoverageGap, ...] = ()
    if required_static_names is not None:
        coverage_gaps = tuple(
            gap
            for code in sorted(matched_basin_ids)
            for basin in (basin_store.fetch_basin(matched_basin_ids[code]),)
            for gap in verify_static_coverage(
                {code: basin.attributes if basin is not None else None},
                required_static_names,
            )
        )
        if unmatched or no_basin or missing_from_manifest or coverage_gaps:
            gap_report = [
                (g.station_code, sorted(g.missing_statics), g.collision_error)
                for g in coverage_gaps
            ]
            raise ConfigurationError(
                "Plan 155 T1 exit gate failed -- every manifest station must "
                f"match, bind to a basin, and resolve all "
                f"{len(required_static_names)} declared statics to finite "
                "values: "
                f"unmatched={sorted(unmatched)}, "
                f"stations_without_basin={sorted(no_basin)}, "
                f"missing_from_manifest={sorted(missing_from_manifest)}, "
                f"coverage_gaps={gap_report}"
            )

    log.info(
        "caravan_import.complete",
        matched=len(matched),
        unmatched=sorted(unmatched),
        stations_without_basin=sorted(no_basin),
        missing_from_manifest=sorted(missing_from_manifest),
    )
    provenance_kwargs: dict[str, str] = {}
    if source_dataset_version is not None:
        provenance_kwargs["source_dataset_version"] = source_dataset_version
    return CaravanImportResult(
        matched_codes=frozenset(matched),
        unmatched_codes=frozenset(unmatched),
        stations_without_basin=frozenset(no_basin),
        missing_from_manifest=missing_from_manifest,
        provenance=CaravanImportProvenance(
            extractor_version=extractor_version,
            content_fingerprint=_fingerprint_rows(rows),
            **provenance_kwargs,
        ),
    )
