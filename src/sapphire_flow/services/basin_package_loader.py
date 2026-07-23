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

No DB writes happen here (Task 2A/2C, a later slice).
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportArgumentType=false
# pyright: reportGeneralTypeIssues=false, reportAttributeAccessIssue=false
from __future__ import annotations

import hashlib
import math
import re
from datetime import date  # noqa: TC003 -- pydantic needs this at runtime
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import geopandas as gpd
import polars as pl
import pyogrio.errors
import structlog
from pydantic import BaseModel, Field, ValidationError

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
    from pathlib import Path

    from sapphire_flow.types.ids import StationId

log = structlog.get_logger(__name__)

_SUPPORTED_CONTRACT_VERSION = "basin-static-artifact/v1"
_MANDATORY_FILES: tuple[str, ...] = (
    "manifest.json",
    "basins.gpkg",
    "static_attributes.parquet",
    "feature_catalog.json",
    "validation_report.json",
)
_OPTIONAL_PAYLOAD_FILES: tuple[str, ...] = ("bands.gpkg", "README.md")
# §3a: internal layer/table name AND per-feature `name` must start with a
# letter or underscore. §4a additionally requires lowercase.
_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
_FORCING_DERIVED_SOURCE_DATASET = "ERA5-Land"

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


class _ValidationSummaryModel(BaseModel):
    passed: int
    failed: int
    warnings: int


class _ValidationBasinEntryModel(BaseModel):
    network: str
    basin_code: str
    station_code: str
    gateway_hru_name: str
    name: str
    status: Literal["passed", "warning", "failed"]
    checks: dict[str, Any]
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

    missing_files = [
        name for name in _MANDATORY_FILES if not (package_dir / name).is_file()
    ]
    if missing_files:
        raise BasinPackageRejectedError(f"mandatory file(s) missing: {missing_files}")

    computed_checksums = _compute_and_verify_checksums(package_dir, manifest_model)
    manifest = _domain_manifest(manifest_model)

    basins_gdf = _read_geometry_file(package_dir / "basins.gpkg", label="basins.gpkg")
    _validate_basin_columns(basins_gdf)
    _validate_network_consistency(basins_gdf, manifest.network, label="basins.gpkg")
    _validate_lat_lon_equality(basins_gdf)
    _validate_name_uniqueness_and_format(basins_gdf, label="basins.gpkg")
    _validate_basin_code_uniqueness(basins_gdf)
    basins = tuple(_row_to_basin_record(row) for _, row in basins_gdf.iterrows())

    bands: tuple[BandRecord, ...] | None = None
    bands_path = package_dir / "bands.gpkg"
    if bands_path.is_file():
        bands_gdf = _read_geometry_file(bands_path, label="bands.gpkg")
        bands = _validate_and_build_bands(bands_gdf, manifest, basins)

    static_df = _read_static_attributes(package_dir / "static_attributes.parquet")
    static_columns = frozenset(static_df.columns) - {"gauge_id"}

    catalog_model = _parse_model(
        _FeatureCatalogModel, package_dir / "feature_catalog.json"
    )
    feature_catalog = _validate_feature_catalog(catalog_model, manifest, static_columns)

    validation_report_model = _parse_model(
        _ValidationReportModel, package_dir / "validation_report.json"
    )
    validation_report = _domain_validation_report(validation_report_model)

    return LoadedBasinPackage(
        manifest=manifest,
        basins=basins,
        bands=bands,
        feature_catalog=feature_catalog,
        static_attributes=_static_attributes_by_gauge_id(static_df),
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
                checks=dict(b.checks),
                warnings=tuple(b.warnings),
                errors=tuple(b.errors),
            )
            for b in model.basins
        ),
    )


# ── Checksums ──


def _payload_files(package_dir: Path, manifest_model: _ManifestModel) -> list[str]:
    """The canonical payload file set SAP3 hashes — the producer-declared
    set (``manifest.checksums`` keys) when present; otherwise every
    ``manifest.files`` value plus any present optional payload file. Always
    excludes the self-referential ``manifest.json``/``checksums.sha256``."""
    if manifest_model.checksums:
        return list(manifest_model.checksums.keys())
    payload = set(manifest_model.files.values())
    payload.update(
        name
        for name in _OPTIONAL_PAYLOAD_FILES
        if name != "checksums.sha256" and (package_dir / name).is_file()
    )
    return sorted(payload)


