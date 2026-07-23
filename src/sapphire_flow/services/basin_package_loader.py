"""Basin/static package loader + acceptance (Plan 120 Phase 1 — Task 1A/1B).

``docs/requirements/04-basin-static-artifact-contract.md`` is authoritative.

- **Task 1A** (``load_basin_package``): whole-package read + validation. Any
  contract §9 whole-package reject rule, or a schema-nonconformance the
  contract requires everywhere (column presence/dtype, name format,
  cross-file consistency), raises :class:`BasinPackageRejectedError` — the entire
  package is rejected before any write.
- **Task 1B** (``evaluate_basin_acceptance``): the ``gauge_id`` join (fails
  loudly, no partial import — also via :class:`BasinPackageRejectedError`), then
  per-basin business decisions (§9 second list) that never reject a single
  basin outright — a per-basin problem holds that basin in ``onboarding``
  (contract §10 language), it never drops it or aborts the package.

**Parse, don't validate (CLAUDE.md HARD rule).** EVERY external row — a
``basins.gpkg`` feature, a ``static_attributes.parquet`` row, a ``bands.gpkg``
feature, a ``validation_report.json`` per-basin entry — is parsed through a
strict Pydantic boundary model (``_BasinRowModel`` / ``_StaticRowModel`` /
``_BandRowModel`` / ``_ValidationBasinEntryModel``) BEFORE any frozen domain
type is constructed. A malformed cell type rejects the package
(:class:`BasinPackageRejectedError`) rather than raising a raw ``ValueError`` /
``TypeError`` deeper in the code.

No DB writes happen here (Task 2A/2C, a later slice).
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false
from __future__ import annotations

import hashlib
import math
import numbers
import re
from datetime import date, datetime, timedelta  # noqa: TC003 -- pydantic runtime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import geopandas as gpd
import polars as pl
import pyogrio.errors
import structlog
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from sapphire_flow.exceptions import BasinPackageRejectedError
from sapphire_flow.types.basin_package import (
    BandRecord,
    BasinAcceptanceDecision,
    BasinPackageAcceptanceReport,
    BasinRecord,
    ClimatologyWindow,
    FeatureCatalogEntry,
    LoadedBasinPackage,
    PackageManifest,
    SourceDataset,
    ValidationReport,
    ValidationReportBasinEntry,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shapely.geometry.base import BaseGeometry

    from sapphire_flow.types.ids import StationId

log = structlog.get_logger(__name__)

_SUPPORTED_CONTRACT_VERSION = "basin-static-artifact/v1"
_REQUIRED_CRS = "EPSG:4326"
_MANDATORY_FILES: tuple[str, ...] = (
    "manifest.json",
    "basins.gpkg",
    "static_attributes.parquet",
    "feature_catalog.json",
    "validation_report.json",
)
_OPTIONAL_PAYLOAD_FILES: tuple[str, ...] = ("bands.gpkg", "README.md")
# Files that carry hashes / describe the package — never hashed as payload, and
# never legal as a declared payload path (a payload path pointing at them is
# self-referential; §9 / security boundary).
_SELF_REFERENTIAL_FILES: frozenset[str] = frozenset(
    {"manifest.json", "checksums.sha256"}
)
_SHA256_VALUE = re.compile(r"^sha256:[0-9a-f]{64}$")
# §3a: internal layer/table name AND per-feature `name` must start with a
# letter or underscore. §4a additionally requires the per-feature `name` to be
# lowercase; the internal layer/table name has only the leading-char rule.
_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
_LAYER_NAME_PATTERN = re.compile(r"^[A-Za-z_]")
# §8 minimum per-basin validation checks are enforced structurally by
# `_ValidationChecksModel` (presence + type at the Pydantic boundary).

_BASIN_REQUIRED_COLUMNS: tuple[str, ...] = (
    "network",
    "station_code",
    "basin_code",
    "gateway_hru_name",
    "name",
    "display_name",
    "area_km2",
    "outlet_lon",
    "outlet_lat",
    "delineation_method",
    "gauge_id",
    "latitude",
    "longitude",
)
_BAND_REQUIRED_COLUMNS: tuple[str, ...] = (
    "network",
    "basin_code",
    "station_code",
    "band_id",
    "gateway_hru_name",
    "name",
    "display_name",
    "min_elevation_m",
    "max_elevation_m",
    "area_km2",
)


# ──────────────────────────────────────────────
# Small validation helpers reused across boundary models
# ──────────────────────────────────────────────


def _nan_to_none(value: Any) -> Any:
    """A GeoPackage null cell surfaces as NaN through pyogrio/pandas — map it to
    ``None`` for OPTIONAL fields (required-field NaN is rejected explicitly)."""
    if isinstance(value, numbers.Real) and math.isnan(float(value)):
        return None
    return value


def _reject_nan(value: float) -> float:
    if math.isnan(value):
        raise ValueError("value must not be NaN")
    return value


def _normalize_station_code(code: str) -> str:
    """§4a normalization: lowercase, runs of non-alphanumerics → one underscore."""
    return re.sub(r"[^a-z0-9]+", "_", code.lower())


# ──────────────────────────────────────────────
# Pydantic boundary models (private — module-internal parsing only)
# ──────────────────────────────────────────────


class _ClimatologyWindowModel(BaseModel):
    start: date
    end: date


class _SourceDatasetModel(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


class _ExtractorModel(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class _ManifestModel(BaseModel):
    contract_version: str
    package_id: str = Field(min_length=1)
    created_at: str
    network: str = Field(min_length=1)
    crs: str
    extractor: _ExtractorModel
    source_datasets: list[_SourceDatasetModel] = Field(min_length=1)
    gateway_hru_names: list[str] = Field(min_length=1)
    climatology_window: _ClimatologyWindowModel | None = None
    files: dict[str, str]
    checksums: dict[str, str] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _created_at_utc_iso(cls, value: str) -> str:
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ValueError(
                f"created_at {value!r} is not an ISO-8601 timestamp"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError(f"created_at {value!r} must be UTC (offset +00:00 or Z)")
        return value

    @field_validator("crs")
    @classmethod
    def _crs_is_4326(cls, value: str) -> str:
        if value != _REQUIRED_CRS:
            raise ValueError(f"crs must be {_REQUIRED_CRS!r}, got {value!r}")
        return value

    @field_validator("checksums")
    @classmethod
    def _checksum_value_syntax(cls, value: dict[str, str]) -> dict[str, str]:
        for filename, digest in value.items():
            if not _SHA256_VALUE.match(digest):
                raise ValueError(
                    f"checksum for {filename!r} is not a 'sha256:<64-hex>' value: "
                    f"{digest!r}"
                )
        return value


class _FeatureCatalogEntryModel(BaseModel):
    name: str = Field(min_length=1)
    type: Literal["float", "integer"]
    unit: str | None
    source_dataset: str = Field(min_length=1)
    aggregation: str = Field(min_length=1)
    description: str = Field(min_length=1)
    climatology_window: _ClimatologyWindowModel | None
    required_by_models: list[str] = Field(default_factory=list)


class _FeatureCatalogModel(BaseModel):
    features: list[_FeatureCatalogEntryModel] = Field(min_length=1)


class _BasinRowModel(BaseModel):
    """Strict boundary model for one ``basins.gpkg`` feature row (§4). Scalar
    columns are parsed with strict types (a wrong-typed cell rejects the
    package); ``geometry`` is carried opaquely and validated in domain code."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    network: StrictStr
    station_code: StrictStr
    basin_code: StrictStr
    gateway_hru_name: StrictStr
    name: StrictStr
    display_name: StrictStr
    area_km2: StrictFloat
    outlet_lon: StrictFloat
    outlet_lat: StrictFloat
    delineation_method: StrictStr
    gauge_id: StrictStr
    latitude: StrictFloat
    longitude: StrictFloat
    geometry: Any = None
    regional_basin: StrictStr | None = None
    outlet_snap_distance_m: StrictFloat | None = None

    @field_validator("regional_basin", "outlet_snap_distance_m", mode="before")
    @classmethod
    def _optional_nan_to_none(cls, value: Any) -> Any:
        return _nan_to_none(value)


