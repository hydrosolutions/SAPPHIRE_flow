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
from sapphire_flow.store._helpers import require_real_transaction
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
    extractor_version: str | None = None,
    source_dataset_version: str | None = None,
    expected_codes: frozenset[str] | None = None,
    required_static_names: frozenset[str] | None = None,
) -> CaravanImportResult:
    """The flexible, INTERNAL building block this module's own
    `run_operational_caravan_import` (below) wraps. Every real production
    caller MUST go through `run_operational_caravan_import`, which makes
    `expected_codes`/`required_static_names` mandatory and non-empty --
    Plan 155 fixer round, BLOCKER finding: "the operational exit gate
    remains optional and trivially bypassable". This function stays
    lenient on purpose: several of its own tests deliberately omit or
    narrow these parameters to exercise the reporting-only paths
    (`unmatched_codes`, `stations_without_basin`, `missing_from_manifest`)
    in isolation, and a future non-gating caller (a dry-run inspection
    script, say) may legitimately want the report without the raise. Do
    NOT call this function directly from a production flow -- call
    `run_operational_caravan_import` instead.

    T0a's frozen manifest is a station-level concept; this joins on
    station identity (T1b step 1: `caravan_camels_ch_<code>` ->
    `("bafu", code)` -- Caravan's CAMELS-CH parser only ever understands a
    `caravan_camels_ch_*` gauge id, so the network is hardcoded rather than
    caller-supplied: Plan 155 fixer round minor finding, a non-"bafu"
    `network` argument could silently attach Swiss CAMELS-CH attributes to
    an unrelated station sharing the same code) and merges into whatever
    basin that station is ALREADY bound to. A station with no `basin_id`
    yet (should not occur for the T0a manifest -- onboarding assigns a
    basin before a station is a discharge candidate) is reported, not
    silently skipped-and-forgotten.

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
    **manifest** (`expected_codes`) station must end up matched,
    basin-bound, and resolving all of `required_static_names` to finite
    values via `services/caravan_statics.py::verify_static_coverage`
    (which is itself collision-aware -- Plan 155 fixer round: the old
    exit-gate check "does not run collision resolution"). Any shortfall --
    an unmatched manifest code, a manifest station without a basin, a
    manifest station missing from the parquet, or a per-manifest-station
    coverage gap -- raises `ConfigurationError` naming every failure.

    Round-2 review BLOCKER: the gate is **manifest-scoped, never
    parquet-scoped**. The delivered parquet legitimately covers hundreds of
    Swiss gauges beyond our own configured stations (Plan 155 T0a: the real
    parquet holds 296 codes against 169 configured stations, 148 overlap --
    the other 148 rows are permanently out of scope BY DESIGN, not a
    failure). A source-only code that matches no configured station, or a
    configured station outside `expected_codes`, is reported via
    `unmatched_codes`/`stations_without_basin` but is **never fatal**. Only
    a station IN `expected_codes` failing to match/bind/cover gates the
    exit -- so `required_static_names` REQUIRES `expected_codes` be given
    alongside it (raises immediately, before any read or write, if only
    one of the two is supplied): omitting `expected_codes` would silently
    make the "gate" validate nothing (every parquet row that happens to
    match is "coverage", with no denominator), while resolving it against
    the raw parquet (the pre-round-2 behaviour) makes the gate raise on
    every run, since real data always carries out-of-scope rows.

    Round-2 review MAJOR (atomicity): `require_real_transaction`
    (`store/_helpers.py`, the same guard `import_basin_package` uses)
    refuses to run at all -- before issuing a single write -- unless
    `basin_store`'s connection is genuinely inside a non-AUTOCOMMIT
    transaction, so a mid-loop gate failure rolls back every write already
    issued instead of leaving a partially-applied import on production's
    AUTOCOMMIT connection.
    """
    if required_static_names is not None and expected_codes is None:
        raise ConfigurationError(
            "import_caravan_attributes: required_static_names was supplied "
            "without expected_codes -- Plan 155's T1 exit gate is scoped to "
            "the T0a MANIFEST, not the source parquet, so it needs the "
            "manifest to gate against (Plan 155 round-2 review BLOCKER: "
            "gating against the raw parquet either always raises on the "
            "permanently out-of-scope rows a real Caravan delivery carries, "
            "or -- omitted entirely -- silently validates nothing)"
        )
    require_real_transaction(basin_store.connection, caller="import_caravan_attributes")

    rows = load_caravan_attribute_rows(path)

    matched: set[str] = set()
    unmatched: set[str] = set()
    no_basin: set[str] = set()
    matched_basin_ids: dict[str, BasinId] = {}

    for code, raw_attrs in rows.items():
        station = station_store.fetch_station_by_code(code, "bafu")
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
        # expected_codes is guaranteed not-None here (checked above).
        manifest = frozenset(expected_codes)  # type: ignore[arg-type]
        manifest_unmatched = unmatched & manifest
        manifest_no_basin = no_basin & manifest
        manifest_codes_matched = sorted(matched_basin_ids.keys() & manifest)
        coverage_gaps = tuple(
            gap
            for code in manifest_codes_matched
            for basin in (basin_store.fetch_basin(matched_basin_ids[code]),)
            for gap in verify_static_coverage(
                {code: basin.attributes if basin is not None else None},
                required_static_names,
            )
        )
        gate_failed = bool(
            manifest_unmatched
            or manifest_no_basin
            or missing_from_manifest
            or coverage_gaps
        )
        if gate_failed:
            gap_report = [
                (g.station_code, sorted(g.missing_statics), g.collision_error)
                for g in coverage_gaps
            ]
            raise ConfigurationError(
                "Plan 155 T1 exit gate failed -- every MANIFEST station "
                f"must match, bind to a basin, and resolve all "
                f"{len(required_static_names)} declared statics to finite "
                "values (source-only codes outside the manifest are never "
                "fatal): "
                f"unmatched={sorted(manifest_unmatched)}, "
                f"stations_without_basin={sorted(manifest_no_basin)}, "
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


def run_operational_caravan_import(
    path: str | Path,
    *,
    station_store: PgStationStore,
    basin_store: PgBasinStore,
    expected_codes: frozenset[str],
    required_static_names: frozenset[str],
    extractor_version: str | None = None,
    source_dataset_version: str | None = None,
) -> CaravanImportResult:
    """THE operational entrypoint for Plan 155's T1 import -- the only
    caller a production flow may use. Fixer round, BLOCKER finding: the
    review reported that `import_caravan_attributes` "defaults both the
    148-station manifest and required statics to None ... calling the
    function with both omitted writes data and returns success without
    validating any station/static" and that "empty sets also validate
    nothing". Both are closed HERE, structurally:

    - `expected_codes` and `required_static_names` are ordinary (no
      default) keyword-only parameters -- a caller cannot omit either one
      and still get past Python's own call-time `TypeError`, so the
      "both omitted" bypass the review found is no longer reachable
      through this entrypoint at all.
    - Both are additionally checked for NON-EMPTINESS at runtime, before
      any read or write: an explicit `frozenset()` is a caller error, not
      a silently-accepted "nothing to validate" gate, since the round-2
      review's own "gate on the manifest" fix makes an empty manifest
      validate vacuously (`missing_from_manifest`/`coverage_gaps` are
      empty by construction over an empty set).

    This function is deliberately a thin, no-defaults wrapper around
    `import_caravan_attributes` (the flexible internal building block,
    still directly unit/integration-tested for its own reporting-only
    behaviour) rather than a rewrite: the manifest-scoped gate logic lives
    in exactly one place. The concrete 148-station T0a manifest and PT's
    real 50 declared static names are NOT hardcoded here -- per the
    original fixer round's own note, the concrete PT static-name set is
    aquacast's contract, not this module's, and the live manifest is a
    database query the modeller's confirmed Caravan release must run
    against (still-pending operational follow-on, unchanged by this fix).
    A future onboarding/operational script supplies both explicitly.
    """
    if not expected_codes:
        raise ConfigurationError(
            "run_operational_caravan_import: expected_codes must be a "
            "non-empty T0a manifest -- an empty manifest makes every "
            "manifest-scoped check (unmatched/no_basin/missing_from_"
            "manifest/coverage_gaps) vacuously pass, silently disabling "
            "the exit gate (Plan 155 fixer round BLOCKER)"
        )
    if not required_static_names:
        raise ConfigurationError(
            "run_operational_caravan_import: required_static_names must be "
            "a non-empty set of the model's declared statics -- an empty "
            "set makes verify_static_coverage report zero gaps for every "
            "station, silently disabling the exit gate (Plan 155 fixer "
            "round BLOCKER)"
        )
    return import_caravan_attributes(
        path,
        station_store=station_store,
        basin_store=basin_store,
        extractor_version=extractor_version,
        source_dataset_version=source_dataset_version,
        expected_codes=expected_codes,
        required_static_names=required_static_names,
    )
