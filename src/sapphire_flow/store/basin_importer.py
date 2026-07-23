# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Basin/static package importer — persistence (Plan 120 Phase 2 — Task 2A
new-basin insert, Task 2C idempotency/correction branch, and the Task 2B
PACKAGE-DRIVEN population of the §5a mapping table).

Consumes the Phase-1 loader/acceptance output
(``services/basin_package_loader.py``) and writes it through the existing
atomic, invariant-enforcing store paths (``PgBasinStore.store_basin`` /
``.update_basin_from_package``; 082's
``RecapGatewayPolygonStore.store_binding``) — never separate ad hoc SQL. See
the plan's "Versioned basin state" § "Canonical write pipeline" for the
single source of truth on the FK-order / partial-index reasoning this module
implements but does not re-derive.

Task 3A (the CLI entrypoint + the full accepted/held/rejected acceptance
report) is a later slice; :func:`import_basin_package` is the write-side
function that slice will wrap.

**Transaction contract (fixer round, 2026-07-23, blocker).** This module runs
several statements per package (provenance insert, per-basin
insert/correction, station-basin binding, the §5a replace) with no internal
transaction of its own — atomicity depends entirely on the caller's
connection. Production flow connections run under `isolation_level=
"AUTOCOMMIT"` (`flows/_db.py::setup_production_stores`), where each
individual statement commits the instant it runs — including, empirically,
statements issued after an explicit `conn.begin()` on that connection: a
`ROLLBACK` after a failure does **not** undo work already executed under
AUTOCOMMIT (verified against a live Postgres: a `begin()`/execute/execute
(fails)/`rollback()` sequence on an AUTOCOMMIT-isolation connection leaves
the first statement's row committed). `import_basin_package` therefore
REFUSES to run at all unless `conn` is demonstrably inside a real,
non-AUTOCOMMIT transaction (`_require_real_transaction` below) — a
mid-pipeline failure on a connection that passes this guard genuinely rolls
back the whole package, because ordinary Postgres transactions (opened via
`engine.connect()` + `conn.begin()`, or `engine.begin()`) are atomic across
multiple statements by construction; only the AUTOCOMMIT special case breaks
that guarantee. The guard is the enforcement point Task 3A's future
orchestration MUST satisfy — acquire a connection via `engine.connect()`
(NOT the shared AUTOCOMMIT production connection) and wrap the whole
`import_basin_package` call in `conn.begin()`, or use `engine.begin()`
directly.
"""

from __future__ import annotations

import math
import uuid
from typing import TYPE_CHECKING, Any, Literal

import sqlalchemy as sa
import structlog
from shapely.geometry import MultiPolygon, Polygon, mapping

from sapphire_flow.db.metadata import (
    basin_static_packages,
    model_artifact_basin_versions,
    stations,
)
from sapphire_flow.exceptions import BasinPackageRejectedError
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.basin_package import (
    BasinPackageImportResult,
    ImportedBasin,
    compute_package_fingerprint,
)
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import ArtifactId, BasinId, PackageId, StationId
from sapphire_flow.types.station import GatewayPolygonBindingRow

if TYPE_CHECKING:
    from collections.abc import Callable

    from shapely.geometry.base import BaseGeometry

    from sapphire_flow.types.basin_package import (
        BandRecord,
        BasinAcceptanceDecision,
        BasinPackageAcceptanceReport,
        BasinRecord,
        ClimatologyWindow,
        LoadedBasinPackage,
        SourceDataset,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import BasinVersionId

log = structlog.get_logger(__name__)


def import_basin_package(
    conn: sa.Connection,
    loaded: LoadedBasinPackage,
    acceptance_report: BasinPackageAcceptanceReport,
    *,
    clock: Callable[[], UtcDatetime],
) -> BasinPackageImportResult:
    """Persist an ACCEPTED basin/static package.

    Runs the canonical write pipeline (plan "Versioned basin state") for
    every basin in ``acceptance_report.accepted`` — package provenance FIRST,
    then per basin: a new ``(network, basin_code)`` inserts via
    ``PgBasinStore.store_basin`` (Task 2A); an existing one corrects via
    ``PgBasinStore.update_basin_from_package`` (Task 2C, Decision B); either
    way the §5a ``basin_average`` row is written/replaced LAST via 082's
    ``RecapGatewayPolygonStore.store_binding`` (Task 2B population).

    A basin already in the DB but ABSENT from this package is left
    completely untouched (Decision A — packages are incremental/regional,
    absence carries no signal). Runs within whatever transaction the caller
    already has open on ``conn`` (Task 2A: "one DB transaction per package");
    this function does not open or commit a transaction itself, but it DOES
    require one already be open (see module docstring "Transaction
    contract") — it raises :class:`RuntimeError` immediately, before writing
    anything, if ``conn`` is AUTOCOMMIT-isolation or has no active
    transaction.

    Before any write, validates every accepted decision's station identity
    against the loaded package (fixer round, major finding, 2026-07-23):
    ``decision.station_code`` must match the loaded basin's own
    ``station_code``, and the resolved station's own ``code``/``network``
    must match the basin's — a mismatch (e.g. a stale acceptance report
    paired with a package whose basin key now names a different station)
    raises :class:`BasinPackageRejectedError` and rejects the whole
    package (see ``_validate_decision_identity``).

    Also binds the accepted station to the imported/corrected basin
    (``stations.basin_id`` — fixer round, major finding): without this,
    ``assemble_station_training_data``/``record_artifact_basin_lineage``
    can never reach the basin this package just wrote. A station already
    bound to a DIFFERENT basin is a conflict — raises
    :class:`BasinPackageRejectedError` rather than silently remapping it.

    Idempotent at the PACKAGE level, keyed on the CANONICAL FINGERPRINT
    (``compute_package_fingerprint`` — validated manifest metadata + computed
    payload checksums): re-importing the identical package (same
    ``package_id``, identical fingerprint) is a no-op — returns
    ``already_imported=True`` and touches no row. A ``package_id`` reused with
    ANY differing fingerprint field — including a manifest-only mutation (e.g.
    a changed ``climatology_window``/``source_datasets``) with unchanged
    payload checksums — raises :class:`BasinPackageRejectedError`: packages are
    immutable once accepted (contract §11, ``04:676``); a content change
    requires a new ``package_id`` (fixer round finding 3, 2026-07-23).
    """
    _require_real_transaction(conn)
    package_id = PackageId(loaded.manifest.package_id)

    # Finding 1(a): BIND the acceptance report to the EXACT loaded package.
    # The report carries the canonical fingerprint of the package it was
    # produced against; recompute it here and reject a mismatch BEFORE any
    # idempotency check or write — a report can never be silently applied to
    # a different package than the one it was evaluated on.
    fingerprint = compute_package_fingerprint(loaded)
    if acceptance_report.fingerprint != fingerprint:
        raise BasinPackageRejectedError(
            f"acceptance report fingerprint {acceptance_report.fingerprint!r} does "
            f"not match the loaded package fingerprint {fingerprint!r} — the report "
            "was produced from a DIFFERENT package than the one supplied; refusing "
            "to persist decisions that do not correspond to this package"
        )

    # Finding 1(b): the decision set must EXACTLY cover the package's basins —
    # no duplicate, missing, or extra decision. (`_basin_for_decision` only
    # catches an ACCEPTED decision naming an absent basin; this rejects a
    # report that omits a package basin, double-decides one, or is otherwise
    # not a 1:1 cover of the package.)
    _verify_decisions_cover_package(acceptance_report, loaded)

    decision = _package_import_decision(conn, package_id, fingerprint)
    if decision == "no_op":
        log.info("basin_importer.package_already_imported", package_id=package_id)
        return BasinPackageImportResult(package_id=package_id, already_imported=True)

    basin_store = PgBasinStore(conn)
    gateway_store = RecapGatewayPolygonStore(conn)
    station_store = PgStationStore(conn)
    basin_by_key = {(b.network, b.basin_code): b for b in loaded.basins}
    coverage_by_key = _coverage_by_key(loaded)

    # Fixer round (findings 1(c) + 2, 2026-07-23): re-enforce the
    # persistence-critical invariants for EVERY accepted decision BEFORE any
    # write — the `basin_static_packages` provenance row must not be inserted
    # if any accepted basin fails a write invariant. The write path never
    # trusts the "accepted" label alone:
    #   - `_reenforce_write_invariants` independently re-derives geometry,
    #     area, and coverage from the package (finding 1(c)), and rejects an
    #     "accepted" decision that still carries hold reasons;
    #   - `_validate_decision_identity` re-checks station identity;
    #   - `_reject_correction_station_migration` rejects a correction whose
    #     resolved station differs from the basin's EXISTING station binding
    #     (finding 2 — a silent station migration would leave BOTH stations
    #     bound with two §5a rows).
    for basin_decision in acceptance_report.accepted:
        basin = _basin_for_decision(basin_by_key, basin_decision)
        _reenforce_write_invariants(
            basin,
            decision=basin_decision,
            coverage=coverage_by_key.get((basin.network, basin.basin_code)),
        )
        _validate_decision_identity(station_store, basin=basin, decision=basin_decision)
        _reject_correction_station_migration(
            conn,
            basin_store,
            basin=basin,
            station_id=_require_station_id(basin_decision),
        )

    # Canonical step 2: package provenance FIRST. `basins.package_id`,
    # `basin_versions.package_id`, and the §5a `package_id` are all
    # IMMEDIATE (non-DEFERRABLE) FKs, so any of them written before this row
    # exists raises a live ForeignKeyViolation.
    conn.execute(
        basin_static_packages.insert().values(
            package_id=package_id,
            network=loaded.manifest.network,
            contract_version=loaded.manifest.contract_version,
            checksums=loaded.computed_checksums,
            extractor_name=loaded.manifest.extractor_name,
            extractor_version=loaded.manifest.extractor_version,
            source_datasets=_serialize_source_datasets(loaded.manifest.source_datasets),
            climatology_window=_serialize_climatology_window(
                loaded.manifest.climatology_window
            ),
            fingerprint=fingerprint,
        )
    )

    imported_at = clock()

    imported_basins = tuple(
        _import_one_basin(
            conn,
            basin_store=basin_store,
            gateway_store=gateway_store,
            station_store=station_store,
            basin=_basin_for_decision(basin_by_key, basin_decision),
            station_id=_require_station_id(basin_decision),
            static_attributes=loaded.static_attributes,
            bands=loaded.bands,
            package_id=package_id,
            imported_at=imported_at,
            clock=clock,
        )
        for basin_decision in acceptance_report.accepted
    )
    return BasinPackageImportResult(
        package_id=package_id,
        already_imported=False,
        imported_basins=imported_basins,
    )


def _package_import_decision(
    conn: sa.Connection, package_id: PackageId, fingerprint: str
) -> Literal["no_op", "proceed"]:
    """Task 2C's idempotency/correction branch, package-level half — now keyed
    on the CANONICAL FINGERPRINT (fixer round finding 3, 2026-07-23), not the
    payload checksums alone: same ``package_id`` + identical stored fingerprint
    is a no-op; same ``package_id`` + ANY differing fingerprint field
    (network/contract_version/extractor version/source_datasets/
    climatology_window/manifest file set OR payload checksums) is an
    immutability violation (raises). An unseen ``package_id`` proceeds
    (per-basin new-vs-correction is then decided per basin in
    :func:`_import_one_basin`).

    Distinguishes ROW ABSENCE from a stored NULL fingerprint (fixer round
    finding 2, 2026-07-23): a pre-0040 ``basin_static_packages`` row carries a
    NULL ``fingerprint``. ``scalar_one_or_none`` would collapse "row present
    with NULL fingerprint" and "package not found" into the same ``None``, so a
    re-import over a legacy row would fall through to ``proceed`` and crash on
    the provenance PRIMARY-KEY insert (``IntegrityError``). We instead fetch the
    ROW: absent → ``proceed``; present with a NULL fingerprint → REJECT
    explicitly (immutability cannot be verified against a fingerprint-less legacy
    row) BEFORE any write."""
    row = conn.execute(
        sa.select(basin_static_packages.c.fingerprint).where(
            basin_static_packages.c.package_id == package_id
        )
    ).one_or_none()
    if row is None:
        return "proceed"  # no row for this package_id → a brand-new package
    existing_fingerprint = row[0]
    if existing_fingerprint is None:
        raise BasinPackageRejectedError(
            f"package {package_id!r} already has a basin_static_packages row with "
            "a NULL fingerprint (a legacy/pre-0040 provenance row) — immutability "
            "cannot be verified against a fingerprint-less row; refusing to "
            "re-import over it (which would crash on the provenance primary key). "
            "A content change requires a NEW package_id (contract §11, 04:676)"
        )
    if existing_fingerprint == fingerprint:
        return "no_op"
    raise BasinPackageRejectedError(
        f"package {package_id!r} was already imported with a different package "
        f"fingerprint (stored {existing_fingerprint!r}, supplied {fingerprint!r}) "
        "— packages are immutable once accepted; a content change (including a "
        "manifest-only metadata change) requires a NEW package_id "
        "(contract §11, 04:676)"
    )


def _verify_decisions_cover_package(
    acceptance_report: BasinPackageAcceptanceReport, loaded: LoadedBasinPackage
) -> None:
    """Finding 1(b): the decision set must be a 1:1 cover of the package's
    basins keyed on ``(network, basin_code)`` — reject a report that
    double-decides a basin, omits one, or decides a basin the package does not
    contain. Without this, a mismatched report could persist a subset (or a
    superset) of the package's basins and silently diverge from the package the
    fingerprint just bound it to."""
    decision_keys = [(d.network, d.basin_code) for d in acceptance_report.decisions]
    seen: set[tuple[str, str]] = set()
    duplicate_keys: set[tuple[str, str]] = set()
    for key in decision_keys:
        if key in seen:
            duplicate_keys.add(key)
        seen.add(key)
    duplicates = sorted(duplicate_keys)
    if duplicates:
        raise BasinPackageRejectedError(
            f"acceptance report has duplicate decision(s) for basin(s) {duplicates} "
            "— the decision set must be a 1:1 cover of the package's basins"
        )
    decision_set = set(decision_keys)
    package_set = {(b.network, b.basin_code) for b in loaded.basins}
    missing = sorted(package_set - decision_set)
    extra = sorted(decision_set - package_set)
    if missing or extra:
        raise BasinPackageRejectedError(
            "acceptance report does not exactly cover the package's basins — "
            f"missing decision(s) for {missing}, extra decision(s) for {extra}; "
            "the report does not correspond to the supplied package"
        )


def _coverage_by_key(
    loaded: LoadedBasinPackage,
) -> dict[tuple[str, str], Any]:
    """Per-basin ``coverage_status`` (from the REQUIRED ``validation_report``)
    keyed on ``(network, basin_code)`` — the same source Task 1B's acceptance
    reads, re-read here so the write boundary can INDEPENDENTLY re-check
    coverage rather than trust the accepted label (finding 1(c))."""
    return {
        (entry.network, entry.basin_code): entry.checks.get("coverage_status")
        for entry in loaded.validation_report.basins
    }


def _reenforce_write_invariants(
    basin: BasinRecord,
    *,
    decision: BasinAcceptanceDecision,
    coverage: Any,
) -> None:
    """Finding 1(c): INDEPENDENTLY re-enforce the persistence-critical
    invariants for an ``accepted`` basin at the write boundary — do NOT trust
    the acceptance label. The geometry, area, and coverage checks re-derive
    from the loaded package itself (so even a report that clears its own
    ``hold_reasons`` cannot smuggle a bad basin past this gate). The
    required-static-feature invariant (§9) is model-assignment-dependent and
    has no package-intrinsic source at the write layer (this Phase-2 slice has
    no DB per-station model-assignment source — see
    ``basin_package_loader.evaluate_basin_acceptance``); it is enforced by
    rejecting an ``accepted`` decision that still carries ANY recorded
    ``hold_reasons`` (an accepted basin has none — a flipped hold→accepted
    decision retains them). Any failure rejects the WHOLE package before the
    provenance row is written."""
    geometry = basin.geometry
    if (
        geometry is None
        or geometry.is_empty
        or not geometry.is_valid
        or geometry.has_z
        or geometry.geom_type not in ("Polygon", "MultiPolygon")
    ):
        raise BasinPackageRejectedError(
            f"accepted basin (network={basin.network!r}, "
            f"basin_code={basin.basin_code!r}) geometry is missing, empty, invalid, "
            "or not a 2-D Polygon/MultiPolygon in EPSG:4326 — the write boundary "
            "re-derived this from the package and refuses to persist it regardless "
            "of the acceptance label (contract §9)"
        )
    if math.isnan(basin.area_km2) or basin.area_km2 <= 0:
        raise BasinPackageRejectedError(
            f"accepted basin (network={basin.network!r}, "
            f"basin_code={basin.basin_code!r}) has non-positive area_km2 "
            f"({basin.area_km2}) — refusing to persist regardless of the "
            "acceptance label (contract §9)"
        )
    if coverage == "outside":
        raise BasinPackageRejectedError(
            f"accepted basin (network={basin.network!r}, "
            f"basin_code={basin.basin_code!r}) lies OUTSIDE required coverage "
            "(validation_report coverage_status) — refusing to persist regardless "
            "of the acceptance label (contract §9)"
        )
    if decision.hold_reasons:
        raise BasinPackageRejectedError(
            f"accepted decision for basin (network={basin.network!r}, "
            f"basin_code={basin.basin_code!r}) still carries hold reason(s) "
            f"{list(decision.hold_reasons)!r} — an 'accepted' outcome must not "
            "carry any recorded hold (required-static-feature §9 hold, or a "
            "flipped hold→accepted decision); refusing to persist"
        )


def _reject_correction_station_migration(
    conn: sa.Connection,
    basin_store: PgBasinStore,
    *,
    basin: BasinRecord,
    station_id: StationId,
) -> None:
    """Finding 2 (major, 2026-07-23): a correction (a new ``package_id`` over an
    EXISTING ``(network, basin_code)``) whose resolved station differs from the
    basin's EXISTING station binding must be REJECTED — the station association
    is part of stable basin identity. Left unchecked, the correction would bind
    the NEW station (``stations.basin_id`` + a new §5a row) while the OLD
    station stayed bound to the same basin, leaving BOTH stations mapped to it
    (two §5a rows). No silent migration: an operator-approved basin→station
    migration is out of Phase-2 scope."""
    existing = basin_store.fetch_basin_by_code(basin.basin_code, basin.network)
    if existing is None:
        return  # a NEW basin — no prior station binding to conflict with
    bound_station_ids = (
        conn.execute(sa.select(stations.c.id).where(stations.c.basin_id == existing.id))
        .scalars()
        .all()
    )
    if bound_station_ids and station_id not in bound_station_ids:
        raise BasinPackageRejectedError(
            f"correction for basin (network={basin.network!r}, "
            f"basin_code={basin.basin_code!r}) resolves to station {station_id}, "
            f"but that basin is already bound to station(s) "
            f"{[str(s) for s in bound_station_ids]} — a correction may not change "
            "the basin's station identity; refusing to silently migrate it (an "
            "operator-approved migration is out of Phase-2 scope)"
        )


def _require_real_transaction(conn: sa.Connection) -> None:
    """Blocker fixer round: refuse to run the multi-statement package
    pipeline unless ``conn`` is genuinely inside a non-AUTOCOMMIT
    transaction. Verified empirically against a live Postgres connection:
    on an ``isolation_level="AUTOCOMMIT"`` connection (production's
    ``flows/_db.py::setup_production_stores``), even an EXPLICIT
    ``conn.begin()`` does not make subsequent statements roll back together
    — a statement executed before a later failure stays committed after
    ``rollback()``. A connection that passes both checks below (no
    AUTOCOMMIT execution option, and an active transaction already open) DOES
    roll back correctly, because ordinary Postgres transactions are atomic
    by construction — see module docstring "Transaction contract"."""
    if conn.get_execution_options().get("isolation_level") == "AUTOCOMMIT":
        raise RuntimeError(
            "import_basin_package refuses to run on an AUTOCOMMIT-isolation "
            "connection — each statement in the package pipeline would "
            "commit independently, so a mid-pipeline failure could leave "
            "provenance/basin/version/§5a writes partially applied. Acquire "
            "a connection via engine.connect() (not the shared production "
            "AUTOCOMMIT connection) and wrap the call in conn.begin(), or "
            "use engine.begin() directly."
        )
    if not conn.in_transaction():
        raise RuntimeError(
            "import_basin_package requires an already-open transaction on "
            "conn (call conn.begin() — or use engine.begin() — before "
            "invoking) so a mid-pipeline failure rolls back the whole "
            "package instead of leaving partial writes."
        )


def _require_station_id(decision: BasinAcceptanceDecision) -> StationId:
    if decision.station_id is None:
        raise ValueError(
            f"accepted basin (network={decision.network!r}, "
            f"basin_code={decision.basin_code!r}) has no resolved station_id "
            "— Task 1B invariant violated (an ACCEPTED decision must carry a "
            "matched station; an unmatched station is an onboarding hold)"
        )
    return decision.station_id


def _basin_for_decision(
    basin_by_key: dict[tuple[str, str], BasinRecord],
    decision: BasinAcceptanceDecision,
) -> BasinRecord:
    """Fail loud, with a clear message, rather than a raw ``KeyError``, if
    the acceptance report references a ``(network, basin_code)`` absent
    from the loaded package — a mismatched/stale report paired with a
    different in-memory package (major finding: never silently proceed)."""
    key = (decision.network, decision.basin_code)
    if key not in basin_by_key:
        raise ValueError(
            f"acceptance report references basin (network={decision.network!r}, "
            f"basin_code={decision.basin_code!r}) that is absent from the "
            "loaded package — the acceptance report does not match the "
            "supplied loaded package"
        )
    return basin_by_key[key]


def _validate_decision_identity(
    station_store: PgStationStore,
    *,
    basin: BasinRecord,
    decision: BasinAcceptanceDecision,
) -> None:
    """Fixer round (major finding, 2026-07-23): before any write, verify a
    stale/mismatched acceptance decision cannot bind the §5a mapping or
    ``stations.basin_id`` to the wrong station. ``_basin_for_decision`` only
    verifies the ``(network, basin_code)`` KEY exists in the loaded package
    — it does not verify the decision's station identity still matches that
    key. Two independent checks, either of which rejects the WHOLE package
    (never a partial/silent write):

    1. ``decision.station_code`` must equal the loaded basin record's
       ``station_code`` — catches a stale acceptance report replayed
       against a package whose basin at that same key now names a
       different station.
    2. The resolved station's OWN ``code``/``network`` must equal the
       basin's — catches ``decision.station_id`` resolving (e.g. via a
       station whose code/network changed since the decision was made) to a
       station whose identity no longer matches what the decision recorded.
    """
    if decision.station_code != basin.station_code:
        raise BasinPackageRejectedError(
            f"acceptance decision for basin (network={decision.network!r}, "
            f"basin_code={decision.basin_code!r}) carries station_code "
            f"{decision.station_code!r}, but the loaded package's basin "
            f"record for that key has station_code {basin.station_code!r} "
            "— the acceptance report does not match the supplied loaded "
            "package (a stale report replayed against a changed package?); "
            "refusing to bind the wrong station"
        )
    station_id = _require_station_id(decision)
    station = station_store.fetch_station(station_id)
    if station is None:
        raise ValueError(
            f"station {station_id} not found while validating it against "
            f"basin (network={basin.network!r}, basin_code={basin.basin_code!r}) "
            "— Task 1B invariant violated (an ACCEPTED decision must carry "
            "a real, resolved station_id)"
        )
    if station.code != basin.station_code or station.network != basin.network:
        raise BasinPackageRejectedError(
            f"resolved station {station_id} has (code={station.code!r}, "
            f"network={station.network!r}), but basin (network="
            f"{basin.network!r}, basin_code={basin.basin_code!r}) expects "
            f"station_code={basin.station_code!r} in network "
            f"{basin.network!r} — refusing to bind a station whose identity "
            "no longer matches the basin"
        )


def _require_static_attributes(
    static_attributes: dict[str, dict[str, float | None]], basin: BasinRecord
) -> dict[str, float | None]:
    """Major finding fix: a missing ``static_attributes`` row for an
    ACCEPTED basin must never silently become ``{}`` — that would let a
    mismatched/stale acceptance report (or a malformed in-memory package)
    produce a successful import with empty attributes, which the contract
    explicitly prohibits (04:670-672, never synthesize missing attributes)."""
    if basin.gauge_id not in static_attributes:
        raise BasinPackageRejectedError(
            f"accepted basin (network={basin.network!r}, "
            f"basin_code={basin.basin_code!r}, gauge_id={basin.gauge_id!r}) "
            "has no static_attributes row — refusing to synthesize empty "
            "attributes (contract 04:670-672); the acceptance report and "
            "the loaded package have diverged"
        )
    return static_attributes[basin.gauge_id]


def _import_one_basin(
    conn: sa.Connection,
    *,
    basin_store: PgBasinStore,
    gateway_store: RecapGatewayPolygonStore,
    station_store: PgStationStore,
    basin: BasinRecord,
    station_id: StationId,
    static_attributes: dict[str, dict[str, float | None]],
    bands: tuple[BandRecord, ...] | None,
    package_id: PackageId,
    imported_at: UtcDatetime,
    clock: Callable[[], UtcDatetime],
) -> ImportedBasin:
    attributes = dict(_require_static_attributes(static_attributes, basin))
    band_geometries = _band_geometries_for_basin(bands, basin)
    geometry = _ensure_multipolygon(_require_geometry(basin))

    existing = basin_store.fetch_basin_by_code(basin.basin_code, basin.network)
    if existing is None:
        result = _insert_new_basin(
            basin_store,
            gateway_store,
            basin=basin,
            station_id=station_id,
            attributes=attributes,
            band_geometries=band_geometries,
            geometry=geometry,
            package_id=package_id,
            imported_at=imported_at,
            clock=clock,
        )
    else:
        result = _correct_existing_basin(
            conn,
            basin_store,
            gateway_store,
            basin=basin,
            existing_basin_id=existing.id,
            station_id=station_id,
            attributes=attributes,
            band_geometries=band_geometries,
            geometry=geometry,
            package_id=package_id,
            imported_at=imported_at,
            clock=clock,
        )
    _assign_station_basin(
        station_store, station_id=station_id, basin_id=result.basin_id
    )
    return result


def _assign_station_basin(
    station_store: PgStationStore, *, station_id: StationId, basin_id: BasinId
) -> None:
    """Major finding fix: bind the accepted station's operational identity
    (``stations.basin_id``) to the basin this package just wrote — without
    this, ``assemble_station_training_data`` (which follows
    ``stations.basin_id``, never the package) can never load the imported
    static attributes, and ``record_artifact_basin_lineage`` skips lineage
    because the station still has a NULL basin_id. A station already bound
    to a DIFFERENT basin is a conflict — reject rather than silently remap
    (contract 04:670-672, "never fall back... without a recorded operator
    decision")."""
    station = station_store.fetch_station(station_id)
    if station is None:
        raise ValueError(
            f"station {station_id} not found while binding it to basin "
            f"{basin_id} — Task 1B invariant violated (an ACCEPTED decision "
            "must carry a real, resolved station_id)"
        )
    if station.basin_id is not None and station.basin_id != basin_id:
        raise BasinPackageRejectedError(
            f"station {station_id} is already bound to basin "
            f"{station.basin_id!r}; refusing to silently reassign it to "
            f"{basin_id!r} — a conflicting basin binding requires an "
            "explicit operator decision (contract 04:670-672)"
        )
    if station.basin_id is None:
        station_store.assign_basin(station_id, basin_id)


def _insert_new_basin(
    basin_store: PgBasinStore,
    gateway_store: RecapGatewayPolygonStore,
    *,
    basin: BasinRecord,
    station_id: StationId,
    attributes: dict[str, Any],
    band_geometries: list[dict[str, Any]] | None,
    geometry: BaseGeometry,
    package_id: PackageId,
    imported_at: UtcDatetime,
    clock: Callable[[], UtcDatetime],
) -> ImportedBasin:
    """Task 2A: a NEW ``(network, basin_code)`` — insert via `store_basin`
    (never separate basins/basin_versions SQL), then the §5a row (Task 2B)."""
    basin_id = BasinId(uuid.uuid4())
    binding = _basin_average_binding(
        basin,
        basin_id=basin_id,
        station_id=station_id,
        package_id=package_id,
        imported_at=imported_at,
    )
    domain_basin = Basin(
        id=basin_id,
        code=basin.basin_code,
        name=basin.display_name,
        geometry=geometry,
        area_km2=basin.area_km2,
        attributes=attributes,
        regional_basin=basin.regional_basin,
        band_geometries=band_geometries,
        created_at=clock(),
        network=basin.network,
        package_id=package_id,
    )
    basin_store.store_basin(
        domain_basin,
        package_id=package_id,
        gateway_mapping=[_serialize_binding(binding)],
    )
    # Canonical step 4: the §5a replace writer runs LAST.
    gateway_store.store_binding(binding)
    return ImportedBasin(
        basin_id=basin_id,
        network=basin.network,
        basin_code=basin.basin_code,
        outcome="inserted",
        material_change=False,
    )


def _correct_existing_basin(
    conn: sa.Connection,
    basin_store: PgBasinStore,
    gateway_store: RecapGatewayPolygonStore,
    *,
    basin: BasinRecord,
    existing_basin_id: BasinId,
    station_id: StationId,
    attributes: dict[str, Any],
    band_geometries: list[dict[str, Any]] | None,
    geometry: BaseGeometry,
    package_id: PackageId,
    imported_at: UtcDatetime,
    clock: Callable[[], UtcDatetime],
) -> ImportedBasin:
    """Task 2C, Decision B: a NEW package_id over an EXISTING
    ``(network, basin_code)`` — a correction. Always material_change=True;
    always emits the affected-artifact set for the version this correction
    just superseded. Passes ``name=basin.display_name`` (fixer round, major
    finding, 2026-07-23) so a corrected package's display name refreshes
    ``basins.name`` — the operational projection — exactly like the
    new-basin insert path does; without it a correction could update
    geometry/attributes/area/regional grouping/bands/provenance/Gateway
    mapping while silently keeping the old name."""
    binding = _basin_average_binding(
        basin,
        basin_id=existing_basin_id,
        station_id=station_id,
        package_id=package_id,
        imported_at=imported_at,
    )
    correction = basin_store.update_basin_from_package(
        basin_id=existing_basin_id,
        package_id=package_id,
        name=basin.display_name,
        geometry=geometry,
        attributes=attributes,
        area_km2=basin.area_km2,
        regional_basin=basin.regional_basin,
        band_geometries=band_geometries,
        gateway_mapping=[_serialize_binding(binding)],
        superseded_at=clock(),
    )
    # Canonical step 4: the §5a replace writer runs LAST.
    gateway_store.store_binding(binding)
    affected_artifact_ids = _affected_artifact_ids(
        conn, correction.superseded_version_id
    )
    return ImportedBasin(
        basin_id=existing_basin_id,
        network=basin.network,
        basin_code=basin.basin_code,
        outcome="corrected",
        material_change=True,
        affected_artifact_ids=affected_artifact_ids,
    )


def _require_geometry(basin: BasinRecord) -> BaseGeometry:
    """Task 1B's per-basin acceptance already holds/rejects a basin with
    missing/empty/invalid geometry — an ACCEPTED decision is guaranteed to
    carry one. Fail loud (never silently skip) if that invariant is somehow
    violated, rather than let a `None` reach `from_shape`."""
    if basin.geometry is None:
        raise ValueError(
            f"accepted basin (network={basin.network!r}, "
            f"basin_code={basin.basin_code!r}) has no geometry — Task 1B "
            "invariant violated (an ACCEPTED decision must carry a valid "
            "geometry; missing geometry is an onboarding hold)"
        )
    return basin.geometry


def _ensure_multipolygon(geometry: BaseGeometry) -> BaseGeometry:
    if isinstance(geometry, Polygon):
        return MultiPolygon([geometry])
    return geometry


def _band_geometries_for_basin(
    bands: tuple[BandRecord, ...] | None, basin: BasinRecord
) -> list[dict[str, Any]] | None:
    """`basins.band_geometries` JSONB for ONE basin (Task 2B — geometries
    only; no §5a `elevation_band` rows in v1, D-BAND). `None` when the
    package has no `bands.gpkg`, or none of its bands belong to this basin."""
    if not bands:
        return None
    matching = [
        b
        for b in bands
        if b.network == basin.network and b.basin_code == basin.basin_code
    ]
    if not matching:
        return None
    return [
        {
            "band_id": band.band_id,
            "name": band.name,
            "display_name": band.display_name,
            "min_elevation_m": band.min_elevation_m,
            "max_elevation_m": band.max_elevation_m,
            "area_km2": band.area_km2,
            "geometry": mapping(band.geometry),
        }
        for band in matching
    ]


def _basin_average_binding(
    basin: BasinRecord,
    *,
    basin_id: BasinId,
    station_id: StationId,
    package_id: PackageId,
    imported_at: UtcDatetime,
) -> GatewayPolygonBindingRow:
    """The ONE shared row-shaping function Task 2A's `gateway_mapping`
    snapshot and Task 2B's actual §5a row both derive from, so they cannot
    drift (plan "gateway_mapping source of truth" duplication-risk note). v1
    writes only `basin_average` rows — band §5a rows are deferred (D-BAND)."""
    return GatewayPolygonBindingRow(
        station_id=station_id,
        basin_id=basin_id,
        gateway_hru_name=basin.gateway_hru_name,
        name=basin.name,
        spatial_type=SpatialRepresentation.BASIN_AVERAGE,
        band_id=None,
        package_id=package_id,
        imported_at=imported_at,
    )


def _serialize_binding(binding: GatewayPolygonBindingRow) -> dict[str, Any]:
    """The `gateway_mapping` JSONB row shape — sourced from the in-memory
    Task 1B structure (never a DB read-back of the §5a table; see plan
    "gateway_mapping source of truth")."""
    return {
        "station_id": str(binding.station_id),
        "basin_id": str(binding.basin_id),
        "gateway_hru_name": binding.gateway_hru_name,
        "name": binding.name,
        "spatial_type": binding.spatial_type.value,
        "band_id": binding.band_id,
    }


def _serialize_source_datasets(
    datasets: tuple[SourceDataset, ...],
) -> list[dict[str, str]]:
    return [
        {"name": d.name, "version": d.version, "purpose": d.purpose} for d in datasets
    ]


def _serialize_climatology_window(
    window: ClimatologyWindow | None,
) -> dict[str, str] | None:
    if window is None:
        return None
    return {"start": window.start.isoformat(), "end": window.end.isoformat()}


def _affected_artifact_ids(
    conn: sa.Connection, basin_version_id: BasinVersionId
) -> tuple[ArtifactId, ...]:
    """Task 2C's "correction → affected-artifact set" — scoped to EXACTLY
    the version this correction just superseded (never every historically-
    superseded version for the basin)."""
    rows = (
        conn.execute(
            sa.select(model_artifact_basin_versions.c.model_artifact_id)
            .where(model_artifact_basin_versions.c.basin_version_id == basin_version_id)
            .distinct()
        )
        .scalars()
        .all()
    )
    return tuple(ArtifactId(r) for r in rows)