def _compute_and_verify_checksums(
    package_dir: Path, manifest_model: _ManifestModel
) -> dict[str, str]:
    computed: dict[str, str] = {}
    for filename in _payload_files(package_dir, manifest_model):
        path = package_dir / filename
        if not path.is_file():
            raise BasinPackageRejectedError(
                f"declared payload file {filename!r} is absent from the package"
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        computed[filename] = f"sha256:{digest}"

    for filename, declared in manifest_model.checksums.items():
        actual = computed.get(filename)
        if actual != declared:
            raise BasinPackageRejectedError(
                f"checksum mismatch for {filename!r}: declared {declared!r}, "
                f"computed {actual!r}"
            )

    sidecar_path = package_dir / "checksums.sha256"
    if sidecar_path.is_file() and manifest_model.checksums:
        sidecar_hashes = _parse_sha256_sidecar(sidecar_path)
        for filename, declared in manifest_model.checksums.items():
            sidecar_value = sidecar_hashes.get(filename)
            if sidecar_value is not None and sidecar_value != declared:
                raise BasinPackageRejectedError(
                    f"manifest.checksums and checksums.sha256 disagree for "
                    f"{filename!r}: {declared!r} vs {sidecar_value!r}"
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
            continue
        digest, filename = parts
        hashes[filename.lstrip("*")] = f"sha256:{digest}"
    return hashes


# ── basins.gpkg ──


def _read_geometry_file(path: Path, *, label: str) -> gpd.GeoDataFrame:
    try:
        gdf = gpd.read_file(path)
    except pyogrio.errors.DataSourceError as exc:
        raise BasinPackageRejectedError(f"{label} could not be read: {exc}") from exc
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        raise BasinPackageRejectedError(f"{label} is not EPSG:4326 (got {gdf.crs!r})")
    return gdf


def _validate_basin_columns(gdf: gpd.GeoDataFrame) -> None:
    missing = [c for c in _BASIN_REQUIRED_COLUMNS if c not in gdf.columns]
    if "geometry" not in gdf.columns:
        missing.append("geometry")
    if missing:
        raise BasinPackageRejectedError(
            f"basins.gpkg missing required column(s): {missing}"
        )


def _validate_network_consistency(
    gdf: gpd.GeoDataFrame, network: str, *, label: str
) -> None:
    bad = gdf[gdf["network"] != network]
    if not bad.empty:
        raise BasinPackageRejectedError(
            f"{label} network column disagrees with manifest.network={network!r} "
            f"for station_code(s): {bad['station_code'].tolist()}"
        )


def _validate_lat_lon_equality(gdf: gpd.GeoDataFrame) -> None:
    mismatched = gdf[
        (gdf["latitude"] != gdf["outlet_lat"]) | (gdf["longitude"] != gdf["outlet_lon"])
    ]
    if not mismatched.empty:
        raise BasinPackageRejectedError(
            "basins.gpkg latitude/longitude disagree with outlet_lat/outlet_lon "
            f"for station_code(s): {mismatched['station_code'].tolist()}"
        )


def _validate_name_uniqueness_and_format(gdf: gpd.GeoDataFrame, *, label: str) -> None:
    for _, row in gdf.iterrows():
        name = row["name"]
        if not isinstance(name, str) or not _NAME_PATTERN.match(name):
            raise BasinPackageRejectedError(
                f"{label} feature name {name!r} (station_code={row['station_code']!r}) "
                "must be lowercase and must not start with a digit"
            )
    duplicated_names = gdf["name"][gdf["name"].duplicated(keep=False)].unique().tolist()
    if duplicated_names:
        colliding_codes = gdf.loc[
            gdf["name"].isin(duplicated_names), "station_code"
        ].tolist()
        raise BasinPackageRejectedError(
            f"{label} feature name collision {duplicated_names} among "
            f"station_code(s): {colliding_codes}"
        )


def _validate_basin_code_uniqueness(gdf: gpd.GeoDataFrame) -> None:
    dupes = gdf[gdf.duplicated(subset=["network", "basin_code"], keep=False)]
    if not dupes.empty:
        raise BasinPackageRejectedError(
            "basins.gpkg has duplicate (network, basin_code) pairs: "
            f"{dupes[['network', 'basin_code']].drop_duplicates().values.tolist()}"
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return str(value)


def _row_to_basin_record(row: Any) -> BasinRecord:
    coverage_status = (
        _optional_str(row["coverage_status"]) if "coverage_status" in row else None
    )
    return BasinRecord(
        network=row["network"],
        station_code=str(row["station_code"]),
        basin_code=str(row["basin_code"]),
        gateway_hru_name=row["gateway_hru_name"],
        name=row["name"],
        display_name=row["display_name"],
        area_km2=float(row["area_km2"]),
        outlet_lon=float(row["outlet_lon"]),
        outlet_lat=float(row["outlet_lat"]),
        delineation_method=str(row["delineation_method"]),
        geometry=row["geometry"],
        gauge_id=str(row["gauge_id"]),
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
        regional_basin=(
            _optional_str(row["regional_basin"]) if "regional_basin" in row else None
        ),
        outlet_snap_distance_m=(
            _optional_float(row["outlet_snap_distance_m"])
            if "outlet_snap_distance_m" in row
            else None
        ),
        coverage_status=coverage_status,  # type: ignore[arg-type]
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

    _validate_network_consistency(gdf, manifest.network, label="bands.gpkg")
    _validate_name_uniqueness_and_format(gdf, label="bands.gpkg")

    basin_by_code = {b.basin_code: b for b in basins}
    for _, row in gdf.iterrows():
        basin_code = str(row["basin_code"])
        parent = basin_by_code.get(basin_code)
        if parent is None:
            raise BasinPackageRejectedError(
                f"bands.gpkg basin_code {basin_code!r} does not reference a "
                "basins.gpkg basin"
            )
        if str(row["station_code"]) != parent.station_code:
            raise BasinPackageRejectedError(
                f"bands.gpkg station_code {row['station_code']!r} does not match "
                f"parent basin {basin_code!r} station_code {parent.station_code!r}"
            )
        if row["gateway_hru_name"] not in manifest.gateway_hru_names:
            raise BasinPackageRejectedError(
                f"bands.gpkg gateway_hru_name {row['gateway_hru_name']!r} not "
                "declared in manifest.gateway_hru_names"
            )
        if not row["display_name"]:
            raise BasinPackageRejectedError(
                f"bands.gpkg display_name missing for band {row['name']!r}"
            )
        min_elev, max_elev = row["min_elevation_m"], row["max_elevation_m"]
        if min_elev is None or max_elev is None or not (max_elev > min_elev):
            raise BasinPackageRejectedError(
                "bands.gpkg max_elevation_m must exceed min_elevation_m "
                f"(got min={min_elev!r}, max={max_elev!r}) for band {row['name']!r}"
            )
        area = row["area_km2"]
        if area is None or not (area > 0):
            raise BasinPackageRejectedError(
                f"bands.gpkg area_km2 must be positive (got {area!r}) for "
                f"band {row['name']!r}"
            )
        geom = row["geometry"]
        if (
            geom is None
            or geom.is_empty
            or not geom.is_valid
            or geom.has_z
            or geom.geom_type not in ("Polygon", "MultiPolygon")
        ):
            raise BasinPackageRejectedError(
                f"bands.gpkg geometry invalid for band {row['name']!r}"
            )

    band_id_dupes = gdf[
        gdf.duplicated(subset=["network", "basin_code", "band_id"], keep=False)
    ]
    if not band_id_dupes.empty:
        raise BasinPackageRejectedError(
            "bands.gpkg band_id not unique within network+basin_code: "
            f"{band_id_dupes[['network', 'basin_code', 'band_id']].values.tolist()}"
        )

    return tuple(
        BandRecord(
            network=row["network"],
            basin_code=str(row["basin_code"]),
            station_code=str(row["station_code"]),
            band_id=int(row["band_id"]),
            gateway_hru_name=row["gateway_hru_name"],
            name=row["name"],
            display_name=row["display_name"],
            min_elevation_m=float(row["min_elevation_m"]),
            max_elevation_m=float(row["max_elevation_m"]),
            area_km2=float(row["area_km2"]),
            geometry=row["geometry"],
        )
        for _, row in gdf.iterrows()
    )


# ── feature_catalog.json ──


def _validate_feature_catalog(
    catalog_model: _FeatureCatalogModel,
    manifest: PackageManifest,
    static_columns: frozenset[str],
) -> tuple[FeatureCatalogEntry, ...]:
    catalog_names = {e.name for e in catalog_model.features}

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

    source_names = {d.name for d in manifest.source_datasets}
    entries: list[FeatureCatalogEntry] = []
    for entry in catalog_model.features:
        if entry.source_dataset not in source_names:
            raise BasinPackageRejectedError(
                f"feature_catalog.json entry {entry.name!r} source_dataset "
                f"{entry.source_dataset!r} not in manifest.source_datasets"
            )
        window = _domain_climatology_window(entry.climatology_window)
        is_forcing_derived = entry.source_dataset == _FORCING_DERIVED_SOURCE_DATASET
        if is_forcing_derived and window is None:
            raise BasinPackageRejectedError(
                f"feature_catalog.json entry {entry.name!r} is forcing-derived "
                f"(source_dataset={_FORCING_DERIVED_SOURCE_DATASET!r}) but has no "
                "climatology_window"
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

    gauge_ids = df["gauge_id"].to_list()
    if any(g is None or g == "" for g in gauge_ids):
        raise BasinPackageRejectedError(
            "static_attributes.parquet has a missing gauge_id value"
        )
    if len(gauge_ids) != len(set(gauge_ids)):
        raise BasinPackageRejectedError(
            "static_attributes.parquet has duplicate gauge_id values"
        )
    return df


def _static_attributes_by_gauge_id(
    df: pl.DataFrame,
) -> dict[str, dict[str, float | None]]:
    attribute_columns = [c for c in df.columns if c != "gauge_id"]
    return {
        row["gauge_id"]: {col: row[col] for col in attribute_columns}
        for row in df.iter_rows(named=True)
    }


# ──────────────────────────────────────────────
# Task 1B — gauge_id join + per-basin acceptance (§9 second list)
# ──────────────────────────────────────────────


def evaluate_basin_acceptance(
    loaded: LoadedBasinPackage,
    *,
    resolve_station: Callable[[str, str], StationId | None],
) -> BasinPackageAcceptanceReport:
    """Join ``basins.gpkg`` to ``static_attributes.parquet`` on ``gauge_id``
    (failing loudly, no partial import) and evaluate each basin's per-basin
    accept / ``onboarding``-hold decision (contract §9 second list).

    ``resolve_station`` matches a station by its ``(code, network)`` pair —
    SAP3 station identity is network-scoped
    (``PgStationStore.fetch_station_by_code``, ``db/metadata.py`` §
    ``uq_stations_network_code``). Never call it with the code alone.
    """
    _validate_gauge_id_join(loaded)

    required_feature_names = frozenset(
        entry.name for entry in loaded.feature_catalog if entry.required_by_models
    )

    decisions = tuple(
        _evaluate_one_basin(basin, loaded, required_feature_names, resolve_station)
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
    required_feature_names: frozenset[str],
    resolve_station: Callable[[str, str], StationId | None],
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

    if basin.area_km2 <= 0:
        hold_reasons.append(f"area_km2 non-positive ({basin.area_km2})")

    station_id = resolve_station(basin.station_code, basin.network)
    if station_id is None:
        hold_reasons.append(
            f"(network={basin.network!r}, station_code={basin.station_code!r}) "
            "unmatched to a SAP3 station"
        )

    static_values = loaded.static_attributes.get(basin.gauge_id, {})
    missing_required = [
        name
        for name in sorted(required_feature_names)
        if _is_missing(static_values.get(name))
    ]
    if missing_required:
        hold_reasons.append(
            "required static feature(s) missing/null for an assigned model: "
            f"{missing_required}"
        )

    if basin.gateway_hru_name not in loaded.manifest.gateway_hru_names:
        hold_reasons.append(
            f"gateway_hru_name {basin.gateway_hru_name!r} not declared in "
            "manifest.gateway_hru_names"
        )

    if basin.coverage_status == "outside":
        hold_reasons.append("basin lies outside required coverage")
    elif basin.coverage_status in ("partial", "unknown"):
        warnings.append(f"basin coverage_status is {basin.coverage_status!r}")

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


def _is_missing(value: float | None) -> bool:
    if value is None:
        return True
    return isinstance(value, float) and math.isnan(value)
