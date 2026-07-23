"""Domain types for an accepted basin/static package (Plan 120 Task 1A/1B).

``docs/requirements/04-basin-static-artifact-contract.md`` is authoritative for
field meanings. These are frozen, parsed-at-the-boundary domain types — the
Pydantic boundary models that produce them live in
``services/basin_package_loader.py`` (CLAUDE.md "Parse, don't validate").
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from datetime import date

    from shapely.geometry.base import BaseGeometry

    from sapphire_flow.types.ids import ArtifactId, BasinId, PackageId, StationId

CoverageStatus = Literal["inside", "partial", "outside", "unknown"]
ValidationStatus = Literal["passed", "warning", "failed"]
BasinAcceptanceOutcome = Literal["accepted", "onboarding_hold"]
BasinImportOutcome = Literal["inserted", "corrected"]


@dataclass(frozen=True, kw_only=True, slots=True)
class ClimatologyWindow:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(
                f"climatology window start {self.start} must precede end {self.end}"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceDataset:
    name: str
    version: str
    purpose: str


@dataclass(frozen=True, kw_only=True, slots=True)
class PackageManifest:
    contract_version: str
    package_id: str
    created_at: str
    network: str
    crs: str
    extractor_name: str
    extractor_version: str
    source_datasets: tuple[SourceDataset, ...]
    gateway_hru_names: frozenset[str]
    climatology_window: ClimatologyWindow | None
    files: dict[str, str]
    checksums: dict[str, str]


@dataclass(frozen=True, kw_only=True, slots=True)
class FeatureCatalogEntry:
    name: str
    type: Literal["float", "integer"]
    unit: str | None
    source_dataset: str
    aggregation: str
    description: str
    climatology_window: ClimatologyWindow | None
    required_by_models: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class BasinRecord:
    """One accepted (schema-conformant) row of ``basins.gpkg`` (contract §4).

    Note: coverage is NOT sourced here. Per-basin coverage is read from the
    REQUIRED ``validation_report.json`` ``checks.coverage_status`` (contract §8),
    joined to the basin in Task 1B — never from an optional GeoPackage column.
    """

    network: str
    station_code: str
    basin_code: str
    gateway_hru_name: str
    name: str
    display_name: str
    area_km2: float
    outlet_lon: float
    outlet_lat: float
    delineation_method: str
    # A GeoPackage row's geometry cell CAN be legitimately null (a producer
    # emitted a basin with no delineation) -- Task 1B's per-basin acceptance
    # explicitly checks for this ("geometry missing", contract §9), so the
    # type stays honest rather than promising a geometry that may not exist.
    geometry: BaseGeometry | None
    gauge_id: str
    latitude: float
    longitude: float
    regional_basin: str | None = None
    outlet_snap_distance_m: float | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class BandRecord:
    """One accepted (schema-conformant) row of ``bands.gpkg`` (contract §5).

    Persisted only as ``basins.band_geometries`` JSONB geometry (Task 2B) — no
    §5a band rows are written in v1 (D-BAND). Task 1A still validates every
    required column/type/parent-reference when the file is present.
    """

    network: str
    basin_code: str
    station_code: str
    band_id: int
    gateway_hru_name: str
    name: str
    display_name: str
    min_elevation_m: float
    max_elevation_m: float
    area_km2: float
    geometry: BaseGeometry


@dataclass(frozen=True, kw_only=True, slots=True)
class ValidationReportBasinEntry:
    network: str
    basin_code: str
    station_code: str
    gateway_hru_name: str
    name: str
    status: ValidationStatus
    checks: dict[str, Any]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class ValidationReport:
    passed: int
    failed: int
    warnings: int
    basins: tuple[ValidationReportBasinEntry, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class LoadedBasinPackage:
    """Task 1A output: a whole-package-accepted, schema-validated basin/static
    package. No cross-file join or per-basin business decision has happened
    yet — that is Task 1B (``evaluate_basin_acceptance``).
    """

    manifest: PackageManifest
    basins: tuple[BasinRecord, ...]
    bands: tuple[BandRecord, ...] | None
    feature_catalog: tuple[FeatureCatalogEntry, ...]
    # gauge_id -> {attribute_name: value}. `None` is a legitimate stored value
    # (contract §6.1 "Nulls are legitimate"), never a sentinel.
    static_attributes: dict[str, dict[str, float | None]]
    validation_report: ValidationReport
    # Payload filename -> "sha256:<hex>" — the payload-set hashes SAP3
    # computed and (where declared) verified; land in
    # `basin_static_packages.checksums` (Task 2A, a later slice).
    computed_checksums: dict[str, str]


def compute_package_fingerprint(loaded: LoadedBasinPackage) -> str:
    """A deterministic canonical fingerprint of a loaded basin/static package
    (Plan 120 Phase 2 fixer round, 2026-07-23).

    Covers the *validated manifest metadata* that identifies the package's
    content-defining provenance — ``network``, ``contract_version``, the
    extractor name/version, ``source_datasets``, ``climatology_window``, and the
    declared manifest file set — PLUS the computed payload checksums. Two
    packages with the same ``package_id`` but ANY difference across these fields
    produce DIFFERENT fingerprints, so:

    - the importer can BIND an (immutable) acceptance report to the exact
      package it was produced from (the report carries this fingerprint; the
      importer recomputes it from the loaded package and rejects a mismatch —
      finding 1), and
    - idempotency/immutability compares the STORED fingerprint, so a manifest-
      only mutation under the same ``package_id`` (e.g. a changed
      ``climatology_window`` or ``source_datasets``) with identical payload
      checksums is caught as an immutability violation rather than silently
      reported ``already_imported`` (finding 3; contract §11 ``04:676``).

    Deterministic: every collection is sorted and the payload is JSON-encoded
    with sorted keys, so the digest depends only on content, never on ordering.
    """
    manifest = loaded.manifest
    window = manifest.climatology_window
    payload = {
        "network": manifest.network,
        "contract_version": manifest.contract_version,
        "extractor_name": manifest.extractor_name,
        "extractor_version": manifest.extractor_version,
        "source_datasets": sorted(
            [d.name, d.version, d.purpose] for d in manifest.source_datasets
        ),
        "climatology_window": (
            None
            if window is None
            else [window.start.isoformat(), window.end.isoformat()]
        ),
        "manifest_files": sorted(manifest.files.items()),
        "checksums": sorted(loaded.computed_checksums.items()),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, kw_only=True, slots=True)
class BasinAcceptanceDecision:
    """Task 1B per-basin outcome. Never "rejected" — a per-basin problem holds
    that basin in `onboarding` (contract §10 language: "keep in onboarding",
    "do not train", "block training", "require manual review" — never
    "reject"); only a whole-package problem raises `BasinPackageRejectedError`."""

    network: str
    station_code: str
    basin_code: str
    outcome: BasinAcceptanceOutcome
    station_id: StationId | None = None
    warnings: tuple[str, ...] = field(default=())
    hold_reasons: tuple[str, ...] = field(default=())


@dataclass(frozen=True, kw_only=True, slots=True)
class BasinPackageAcceptanceReport:
    decisions: tuple[BasinAcceptanceDecision, ...]
    # Canonical fingerprint of the loaded package these decisions were produced
    # against (``compute_package_fingerprint``). The importer recomputes the
    # fingerprint from the package it is handed and rejects a mismatch, so an
    # acceptance report can never be silently applied against a DIFFERENT
    # package than the one it was evaluated on (Plan 120 finding 1).
    fingerprint: str

    @property
    def accepted(self) -> tuple[BasinAcceptanceDecision, ...]:
        return tuple(d for d in self.decisions if d.outcome == "accepted")

    @property
    def onboarding_held(self) -> tuple[BasinAcceptanceDecision, ...]:
        return tuple(d for d in self.decisions if d.outcome == "onboarding_hold")


@dataclass(frozen=True, kw_only=True, slots=True)
class ImportedBasin:
    """Task 2A/2C persistence outcome for ONE accepted basin. ``"inserted"``
    is a brand-new ``(network, basin_code)``; ``"corrected"`` is a new
    ``package_id`` over an already-imported ``(network, basin_code)``
    (Decision B) — its ``material_change`` is always ``True`` and
    ``affected_artifact_ids`` names the artifacts trained on the version this
    correction just superseded (never all historically-superseded versions).
    """

    basin_id: BasinId
    network: str
    basin_code: str
    outcome: BasinImportOutcome
    material_change: bool
    affected_artifact_ids: tuple[ArtifactId, ...] = ()


@dataclass(frozen=True, kw_only=True, slots=True)
class BasinPackageImportResult:
    """Task 2A/2C persistence outcome for ONE package import attempt.
    ``already_imported=True`` means the identical package (same
    ``package_id``, same computed checksums) was already imported — a no-op,
    ``imported_basins`` is empty. A ``package_id`` reused with DIFFERENT
    computed checksums never reaches this type — it raises
    :class:`~sapphire_flow.exceptions.BasinPackageRejectedError` instead
    (packages are immutable once accepted, contract §10)."""

    package_id: PackageId
    already_imported: bool
    imported_basins: tuple[ImportedBasin, ...] = ()