class _BandRowModel(BaseModel):
    """Strict boundary model for one ``bands.gpkg`` feature row (§5)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    network: StrictStr
    basin_code: StrictStr
    station_code: StrictStr
    band_id: StrictInt
    gateway_hru_name: StrictStr
    name: StrictStr
    display_name: StrictStr
    min_elevation_m: StrictFloat
    max_elevation_m: StrictFloat
    area_km2: StrictFloat
    geometry: Any = None

    @field_validator("band_id", mode="before")
    @classmethod
    def _band_id_integral(cls, value: Any) -> int:
        # §5: band_id is an integer. Consistent with StrictInt, a non-integer
        # RUNTIME type is rejected outright — a float `1.0` does NOT coerce to
        # `1` (that would silently accept a float-typed GeoPackage column). Only
        # genuine integral types (Python int, numpy integer) are accepted; bool
        # is a numbers.Integral subtype and must be rejected explicitly.
        if isinstance(value, bool):
            raise ValueError("band_id must be an integer, not a bool")
        if isinstance(value, numbers.Integral):
            return int(value)
        raise ValueError(
            f"band_id must be an integer-typed value, got {type(value).__name__} "
            f"({value!r})"
        )

    @field_validator("min_elevation_m", "max_elevation_m", "area_km2")
    @classmethod
    def _no_nan(cls, value: float) -> float:
        return _reject_nan(value)


class _StaticRowModel(BaseModel):
    """Strict boundary model for one ``static_attributes.parquet`` row (§6):
    a string ``gauge_id`` plus ``Float64``-or-null attribute values."""

    model_config = ConfigDict(extra="forbid")

    gauge_id: StrictStr
    attributes: dict[str, StrictFloat | None]


class _ValidationSummaryModel(BaseModel):
    # StrictInt rejects strings AND bools ("1"/True are NOT valid counts);
    # ge=0 rejects a negative count. §8 summary counts are cardinalities.
    passed: StrictInt = Field(ge=0)
    failed: StrictInt = Field(ge=0)
    warnings: StrictInt = Field(ge=0)


class _ValidationChecksModel(BaseModel):
    """§8 minimum checks — presence and type enforced at the boundary."""

    model_config = ConfigDict(extra="allow")

    geometry_present: StrictBool
    geometry_valid: StrictBool
    crs_epsg_4326: StrictBool
    geometry_2d: StrictBool
    area_positive: StrictBool
    ids_unique: StrictBool
    static_row_present: StrictBool
    required_static_features_present: StrictBool
    outlet_snap_distance_m: StrictFloat | StrictInt | None
    coverage_status: Literal["inside", "partial", "outside", "unknown"]


class _ValidationBasinEntryModel(BaseModel):
    network: str
    basin_code: str
    station_code: str
    gateway_hru_name: str
    name: str
    status: Literal["passed", "warning", "failed"]
    checks: _ValidationChecksModel
    warnings: list[str]
    errors: list[str]


class _ValidationReportModel(BaseModel):
    summary: _ValidationSummaryModel
    basins: list[_ValidationBasinEntryModel]


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _parse_model(model_cls: type[_ModelT], path: Path) -> _ModelT:
    try:
        raw = path.read_text()
    except OSError as exc:
        raise BasinPackageRejectedError(
            f"{path.name} could not be read: {exc}"
        ) from exc
    try:
        return model_cls.model_validate_json(raw)
    except ValidationError as exc:
        raise BasinPackageRejectedError(
            f"{path.name} failed schema validation: {exc}"
        ) from exc


def _parse_row(
    model_cls: type[_ModelT], data: dict[str, Any], *, label: str
) -> _ModelT:
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise BasinPackageRejectedError(
            f"{label} row failed schema validation: {exc}"
        ) from exc


# ──────────────────────────────────────────────
# Task 1A — whole-package load + validation
# ──────────────────────────────────────────────


def load_basin_package(package_dir: Path) -> LoadedBasinPackage:
    """Load and whole-package-validate a basin/static package (contract §9
    first list + the "Full contract-conformance validation" bullets).

    Raises :class:`BasinPackageRejectedError` on ANY whole-package reject rule.
    Performs no DB writes and no per-basin business decisions (Task 1B).
    """
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file():
        raise BasinPackageRejectedError(
            f"mandatory file manifest.json missing from {package_dir}"
        )
    manifest_model = _parse_model(_ManifestModel, manifest_path)

    if manifest_model.contract_version != _SUPPORTED_CONTRACT_VERSION:
        raise BasinPackageRejectedError(
            f"unsupported contract_version {manifest_model.contract_version!r}; "
            f"expected {_SUPPORTED_CONTRACT_VERSION!r}"
        )

    _validate_declared_paths(package_dir, manifest_model)

    missing_files = [
        name for name in _MANDATORY_FILES if not (package_dir / name).is_file()
    ]
    if missing_files:
        raise BasinPackageRejectedError(f"mandatory file(s) missing: {missing_files}")

    computed_checksums = _compute_and_verify_checksums(package_dir, manifest_model)
    manifest = _domain_manifest(manifest_model)

    basins_gdf = _read_geometry_file(package_dir / "basins.gpkg", label="basins.gpkg")
    _validate_basin_columns(basins_gdf)
    basins = tuple(
        _domain_basin_record(
            _parse_row(_BasinRowModel, row.to_dict(), label="basins.gpkg")
        )
        for _, row in basins_gdf.iterrows()
    )
    _validate_single_gateway_hru(
        {b.gateway_hru_name for b in basins}, label="basins.gpkg"
    )
    _validate_network_consistency(basins, manifest.network, label="basins.gpkg")
    _validate_lat_lon_equality(basins)
    _validate_basin_names(basins)
    _validate_basin_gauge_id_uniqueness(basins)
    _validate_basin_code_uniqueness(basins)

    bands: tuple[BandRecord, ...] | None = None
    bands_path = package_dir / "bands.gpkg"
    if bands_path.is_file():
        bands_gdf = _read_geometry_file(bands_path, label="bands.gpkg")
        bands = _validate_and_build_bands(bands_gdf, manifest, basins)
        _validate_cross_file_name_collisions(basins, bands)

    static_df = _read_static_attributes(package_dir / "static_attributes.parquet")
    static_attributes = _static_attributes_by_gauge_id(static_df)
    static_columns = frozenset(static_df.columns) - {"gauge_id"}

    catalog_model = _parse_model(
        _FeatureCatalogModel, package_dir / "feature_catalog.json"
    )
    feature_catalog = _validate_feature_catalog(catalog_model, manifest, static_columns)

    validation_report_model = _parse_model(
        _ValidationReportModel, package_dir / "validation_report.json"
    )
    _validate_validation_report(validation_report_model, basins)
    validation_report = _domain_validation_report(validation_report_model)

    return LoadedBasinPackage(
        manifest=manifest,
        basins=basins,
        bands=bands,
        feature_catalog=feature_catalog,
        static_attributes=static_attributes,
        validation_report=validation_report,
        computed_checksums=computed_checksums,
    )


def _domain_manifest(model: _ManifestModel) -> PackageManifest:
    return PackageManifest(
        contract_version=model.contract_version,
        package_id=model.package_id,
        created_at=model.created_at,
        network=model.network,
        crs=model.crs,
        extractor_name=model.extractor.name,
        extractor_version=model.extractor.version,
        source_datasets=tuple(
            SourceDataset(name=d.name, version=d.version, purpose=d.purpose)
            for d in model.source_datasets
        ),
        gateway_hru_names=frozenset(model.gateway_hru_names),
        climatology_window=_domain_climatology_window(model.climatology_window),
        files=dict(model.files),
        checksums=dict(model.checksums),
    )


def _domain_climatology_window(
    model: _ClimatologyWindowModel | None,
) -> ClimatologyWindow | None:
    if model is None:
        return None
    return ClimatologyWindow(start=model.start, end=model.end)


def _domain_basin_record(model: _BasinRowModel) -> BasinRecord:
    geometry: BaseGeometry | None = model.geometry
    return BasinRecord(
        network=model.network,
        station_code=model.station_code,
        basin_code=model.basin_code,
        gateway_hru_name=model.gateway_hru_name,
        name=model.name,
        display_name=model.display_name,
        area_km2=model.area_km2,
        outlet_lon=model.outlet_lon,
        outlet_lat=model.outlet_lat,
        delineation_method=model.delineation_method,
        geometry=geometry,
        gauge_id=model.gauge_id,
        latitude=model.latitude,
        longitude=model.longitude,
        regional_basin=model.regional_basin,
        outlet_snap_distance_m=model.outlet_snap_distance_m,
    )


def _domain_validation_report(model: _ValidationReportModel) -> ValidationReport:
    return ValidationReport(
        passed=model.summary.passed,
        failed=model.summary.failed,
        warnings=model.summary.warnings,
        basins=tuple(
            ValidationReportBasinEntry(
                network=b.network,
                basin_code=b.basin_code,
                station_code=b.station_code,
                gateway_hru_name=b.gateway_hru_name,
                name=b.name,
                status=b.status,
                checks=b.checks.model_dump(),
                warnings=tuple(b.warnings),
                errors=tuple(b.errors),
            )
            for b in model.basins
        ),
    )


# ── Declared-path security boundary (§9) ──


def _reject_unsafe_paths(package_dir: Path, declared: set[str]) -> None:
    """Reject any declared payload path that is absolute, contains a ``..``
    traversal, is self-referential (``manifest.json``/``checksums.sha256``), or
    escapes the package directory. Runs BEFORE any file is opened/hashed by a
    declared path — the single path-safety gate for EVERY declared-path source
    (``manifest.files`` values, ``manifest.checksums`` keys, and the
    ``checksums.sha256`` sidecar key set)."""
    base = package_dir.resolve()
    for rel in declared:
        if rel in _SELF_REFERENTIAL_FILES:
            raise BasinPackageRejectedError(
                f"declared payload path {rel!r} is self-referential "
                "(manifest.json/checksums.sha256 must not be listed as payload)"
            )
        candidate = Path(rel)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise BasinPackageRejectedError(
                f"declared payload path {rel!r} is absolute or contains '..' traversal"
            )
        resolved = (base / candidate).resolve()
        if resolved != base and base not in resolved.parents:
            raise BasinPackageRejectedError(
                f"declared payload path {rel!r} escapes the package directory"
            )


def _validate_declared_paths(package_dir: Path, manifest_model: _ManifestModel) -> None:
    """Path-safety gate for the manifest-declared paths (``manifest.files``
    values and ``manifest.checksums`` keys). The ``checksums.sha256`` sidecar
    key set is gated separately in :func:`_compute_and_verify_checksums`, since
    it is only known once the sidecar is parsed."""
    _reject_unsafe_paths(
        package_dir,
        set(manifest_model.files.values()) | set(manifest_model.checksums.keys()),
    )


# ── Checksums ──


def _declared_checksums(
    package_dir: Path, manifest_model: _ManifestModel
) -> dict[str, str] | None:
    """The canonical declared checksum set: ``manifest.checksums`` and/or a
    ``checksums.sha256`` sidecar (the SAME set). Returns ``None`` when the
    producer declared no hashes anywhere. When both are present they MUST agree
    exactly — same key set AND same value per key (a missing OR extra sidecar
    entry rejects)."""
    manifest_checksums = manifest_model.checksums or None
    sidecar_path = package_dir / "checksums.sha256"
    sidecar_checksums = (
        _parse_sha256_sidecar(sidecar_path) if sidecar_path.is_file() else None
    )

    if manifest_checksums is not None and sidecar_checksums is not None:
        if set(manifest_checksums) != set(sidecar_checksums):
            raise BasinPackageRejectedError(
                "manifest.checksums and checksums.sha256 declare different file "
                f"sets: manifest={sorted(manifest_checksums)}, "
                f"sidecar={sorted(sidecar_checksums)}"
            )
        for filename, declared in manifest_checksums.items():
            if sidecar_checksums[filename] != declared:
                raise BasinPackageRejectedError(
                    f"manifest.checksums and checksums.sha256 disagree for "
                    f"{filename!r}: {declared!r} vs {sidecar_checksums[filename]!r}"
                )
        return dict(manifest_checksums)

    return manifest_checksums or sidecar_checksums


def _compute_and_verify_checksums(
    package_dir: Path, manifest_model: _ManifestModel
) -> dict[str, str]:
    declared = _declared_checksums(package_dir, manifest_model)

    if declared is not None:
        payload_files = sorted(declared.keys())
    else:
        # No declarations anywhere: compute over manifest.files ∪ present
        # optional payload files, excluding the self-referential/hash files.
        payload = set(manifest_model.files.values())
        payload.update(
            name for name in _OPTIONAL_PAYLOAD_FILES if (package_dir / name).is_file()
        )
        payload -= _SELF_REFERENTIAL_FILES
        payload_files = sorted(payload)

    # Path-safety gate for EVERY payload path we are about to open/hash — this
    # closes the sidecar bypass: a `checksums.sha256` sidecar key never passed
    # through `_validate_declared_paths`, so an absolute / `..` / escaping path
    # sourced only from the sidecar must be rejected HERE before it is hashed.
    _reject_unsafe_paths(package_dir, set(payload_files))

    computed: dict[str, str] = {}
    for filename in payload_files:
        path = package_dir / filename
        if not path.is_file():
            raise BasinPackageRejectedError(
                f"declared payload file {filename!r} is absent from the package"
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        computed[filename] = f"sha256:{digest}"

    if declared is not None:
        for filename, declared_value in declared.items():
            if computed[filename] != declared_value:
                raise BasinPackageRejectedError(
                    f"checksum mismatch for {filename!r}: declared "
                    f"{declared_value!r}, computed {computed[filename]!r}"
                )
    return computed


def _parse_sha256_sidecar(path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            raise BasinPackageRejectedError(
                f"checksums.sha256 line is not '<hex>  <file>': {line!r}"
            )
        digest, filename = parts
        value = digest if digest.startswith("sha256:") else f"sha256:{digest}"
        if not _SHA256_VALUE.match(value):
            raise BasinPackageRejectedError(
                f"checksums.sha256 entry for {filename!r} is not a sha256 hex value: "
                f"{digest!r}"
            )
        hashes[filename.lstrip("*")] = value
    return hashes


# ── basins.gpkg ──


def _read_geometry_file(path: Path, *, label: str) -> gpd.GeoDataFrame:
    layer = _selected_layer_name(path, label=label)
    # §3a rule 1: the internal layer/table name MUST start with a letter or
    # underscore (`polygons` OK, `00003` not).
    if not _LAYER_NAME_PATTERN.match(layer):
        raise BasinPackageRejectedError(
            f"{label} internal layer/table name {layer!r} must start with a letter "
            "or underscore (§3a)"
        )
    try:
        gdf = gpd.read_file(path, layer=layer)
    except pyogrio.errors.DataSourceError as exc:
        raise BasinPackageRejectedError(f"{label} could not be read: {exc}") from exc
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        raise BasinPackageRejectedError(f"{label} is not EPSG:4326 (got {gdf.crs!r})")
    return gdf


def _selected_layer_name(path: Path, *, label: str) -> str:
    """The layer ``gpd.read_file`` reads (the first one). Listed via pyogrio so
    the name is available for §3a validation before the layer is read."""
    try:
        layers = pyogrio.list_layers(path)
    except pyogrio.errors.DataSourceError as exc:
        raise BasinPackageRejectedError(f"{label} could not be read: {exc}") from exc
    if len(layers) == 0:
        raise BasinPackageRejectedError(f"{label} contains no layers")
    return str(layers[0][0])


def _validate_single_gateway_hru(hru_names: set[str], *, label: str) -> None:
    """§3a rule 2: one Gateway HRU IS one GeoPackage (single-kind), so every
    feature row in a given ``.gpkg`` MUST carry the same ``gateway_hru_name``."""
    if len(hru_names) > 1:
        raise BasinPackageRejectedError(
            f"{label} carries multiple gateway_hru_name values (a Gateway HRU is a "
            "single GeoPackage — every feature in one .gpkg must share one "
            f"gateway_hru_name): {sorted(hru_names)}"
        )


def _validate_basin_columns(gdf: gpd.GeoDataFrame) -> None:
    missing = [c for c in _BASIN_REQUIRED_COLUMNS if c not in gdf.columns]
    if "geometry" not in gdf.columns:
        missing.append("geometry")
    if missing:
        raise BasinPackageRejectedError(
            f"basins.gpkg missing required column(s): {missing}"
        )


def _validate_network_consistency(
    basins: tuple[BasinRecord, ...], network: str, *, label: str
) -> None:
    bad = [b.station_code for b in basins if b.network != network]
    if bad:
        raise BasinPackageRejectedError(
            f"{label} network column disagrees with manifest.network={network!r} "
            f"for station_code(s): {bad}"
        )


def _validate_lat_lon_equality(basins: tuple[BasinRecord, ...]) -> None:
    mismatched = [
        b.station_code
        for b in basins
        if b.latitude != b.outlet_lat or b.longitude != b.outlet_lon
    ]
    if mismatched:
        raise BasinPackageRejectedError(
            "basins.gpkg latitude/longitude disagree with outlet_lat/outlet_lon "
            f"for station_code(s): {mismatched}"
        )


def _validate_basin_names(basins: tuple[BasinRecord, ...]) -> None:
    """§4a: every basin feature ``name`` MUST equal ``g_<normalized station
    code>`` (exact Gateway form), and names MUST be unique across the file.
    Normalization is not injective, so two codes can collide → §4a collision =
    a package failure naming both codes."""
    seen: dict[str, str] = {}
    for basin in basins:
        expected = f"g_{_normalize_station_code(basin.station_code)}"
        if not _NAME_PATTERN.match(basin.name):
            raise BasinPackageRejectedError(
                f"basins.gpkg feature name {basin.name!r} "
                f"(station_code={basin.station_code!r}) must be lowercase and must "
                "not start with a digit"
            )
        if basin.name != expected:
            raise BasinPackageRejectedError(
                f"basins.gpkg feature name {basin.name!r} does not match the required "
                f"Gateway form {expected!r} for station_code {basin.station_code!r}"
            )
        if basin.name in seen and seen[basin.name] != basin.station_code:
            raise BasinPackageRejectedError(
                f"basins.gpkg feature name collision {basin.name!r} among "
                f"station_code(s): {[seen[basin.name], basin.station_code]}"
            )
        seen[basin.name] = basin.station_code


def _validate_basin_gauge_id_uniqueness(basins: tuple[BasinRecord, ...]) -> None:
    seen: set[str] = set()
    dupes: list[str] = []
    for basin in basins:
        if basin.gauge_id in seen:
            dupes.append(basin.gauge_id)
        seen.add(basin.gauge_id)
    if dupes:
        raise BasinPackageRejectedError(
            f"basins.gpkg has duplicate gauge_id value(s): {sorted(set(dupes))}"
        )


def _validate_basin_code_uniqueness(basins: tuple[BasinRecord, ...]) -> None:
    seen: set[tuple[str, str]] = set()
    dupes: list[tuple[str, str]] = []
    for basin in basins:
        key = (basin.network, basin.basin_code)
        if key in seen:
            dupes.append(key)
        seen.add(key)
    if dupes:
        raise BasinPackageRejectedError(
            f"basins.gpkg has duplicate (network, basin_code) pairs: "
            f"{sorted(set(dupes))}"
        )


# ── bands.gpkg (validated fully when present; §5a rows deferred — D-BAND) ──


def _validate_and_build_bands(
    gdf: gpd.GeoDataFrame,
    manifest: PackageManifest,
    basins: tuple[BasinRecord, ...],
) -> tuple[BandRecord, ...]:
    missing = [c for c in _BAND_REQUIRED_COLUMNS if c not in gdf.columns]
    if "geometry" not in gdf.columns:
        missing.append("geometry")
    if missing:
        raise BasinPackageRejectedError(
            f"bands.gpkg missing required column(s): {missing}"
        )

    band_models = [
        _parse_row(_BandRowModel, row.to_dict(), label="bands.gpkg")
        for _, row in gdf.iterrows()
    ]

    _validate_single_gateway_hru(
        {b.gateway_hru_name for b in band_models}, label="bands.gpkg"
    )

    bad_network = [b.station_code for b in band_models if b.network != manifest.network]
    if bad_network:
        raise BasinPackageRejectedError(
            f"bands.gpkg network disagrees with manifest.network="
            f"{manifest.network!r} for station_code(s): {bad_network}"
        )

    basin_by_code = {b.basin_code: b for b in basins}
    for band in band_models:
        parent = basin_by_code.get(band.basin_code)
        if parent is None:
            raise BasinPackageRejectedError(
                f"bands.gpkg basin_code {band.basin_code!r} does not reference a "
                "basins.gpkg basin"
            )
        if band.station_code != parent.station_code:
            raise BasinPackageRejectedError(
                f"bands.gpkg station_code {band.station_code!r} does not match "
                f"parent basin {band.basin_code!r} station_code "
                f"{parent.station_code!r}"
            )
        expected_name = (
            f"g_{_normalize_station_code(band.station_code)}_band_{band.band_id}"
        )
        if not _NAME_PATTERN.match(band.name):
            raise BasinPackageRejectedError(
                f"bands.gpkg feature name {band.name!r} must be lowercase and must "
                "not start with a digit"
            )
        if band.name != expected_name:
            raise BasinPackageRejectedError(
                f"bands.gpkg feature name {band.name!r} does not match the required "
                f"Gateway band form {expected_name!r}"
            )
        if band.gateway_hru_name not in manifest.gateway_hru_names:
            raise BasinPackageRejectedError(
                f"bands.gpkg gateway_hru_name {band.gateway_hru_name!r} not declared "
                "in manifest.gateway_hru_names"
            )
        if not band.display_name:
            raise BasinPackageRejectedError(
                f"bands.gpkg display_name missing for band {band.name!r}"
            )
        if band.max_elevation_m <= band.min_elevation_m:
            raise BasinPackageRejectedError(
                "bands.gpkg max_elevation_m must exceed min_elevation_m (got "
                f"min={band.min_elevation_m!r}, max={band.max_elevation_m!r}) for "
                f"band {band.name!r}"
            )
        if band.area_km2 <= 0:
            raise BasinPackageRejectedError(
                f"bands.gpkg area_km2 must be positive (got {band.area_km2!r}) for "
                f"band {band.name!r}"
            )
        _validate_geometry_2d(band.geometry, label=f"bands.gpkg band {band.name!r}")

    _validate_band_name_uniqueness(band_models)
    _validate_band_id_uniqueness(band_models)

    return tuple(
        BandRecord(
            network=band.network,
            basin_code=band.basin_code,
            station_code=band.station_code,
            band_id=band.band_id,
            gateway_hru_name=band.gateway_hru_name,
            name=band.name,
            display_name=band.display_name,
            min_elevation_m=band.min_elevation_m,
            max_elevation_m=band.max_elevation_m,
            area_km2=band.area_km2,
            geometry=band.geometry,
        )
        for band in band_models
    )


def _validate_geometry_2d(geometry: Any, *, label: str) -> None:
    if (
        geometry is None
        or geometry.is_empty
        or not geometry.is_valid
        or geometry.has_z
        or geometry.geom_type not in ("Polygon", "MultiPolygon")
    ):
        raise BasinPackageRejectedError(
            f"{label} geometry invalid — must be a 2-D valid Polygon/MultiPolygon"
        )


def _validate_band_name_uniqueness(bands: list[_BandRowModel]) -> None:
    seen: set[str] = set()
    dupes: list[str] = []
    for band in bands:
        if band.name in seen:
            dupes.append(band.name)
        seen.add(band.name)
    if dupes:
        raise BasinPackageRejectedError(
            f"bands.gpkg feature name collision {sorted(set(dupes))}"
        )


def _validate_band_id_uniqueness(bands: list[_BandRowModel]) -> None:
    seen: set[tuple[str, str, int]] = set()
    dupes: list[tuple[str, str, int]] = []
    for band in bands:
        key = (band.network, band.basin_code, band.band_id)
        if key in seen:
            dupes.append(key)
        seen.add(key)
    if dupes:
        raise BasinPackageRejectedError(
            "bands.gpkg band_id not unique within network+basin_code: "
            f"{sorted(set(dupes))}"
        )


def _validate_cross_file_name_collisions(
    basins: tuple[BasinRecord, ...], bands: tuple[BandRecord, ...]
) -> None:
    """§4a / §3a: basin and band feature ``name`` spaces MUST be disjoint across
    the whole package (a whole-package reject, not deferrable)."""
    basin_names = {b.name for b in basins}
    band_names = {b.name for b in bands}
    collisions = basin_names & band_names
    if collisions:
        raise BasinPackageRejectedError(
            "basins.gpkg and bands.gpkg share feature name(s) (must be disjoint "
            f"across the package): {sorted(collisions)}"
        )


# ── feature_catalog.json ──


def _validate_feature_catalog(
    catalog_model: _FeatureCatalogModel,
    manifest: PackageManifest,
    static_columns: frozenset[str],
) -> tuple[FeatureCatalogEntry, ...]:
    seen_names: set[str] = set()
    duplicate_names: list[str] = []
    for entry in catalog_model.features:
        if entry.name in seen_names:
            duplicate_names.append(entry.name)
        seen_names.add(entry.name)
    if duplicate_names:
        raise BasinPackageRejectedError(
            f"feature_catalog.json has duplicate feature name(s): "
            f"{sorted(set(duplicate_names))}"
        )

    catalog_names = seen_names
    missing_from_catalog = static_columns - catalog_names
    if missing_from_catalog:
        raise BasinPackageRejectedError(
            "feature_catalog.json omits static_attributes.parquet column(s): "
            f"{sorted(missing_from_catalog)}"
        )
    missing_from_parquet = catalog_names - static_columns
    if missing_from_parquet:
        raise BasinPackageRejectedError(
            "feature_catalog.json entry has no matching static_attributes.parquet "
            f"column: {sorted(missing_from_parquet)}"
        )

    # Forcing-vs-geometry derivation is decided by the dataset's PURPOSE in
    # manifest.source_datasets (§6.3/§7), not by a hard-coded dataset name.
    forcing_datasets = {
        d.name for d in manifest.source_datasets if _is_forcing_purpose(d.purpose)
    }
    source_names = {d.name for d in manifest.source_datasets}
    entries: list[FeatureCatalogEntry] = []
    for entry in catalog_model.features:
        if entry.source_dataset not in source_names:
            raise BasinPackageRejectedError(
                f"feature_catalog.json entry {entry.name!r} source_dataset "
                f"{entry.source_dataset!r} not in manifest.source_datasets"
            )
        window = _domain_climatology_window(entry.climatology_window)
        is_forcing_derived = entry.source_dataset in forcing_datasets
        if is_forcing_derived and window is None:
            raise BasinPackageRejectedError(
                f"feature_catalog.json entry {entry.name!r} is forcing-derived "
                f"(source_dataset={entry.source_dataset!r}) but has no "
                "climatology_window"
            )
        if not is_forcing_derived and window is not None:
            raise BasinPackageRejectedError(
                f"feature_catalog.json entry {entry.name!r} is geometry-derived "
                f"(source_dataset={entry.source_dataset!r}) but declares a "
                "climatology_window (must be null)"
            )
        if window is not None and manifest.climatology_window is None:
            raise BasinPackageRejectedError(
                f"feature_catalog.json entry {entry.name!r} declares a "
                "climatology_window but manifest.climatology_window is absent"
            )
        if (
            window is not None
            and manifest.climatology_window is not None
            and window != manifest.climatology_window
        ):
            raise BasinPackageRejectedError(
                f"feature_catalog.json entry {entry.name!r} climatology_window "
                f"{window} != manifest.climatology_window {manifest.climatology_window}"
            )
        entries.append(
            FeatureCatalogEntry(
                name=entry.name,
                type=entry.type,
                unit=entry.unit,
                source_dataset=entry.source_dataset,
                aggregation=entry.aggregation,
                description=entry.description,
                climatology_window=window,
                required_by_models=tuple(entry.required_by_models),
            )
        )
    return tuple(entries)


def _is_forcing_purpose(purpose: str) -> bool:
    return "forcing" in purpose.lower()


# ── static_attributes.parquet ──


def _read_static_attributes(path: Path) -> pl.DataFrame:
    try:
        df = pl.read_parquet(path)
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise BasinPackageRejectedError(
            f"static_attributes.parquet could not be read: {exc}"
        ) from exc

    if "gauge_id" not in df.columns:
        raise BasinPackageRejectedError(
            "static_attributes.parquet missing gauge_id column"
        )
    if df.schema["gauge_id"] != pl.Utf8:
        raise BasinPackageRejectedError(
            f"static_attributes.parquet gauge_id must be Utf8, got "
            f"{df.schema['gauge_id']}"
        )
    non_float_columns = [
        name
        for name in df.columns
        if name != "gauge_id" and df.schema[name] != pl.Float64
    ]
    if non_float_columns:
        raise BasinPackageRejectedError(
            f"static_attributes.parquet attribute column(s) not Float64: "
            f"{non_float_columns}"
        )
    return df


def _static_attributes_by_gauge_id(
    df: pl.DataFrame,
) -> dict[str, dict[str, float | None]]:
    attribute_columns = [c for c in df.columns if c != "gauge_id"]
    parsed: dict[str, dict[str, float | None]] = {}
    for raw in df.iter_rows(named=True):
        gauge_id = raw["gauge_id"]
        if gauge_id is None or gauge_id == "":
            raise BasinPackageRejectedError(
                "static_attributes.parquet has a missing gauge_id value"
            )
        row = _parse_row(
            _StaticRowModel,
            {
                "gauge_id": gauge_id,
                "attributes": {col: raw[col] for col in attribute_columns},
            },
            label="static_attributes.parquet",
        )
        if row.gauge_id in parsed:
            raise BasinPackageRejectedError(
                "static_attributes.parquet has duplicate gauge_id values"
            )
        parsed[row.gauge_id] = dict(row.attributes)
    return parsed


# ── validation_report.json ──


def _validate_validation_report(
    model: _ValidationReportModel, basins: tuple[BasinRecord, ...]
) -> None:
    """§8: one entry per basin, identity agreement with basins.gpkg, the
    required check keys (enforced by ``_ValidationChecksModel``), and summary
    consistency. Any violation rejects the package."""
    basin_by_key = {(b.network, b.basin_code): b for b in basins}

    if len(model.basins) != len(basins):
        raise BasinPackageRejectedError(
            "validation_report.json must have one entry per basins.gpkg feature "
            f"(got {len(model.basins)} entries for {len(basins)} basins)"
        )

    seen_keys: set[tuple[str, str]] = set()
    for entry in model.basins:
        key = (entry.network, entry.basin_code)
        if key in seen_keys:
            raise BasinPackageRejectedError(
                f"validation_report.json has duplicate entry for {key}"
            )
        seen_keys.add(key)
        basin = basin_by_key.get(key)
        if basin is None:
            raise BasinPackageRejectedError(
                f"validation_report.json entry {key} matches no basins.gpkg basin"
            )
        mismatches = {
            field: (report_value, basin_value)
            for field, report_value, basin_value in (
                ("station_code", entry.station_code, basin.station_code),
                ("gateway_hru_name", entry.gateway_hru_name, basin.gateway_hru_name),
                ("name", entry.name, basin.name),
            )
            if report_value != basin_value
        }
        if mismatches:
            raise BasinPackageRejectedError(
                f"validation_report.json entry {key} disagrees with basins.gpkg: "
                f"{mismatches}"
            )

    status_counts = {"passed": 0, "warning": 0, "failed": 0}
    for entry in model.basins:
        status_counts[entry.status] += 1
    summary = model.summary
    if (
        summary.passed != status_counts["passed"]
        or summary.failed != status_counts["failed"]
        or summary.warnings != status_counts["warning"]
    ):
        raise BasinPackageRejectedError(
            "validation_report.json summary is inconsistent with per-basin "
            f"statuses: summary={{'passed': {summary.passed}, 'failed': "
            f"{summary.failed}, 'warnings': {summary.warnings}}}, counted="
            f"{status_counts}"
        )


# ──────────────────────────────────────────────
# Task 1B — gauge_id join + per-basin acceptance (§9 second list)
# ──────────────────────────────────────────────


def evaluate_basin_acceptance(
    loaded: LoadedBasinPackage,
    *,
    resolve_station: Callable[[str, str], StationId | None],
    assigned_model_features: Callable[[BasinRecord], frozenset[str]] | None = None,
) -> BasinPackageAcceptanceReport:
    """Join ``basins.gpkg`` to ``static_attributes.parquet`` on ``gauge_id``
    (failing loudly, no partial import) and evaluate each basin's per-basin
    accept / ``onboarding``-hold decision (contract §9 second list).

    ``resolve_station`` matches a station by its ``(code, network)`` pair —
    SAP3 station identity is network-scoped
    (``PgStationStore.fetch_station_by_code``, ``db/metadata.py`` §
    ``uq_stations_network_code``). Never call it with the code alone.

    ``assigned_model_features`` is the SEAM for the later Task 2A/2C slice:
    given a basin, it returns the static features that basin's ASSIGNED models
    genuinely require. When a required-static feature is null/missing, the basin
    is held in ``onboarding`` ONLY if that feature is in this assigned set (§9);
    otherwise the null is surfaced as a VISIBLE per-basin WARNING, not an
    onboarding hold (§9/§10: "SHOULD allow import with per-basin warnings when
    the basin is not yet assigned to a model requiring the missing feature").
    This slice has no DB-backed per-station model-assignment source, so the
    default (``None``) treats NO basin as verifiably assigned — every
    catalog-required-but-null feature becomes a warning, never a hold.
    """
    _validate_gauge_id_join(loaded)

    # Features some model declares a need for (catalog-level, not basin-scoped).
    catalog_required = frozenset(
        entry.name for entry in loaded.feature_catalog if entry.required_by_models
    )
    coverage_by_key = {
        (entry.network, entry.basin_code): entry.checks.get("coverage_status")
        for entry in loaded.validation_report.basins
    }

    decisions = tuple(
        _evaluate_one_basin(
            basin,
            loaded,
            catalog_required=catalog_required,
            coverage_by_key=coverage_by_key,
            resolve_station=resolve_station,
            assigned_model_features=assigned_model_features,
        )
        for basin in loaded.basins
    )
    return BasinPackageAcceptanceReport(decisions=decisions)


def _validate_gauge_id_join(loaded: LoadedBasinPackage) -> None:
    basin_gauge_ids = {b.gauge_id for b in loaded.basins}
    static_gauge_ids = set(loaded.static_attributes.keys())
    only_in_basins = basin_gauge_ids - static_gauge_ids
    only_in_static = static_gauge_ids - basin_gauge_ids
    if only_in_basins or only_in_static:
        raise BasinPackageRejectedError(
            "gauge_id join between basins.gpkg and static_attributes.parquet is "
            f"incomplete — only in basins.gpkg: {sorted(only_in_basins)}; only in "
            f"static_attributes.parquet: {sorted(only_in_static)}"
        )


def _evaluate_one_basin(
    basin: BasinRecord,
    loaded: LoadedBasinPackage,
    *,
    catalog_required: frozenset[str],
    coverage_by_key: dict[tuple[str, str], Any],
    resolve_station: Callable[[str, str], StationId | None],
    assigned_model_features: Callable[[BasinRecord], frozenset[str]] | None,
) -> BasinAcceptanceDecision:
    hold_reasons: list[str] = []
    warnings: list[str] = []

    geom = basin.geometry
    if (
        geom is None
        or geom.is_empty
        or not geom.is_valid
        or geom.has_z
        or geom.geom_type not in ("Polygon", "MultiPolygon")
    ):
        hold_reasons.append(
            "basin geometry missing, empty, invalid, or not 2-D Polygon/MultiPolygon"
        )

    if math.isnan(basin.area_km2) or basin.area_km2 <= 0:
        hold_reasons.append(f"area_km2 non-positive ({basin.area_km2})")

    station_id = resolve_station(basin.station_code, basin.network)
    if station_id is None:
        hold_reasons.append(
            f"(network={basin.network!r}, station_code={basin.station_code!r}) "
            "unmatched to a SAP3 station"
        )

    _evaluate_required_static(
        basin,
        loaded,
        catalog_required=catalog_required,
        assigned_model_features=assigned_model_features,
        hold_reasons=hold_reasons,
        warnings=warnings,
    )

    if basin.gateway_hru_name not in loaded.manifest.gateway_hru_names:
        hold_reasons.append(
            f"gateway_hru_name {basin.gateway_hru_name!r} not declared in "
            "manifest.gateway_hru_names"
        )

    coverage = coverage_by_key.get((basin.network, basin.basin_code))
    if coverage == "outside":
        hold_reasons.append("basin lies outside required coverage")
    elif coverage in ("partial", "unknown"):
        warnings.append(f"basin coverage_status is {coverage!r}")

    outcome: Literal["accepted", "onboarding_hold"] = (
        "onboarding_hold" if hold_reasons else "accepted"
    )
    decision = BasinAcceptanceDecision(
        network=basin.network,
        station_code=basin.station_code,
        basin_code=basin.basin_code,
        outcome=outcome,
        station_id=station_id,
        warnings=tuple(warnings),
        hold_reasons=tuple(hold_reasons),
    )
    if outcome == "onboarding_hold":
        log.info(
            "basin_package.basin_held_in_onboarding",
            network=basin.network,
            station_code=basin.station_code,
            basin_code=basin.basin_code,
            hold_reasons=decision.hold_reasons,
        )
    return decision


def _evaluate_required_static(
    basin: BasinRecord,
    loaded: LoadedBasinPackage,
    *,
    catalog_required: frozenset[str],
    assigned_model_features: Callable[[BasinRecord], frozenset[str]] | None,
    hold_reasons: list[str],
    warnings: list[str],
) -> None:
    """Per §9/§10: a null required-static feature is an onboarding HOLD only when
    the basin is VERIFIABLY assigned to a model needing it; otherwise a visible
    WARNING (accept-with-warning). See ``assigned_model_features``."""
    static_values = loaded.static_attributes.get(basin.gauge_id, {})
    assigned = (
        assigned_model_features(basin)
        if assigned_model_features is not None
        else frozenset()
    )
    held: list[str] = []
    warned: list[str] = []
    for name in sorted(catalog_required):
        if not _is_missing(static_values.get(name)):
            continue
        if name in assigned:
            held.append(name)
        else:
            warned.append(name)
    if held:
        hold_reasons.append(
            f"required static feature(s) missing/null for an assigned model: {held}"
        )
    if warned:
        warnings.append(
            "static feature(s) declared required_by_models are null/missing but the "
            f"basin is not verifiably assigned to a model needing them: {warned}"
        )


def _is_missing(value: float | None) -> bool:
    if value is None:
        return True
    return isinstance(value, float) and math.isnan(value)
