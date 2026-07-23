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
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

import sqlalchemy as sa
import structlog
from shapely.geometry import MultiPolygon, Polygon, mapping

from sapphire_flow.db.metadata import (
    basin_static_packages,
    model_artifact_basin_versions,
)
from sapphire_flow.exceptions import BasinPackageRejectedError
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.recap_gateway_polygon_store import RecapGatewayPolygonStore
from sapphire_flow.types.basin import Basin
from sapphire_flow.types.basin_package import BasinPackageImportResult, ImportedBasin
from sapphire_flow.types.enums import SpatialRepresentation
from sapphire_flow.types.ids import ArtifactId, BasinId, PackageId
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
    from sapphire_flow.types.ids import BasinVersionId, StationId

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
    this function does not open or commit a transaction itself.

    Idempotent at the PACKAGE level: re-importing the identical package
    (same ``package_id``, same computed checksums) is a no-op — returns
    ``already_imported=True`` and touches no row. A ``package_id`` reused
    with DIFFERENT computed checksums (content mutated without a new id)
    raises :class:`BasinPackageRejectedError` — packages are immutable once
    accepted (contract §10, ``04:676``); do not overwrite.
    """
    package_id = PackageId(loaded.manifest.package_id)
    decision = _package_import_decision(conn, package_id, loaded)
    if decision == "no_op":
        log.info("basin_importer.package_already_imported", package_id=package_id)
        return BasinPackageImportResult(package_id=package_id, already_imported=True)

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
        )
    )

    basin_store = PgBasinStore(conn)
    gateway_store = RecapGatewayPolygonStore(conn)
    basin_by_key = {(b.network, b.basin_code): b for b in loaded.basins}
    imported_at = clock()

    imported_basins = tuple(
        _import_one_basin(
            conn,
            basin_store=basin_store,
            gateway_store=gateway_store,
            basin=basin_by_key[(basin_decision.network, basin_decision.basin_code)],
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
    conn: sa.Connection, package_id: PackageId, loaded: LoadedBasinPackage
) -> Literal["no_op", "proceed"]:
    """Task 2C's idempotency/correction branch, package-level half: same
    ``package_id`` + identical computed checksums is a no-op; same
    ``package_id`` + DIFFERENT checksums is an immutability violation
    (raises). An unseen ``package_id`` proceeds (per-basin new-vs-correction
    is then decided per basin in :func:`_import_one_basin`)."""
    existing_checksums = conn.execute(
        sa.select(basin_static_packages.c.checksums).where(
            basin_static_packages.c.package_id == package_id
        )
    ).scalar_one_or_none()
    if existing_checksums is None:
        return "proceed"
    if dict(existing_checksums) == dict(loaded.computed_checksums):
        return "no_op"
    raise BasinPackageRejectedError(
        f"package {package_id!r} was already imported with different "
        "computed checksums — packages are immutable once accepted; a "
        "content change requires a NEW package_id (contract §10, 04:676)"
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


def _import_one_basin(
    conn: sa.Connection,
    *,
    basin_store: PgBasinStore,
    gateway_store: RecapGatewayPolygonStore,
    basin: BasinRecord,
    station_id: StationId,
    static_attributes: dict[str, dict[str, float | None]],
    bands: tuple[BandRecord, ...] | None,
    package_id: PackageId,
    imported_at: UtcDatetime,
    clock: Callable[[], UtcDatetime],
) -> ImportedBasin:
    attributes = dict(static_attributes.get(basin.gauge_id, {}))
    band_geometries = _band_geometries_for_basin(bands, basin)
    geometry = _ensure_multipolygon(_require_geometry(basin))

    existing = basin_store.fetch_basin_by_code(basin.basin_code, basin.network)
    if existing is None:
        return _insert_new_basin(
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
    return _correct_existing_basin(
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
    just superseded."""
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
