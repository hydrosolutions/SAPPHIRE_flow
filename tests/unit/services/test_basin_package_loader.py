"""Plan 120 Phase 1 (Task 1A/1B) — basin/static package loader + acceptance.

Red-first acceptance tests locked from
``docs/requirements/04-basin-static-artifact-contract.md`` §9 and
``docs/plans/120-basin-static-importer.md`` Task 1A/1B. Uses the real,
contract-compliant fixture at
``tests/fixtures/basin_static/nepal-dhm-basins/`` (copied per-test into a
tmp dir and mutated to build discriminating negative fixtures).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

import geopandas as gpd
import pandas as pd
import polars as pl
import pytest
from shapely.geometry import Point, Polygon

from sapphire_flow.exceptions import BasinPackageRejectedError
from sapphire_flow.services.basin_package_loader import (
    evaluate_basin_acceptance,
    load_basin_package,
)
from sapphire_flow.types.basin_package import (
    BasinRecord,
    FeatureCatalogEntry,
    LoadedBasinPackage,
    PackageManifest,
    ValidationReport,
    ValidationReportBasinEntry,
)
from sapphire_flow.types.ids import StationId

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "basin_static"
    / "nepal-dhm-basins"
)

_VALID_GEOM = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])


# ──────────────────────────────────────────────
# File-based fixture helpers (mutate a copy of the real package)
# ──────────────────────────────────────────────


def _copy_fixture(tmp_path: Path) -> Path:
    dest = tmp_path / "pkg"
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


def _read_manifest(pkg_dir: Path) -> dict:
    return json.loads((pkg_dir / "manifest.json").read_text())


def _write_manifest(pkg_dir: Path, manifest: dict) -> None:
    (pkg_dir / "manifest.json").write_text(json.dumps(manifest))


def _recompute_checksums(pkg_dir: Path) -> None:
    """Recompute manifest.checksums for whichever declared payload files
    exist, so a deliberate content mutation doesn't ALSO trip the (unrelated)
    checksum-mismatch rule and mask the rule actually under test."""
    manifest = _read_manifest(pkg_dir)
    for filename in manifest["checksums"]:
        path = pkg_dir / filename
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest["checksums"][filename] = f"sha256:{digest}"
    _write_manifest(pkg_dir, manifest)


def _mutate_basins_gpkg(pkg_dir: Path, mutate) -> None:
    gdf = gpd.read_file(pkg_dir / "basins.gpkg")
    gdf = mutate(gdf)
    (pkg_dir / "basins.gpkg").unlink()
    gdf.to_file(pkg_dir / "basins.gpkg", driver="GPKG")


def _mutate_static_parquet(pkg_dir: Path, mutate) -> None:
    df = pl.read_parquet(pkg_dir / "static_attributes.parquet")
    df = mutate(df)
    df.write_parquet(pkg_dir / "static_attributes.parquet")


def _mutate_json(pkg_dir: Path, filename: str, mutate) -> None:
    path = pkg_dir / filename
    data = json.loads(path.read_text())
    data = mutate(data)
    path.write_text(json.dumps(data))


def _feature_index(catalog: dict, name: str) -> int:
    for i, feature in enumerate(catalog["features"]):
        if feature["name"] == name:
            return i
    raise AssertionError(f"no feature_catalog entry named {name!r}")


def _concat_gdf(first: gpd.GeoDataFrame, second: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    combined = pd.concat([first, second], ignore_index=True)
    return gpd.GeoDataFrame(combined, crs=first.crs)


_BAND_GEOM = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])


def _valid_band_row(**overrides: object) -> dict:
    row: dict = dict(
        network="dhm",
        basin_code="123",
        station_code="123",
        band_id=1,
        gateway_hru_name="nepal_dhm_v1",
        name="g_123_band_1",
        display_name="band 1",
        min_elevation_m=100.0,
        max_elevation_m=500.0,
        area_km2=10.0,
        geometry=_BAND_GEOM,
    )
    row.update(overrides)
    return row


def _write_bands_gpkg(pkg_dir: Path, rows: list[dict]) -> None:
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf.to_file(pkg_dir / "bands.gpkg", driver="GPKG")


def _write_sidecar(pkg_dir: Path, entries: dict[str, str]) -> None:
    lines = [f"{digest}  {name}" for name, digest in entries.items()]
    (pkg_dir / "checksums.sha256").write_text("\n".join(lines) + "\n")


def _hex_of(pkg_dir: Path, filename: str) -> str:
    return hashlib.sha256((pkg_dir / filename).read_bytes()).hexdigest()


# ──────────────────────────────────────────────
# Task 1A — whole-package acceptance
# ──────────────────────────────────────────────


class TestWholePackageAcceptance:
    def test_well_formed_package_parses(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        loaded = load_basin_package(pkg_dir)

        assert loaded.manifest.network == "dhm"
        assert loaded.manifest.contract_version == "basin-static-artifact/v1"
        assert len(loaded.basins) == 1
        assert loaded.basins[0].gauge_id == "nepal_123"
        assert loaded.static_attributes["nepal_123"]["area"] is not None
        assert len(loaded.feature_catalog) == 92
        assert (
            loaded.computed_checksums["basins.gpkg"]
            == (loaded.manifest.checksums["basins.gpkg"])
        )

    def test_absent_bands_gpkg_parses_clean(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        assert not (pkg_dir / "bands.gpkg").exists()

        loaded = load_basin_package(pkg_dir)

        assert loaded.bands is None

    def test_present_malformed_bands_gpkg_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        (pkg_dir / "bands.gpkg").write_bytes(b"not a real geopackage")

        with pytest.raises(BasinPackageRejectedError, match="bands.gpkg"):
            load_basin_package(pkg_dir)

    def test_unsupported_contract_version_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        manifest["contract_version"] = "basin-static-artifact/v2"
        _write_manifest(pkg_dir, manifest)

        with pytest.raises(BasinPackageRejectedError, match="contract_version"):
            load_basin_package(pkg_dir)

    @pytest.mark.parametrize(
        "filename",
        [
            "basins.gpkg",
            "static_attributes.parquet",
            "feature_catalog.json",
            "validation_report.json",
        ],
    )
    def test_missing_mandatory_file_rejects(
        self, tmp_path: Path, filename: str
    ) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        (pkg_dir / filename).unlink()

        with pytest.raises(BasinPackageRejectedError, match="mandatory file"):
            load_basin_package(pkg_dir)

    def test_checksum_mismatch_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        # Mutate the payload WITHOUT recomputing its declared checksum.
        _mutate_json(
            pkg_dir,
            "validation_report.json",
            lambda d: {**d, "summary": {**d["summary"], "warnings": 999}},
        )

        with pytest.raises(BasinPackageRejectedError, match="checksum mismatch"):
            load_basin_package(pkg_dir)

    def test_file_mutated_vs_present_checksum_rejects(self, tmp_path: Path) -> None:
        """Same rule as the checksum-mismatch case above, phrased per the
        plan's own verification wording ("a file mutated vs a present
        producer checksum rejects")."""
        pkg_dir = _copy_fixture(tmp_path)
        readme = pkg_dir / "README.md"
        readme.write_text(readme.read_text() + "\nmutated\n")

        with pytest.raises(BasinPackageRejectedError, match="checksum mismatch"):
            load_basin_package(pkg_dir)

    def test_empty_network_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        manifest["network"] = ""
        _write_manifest(pkg_dir, manifest)

        with pytest.raises(BasinPackageRejectedError):
            load_basin_package(pkg_dir)

    def test_conflicting_network_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        manifest["network"] = "some_other_network"
        _write_manifest(pkg_dir, manifest)

        with pytest.raises(BasinPackageRejectedError, match="network"):
            load_basin_package(pkg_dir)

    def test_geometry_not_epsg4326_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _mutate_basins_gpkg(pkg_dir, lambda gdf: gdf.to_crs(epsg=3857))
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="EPSG:4326"):
            load_basin_package(pkg_dir)

    def test_duplicate_gateway_feature_name_rejects(self, tmp_path: Path) -> None:
        """§4a normalization is NOT injective: two DIFFERENT station codes that
        normalize to the same `g_<code>` name collide → package failure naming
        both codes."""
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
            first = gdf.iloc[[0]].copy()
            first["station_code"] = "ab/1"
            first["basin_code"] = "ab/1"
            first["gauge_id"] = "nepal_ab1"
            first["name"] = "g_ab_1"
            second = gdf.iloc[[0]].copy()
            # `ab-1` and `ab/1` both normalize to `ab_1` -> same `g_ab_1` name.
            second["station_code"] = "ab-1"
            second["basin_code"] = "ab-1"
            second["gauge_id"] = "nepal_ab2"
            second["name"] = "g_ab_1"
            return _concat_gdf(first, second)

        _mutate_basins_gpkg(pkg_dir, mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="collision"):
            load_basin_package(pkg_dir)

    def test_duplicate_basin_code_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
            second = gdf.iloc[[0]].copy()
            second["station_code"] = "456"
            second["name"] = "g_456"
            second["gauge_id"] = "nepal_456"
            # SAME (network, basin_code) as the first row.
            return _concat_gdf(gdf, second)

        _mutate_basins_gpkg(pkg_dir, mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="basin_code"):
            load_basin_package(pkg_dir)

    def test_feature_catalog_omits_parquet_column_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(catalog: dict) -> dict:
            idx = _feature_index(catalog, "area")
            del catalog["features"][idx]
            return catalog

        _mutate_json(pkg_dir, "feature_catalog.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="omits"):
            load_basin_package(pkg_dir)

    def test_catalog_entry_without_parquet_column_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(catalog: dict) -> dict:
            extra = dict(catalog["features"][0])
            extra["name"] = "not_a_real_parquet_column"
            catalog["features"].append(extra)
            return catalog

        _mutate_json(pkg_dir, "feature_catalog.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="no matching"):
            load_basin_package(pkg_dir)

    def test_catalog_source_dataset_not_in_manifest_rejects(
        self, tmp_path: Path
    ) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(catalog: dict) -> dict:
            idx = _feature_index(catalog, "area")
            catalog["features"][idx]["source_dataset"] = "NotInManifest"
            return catalog

        _mutate_json(pkg_dir, "feature_catalog.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="source_dataset"):
            load_basin_package(pkg_dir)

    def test_climatology_window_mismatch_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(catalog: dict) -> dict:
            idx = _feature_index(catalog, "p_mean")
            catalog["features"][idx]["climatology_window"] = {
                "start": "1981-01-01",
                "end": "2010-12-31",
            }
            return catalog

        _mutate_json(pkg_dir, "feature_catalog.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="climatology_window"):
            load_basin_package(pkg_dir)

    @pytest.mark.parametrize("field", ["aggregation", "description"])
    def test_catalog_entry_missing_field_rejects(
        self, tmp_path: Path, field: str
    ) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(catalog: dict, field: str = field) -> dict:
            idx = _feature_index(catalog, "area")
            del catalog["features"][idx][field]
            return catalog

        _mutate_json(pkg_dir, "feature_catalog.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="schema validation"):
            load_basin_package(pkg_dir)

    def test_forcing_derived_entry_missing_climatology_window_rejects(
        self, tmp_path: Path
    ) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(catalog: dict) -> dict:
            idx = _feature_index(catalog, "p_mean")
            catalog["features"][idx]["climatology_window"] = None
            return catalog

        _mutate_json(pkg_dir, "feature_catalog.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="forcing-derived"):
            load_basin_package(pkg_dir)

    def test_geometry_derived_entry_null_climatology_window_ok(
        self, tmp_path: Path
    ) -> None:
        """The contrast case for the rule above: a geometry-derived
        (HydroATLAS) entry's `climatology_window: null` is VALID, not a
        reject."""
        pkg_dir = _copy_fixture(tmp_path)

        loaded = load_basin_package(pkg_dir)

        area_entry = next(e for e in loaded.feature_catalog if e.name == "area")
        assert area_entry.climatology_window is None

    def test_non_float64_attribute_column_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _mutate_static_parquet(
            pkg_dir, lambda df: df.with_columns(pl.col("area").cast(pl.Int64))
        )
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="Float64"):
            load_basin_package(pkg_dir)

    def test_duplicate_gauge_id_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _mutate_static_parquet(pkg_dir, lambda df: pl.concat([df, df]))
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="duplicate gauge_id"):
            load_basin_package(pkg_dir)

    def test_missing_gauge_id_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _mutate_static_parquet(
            pkg_dir,
            lambda df: df.with_columns(pl.lit(None).cast(pl.Utf8).alias("gauge_id")),
        )
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="missing gauge_id"):
            load_basin_package(pkg_dir)

    @pytest.mark.parametrize(
        "column",
        [
            "display_name",
            "outlet_lon",
            "outlet_lat",
            "delineation_method",
            "gauge_id",
            "latitude",
            "longitude",
        ],
    )
    def test_basins_gpkg_missing_required_column_rejects(
        self, tmp_path: Path, column: str
    ) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _mutate_basins_gpkg(
            pkg_dir, lambda gdf, column=column: gdf.drop(columns=[column])
        )
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="missing required column"):
            load_basin_package(pkg_dir)

    def test_latitude_outlet_lat_mismatch_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
            gdf = gdf.copy()
            gdf.loc[0, "latitude"] = gdf.loc[0, "latitude"] + 1.0
            return gdf

        _mutate_basins_gpkg(pkg_dir, mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="latitude/longitude"):
            load_basin_package(pkg_dir)

    def test_longitude_outlet_lon_mismatch_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
            gdf = gdf.copy()
            gdf.loc[0, "longitude"] = gdf.loc[0, "longitude"] + 1.0
            return gdf

        _mutate_basins_gpkg(pkg_dir, mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="latitude/longitude"):
            load_basin_package(pkg_dir)

    @pytest.mark.parametrize("top_level_field", ["summary", "basins"])
    def test_validation_report_missing_top_level_field_rejects(
        self, tmp_path: Path, top_level_field: str
    ) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(report: dict, field: str = top_level_field) -> dict:
            del report[field]
            return report

        _mutate_json(pkg_dir, "validation_report.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="schema validation"):
            load_basin_package(pkg_dir)

    @pytest.mark.parametrize(
        "per_basin_field", ["status", "checks", "warnings", "errors"]
    )
    def test_validation_report_missing_per_basin_field_rejects(
        self, tmp_path: Path, per_basin_field: str
    ) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(report: dict, field: str = per_basin_field) -> dict:
            del report["basins"][0][field]
            return report

        _mutate_json(pkg_dir, "validation_report.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="schema validation"):
            load_basin_package(pkg_dir)


class TestDuplicateBasinGaugeId:
    """Finding #4: a duplicate `gauge_id` WITHIN basins.gpkg is rejected before
    the set-based `gauge_id` join could silently hide it."""

    def test_duplicate_basin_gauge_id_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
            second = gdf.iloc[[0]].copy()
            second["station_code"] = "456"
            second["basin_code"] = "456"
            second["name"] = "g_456"
            # SAME gauge_id as row 0 -> a duplicate a set-based join would hide.
            second["gauge_id"] = "nepal_123"
            return _concat_gdf(gdf, second)

        _mutate_basins_gpkg(pkg_dir, mutate)

        # Keep the validation report consistent (one entry per basin, identities
        # agree) so ONLY the gauge_id-uniqueness rule can reject.
        def fix_report(report: dict) -> dict:
            extra = json.loads(json.dumps(report["basins"][0]))
            extra["basin_code"] = "456"
            extra["station_code"] = "456"
            extra["name"] = "g_456"
            report["basins"].append(extra)
            report["summary"]["passed"] = 2
            return report

        _mutate_json(pkg_dir, "validation_report.json", fix_report)
        # static_attributes still has only nepal_123, but the gauge_id-uniqueness
        # check runs during load (before the join), so the dup rejects first.
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="duplicate gauge_id"):
            load_basin_package(pkg_dir)


class TestExactGatewayNames:
    """Finding #3: the exact `g_<normalized station_code>` form is enforced, and
    basin↔band name spaces are disjoint across the whole package."""

    def test_basin_name_not_matching_gateway_form_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        # Legal per the generic pattern (lowercase, letter-leading) but NOT the
        # required g_<station_code> form for station_code "123". The validation
        # report `name` is updated to match so ONLY the exact-form rule (not the
        # report identity check) can reject — isolating the rule under test.
        _mutate_basins_gpkg(pkg_dir, lambda gdf: gdf.assign(name=["not_g_123"]))

        def fix_report(report: dict) -> dict:
            report["basins"][0]["name"] = "not_g_123"
            return report

        _mutate_json(pkg_dir, "validation_report.json", fix_report)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="required Gateway form"):
            load_basin_package(pkg_dir)

    def test_band_name_not_matching_gateway_band_form_rejects(
        self, tmp_path: Path
    ) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        # Legal per the generic pattern but NOT the required
        # g_<station_code>_band_<band_id> form.
        _write_bands_gpkg(pkg_dir, [_valid_band_row(name="g_123")])

        with pytest.raises(BasinPackageRejectedError, match="band form"):
            load_basin_package(pkg_dir)


class TestBoundaryParsing:
    """Finding #1: every external row is parsed through a strict Pydantic
    boundary model — a wrong-typed GeoPackage cell rejects the package rather
    than being silently coerced (`str(row[...])`)."""

    def test_non_string_station_code_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _mutate_basins_gpkg(
            pkg_dir,
            lambda gdf: gdf.assign(station_code=gdf["station_code"].astype("int64")),
        )
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="schema validation"):
            load_basin_package(pkg_dir)


class TestFeatureCatalogRules:
    """Finding #5: duplicate names, geometry-vs-forcing window rules driven by
    dataset purpose, and manifest-window coupling."""

    def test_duplicate_catalog_name_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(catalog: dict) -> dict:
            idx = _feature_index(catalog, "area")
            dup = dict(catalog["features"][idx])
            catalog["features"].append(dup)  # second entry named "area"
            return catalog

        _mutate_json(pkg_dir, "feature_catalog.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="duplicate feature name"):
            load_basin_package(pkg_dir)

    def test_geometry_derived_entry_with_window_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(catalog: dict) -> dict:
            # "area" is HydroATLAS (geometry-derived) -> window MUST be null.
            idx = _feature_index(catalog, "area")
            catalog["features"][idx]["climatology_window"] = {
                "start": "1991-01-01",
                "end": "2020-12-31",
            }
            return catalog

        _mutate_json(pkg_dir, "feature_catalog.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="geometry-derived"):
            load_basin_package(pkg_dir)

    def test_window_present_but_manifest_window_absent_rejects(
        self, tmp_path: Path
    ) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        # Drop the manifest window; a forcing-derived feature still declares one.
        manifest = _read_manifest(pkg_dir)
        del manifest["climatology_window"]
        _write_manifest(pkg_dir, manifest)
        _recompute_checksums(pkg_dir)

        with pytest.raises(
            BasinPackageRejectedError, match="manifest.climatology_window is absent"
        ):
            load_basin_package(pkg_dir)


class TestValidationReportContract:
    """Finding #6: §8 required checks, one-entry-per-basin cardinality, identity
    agreement with basins, and summary consistency."""

    def test_missing_required_check_key_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(report: dict) -> dict:
            del report["basins"][0]["checks"]["coverage_status"]
            return report

        _mutate_json(pkg_dir, "validation_report.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="schema validation"):
            load_basin_package(pkg_dir)

    def test_cardinality_mismatch_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(report: dict) -> dict:
            extra = json.loads(json.dumps(report["basins"][0]))
            extra["basin_code"] = "999"
            extra["station_code"] = "999"
            extra["name"] = "g_999"
            report["basins"].append(extra)
            return report

        _mutate_json(pkg_dir, "validation_report.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="one entry per"):
            load_basin_package(pkg_dir)

    def test_identity_disagreement_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(report: dict) -> dict:
            report["basins"][0]["name"] = "g_wrong"
            return report

        _mutate_json(pkg_dir, "validation_report.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="disagrees with basins"):
            load_basin_package(pkg_dir)

    def test_summary_inconsistent_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(report: dict) -> dict:
            report["summary"]["passed"] = 999
            return report

        _mutate_json(pkg_dir, "validation_report.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="summary is inconsistent"):
            load_basin_package(pkg_dir)

    @pytest.mark.parametrize("bad_value", ["1", -1], ids=["string", "negative"])
    def test_summary_count_wrong_type_or_negative_rejects(
        self, tmp_path: Path, bad_value: object
    ) -> None:
        """§8 summary counts are non-negative cardinalities: a string-typed
        (`"1"`) or negative (`-1`) count is rejected at the boundary, never
        silently coerced/accepted."""
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(report: dict, bad_value: object = bad_value) -> dict:
            report["summary"]["passed"] = bad_value
            return report

        _mutate_json(pkg_dir, "validation_report.json", mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="schema validation"):
            load_basin_package(pkg_dir)


class TestManifestConstraints:
    """Finding #9: strict manifest fields + the declared-path security boundary."""

    def test_non_utc_created_at_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        manifest["created_at"] = "2026-07-17T06:32:31+05:45"  # Nepal offset, not UTC
        _write_manifest(pkg_dir, manifest)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="schema validation"):
            load_basin_package(pkg_dir)

    def test_manifest_crs_not_4326_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        manifest["crs"] = "EPSG:3857"
        _write_manifest(pkg_dir, manifest)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="schema validation"):
            load_basin_package(pkg_dir)

    def test_bad_checksum_value_syntax_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        manifest["checksums"]["basins.gpkg"] = "not-a-sha256"
        _write_manifest(pkg_dir, manifest)

        with pytest.raises(BasinPackageRejectedError, match="schema validation"):
            load_basin_package(pkg_dir)

    @pytest.mark.parametrize(
        "bad_path",
        ["/etc/passwd", "../escape.gpkg", "manifest.json"],
        ids=["absolute", "dotdot", "self_referential"],
    )
    def test_unsafe_declared_path_rejects(self, tmp_path: Path, bad_path: str) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        manifest["files"]["evil"] = bad_path
        _write_manifest(pkg_dir, manifest)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="declared payload path"):
            load_basin_package(pkg_dir)


class TestChecksumSidecar:
    """Finding #2: a `checksums.sha256` sidecar is a first-class checksum source
    (never ignored), and must agree exactly with `manifest.checksums`."""

    def test_sidecar_only_mismatch_rejects(self, tmp_path: Path) -> None:
        """No `manifest.checksums`; a sidecar declares a WRONG hash -> reject
        (proves the sidecar is NOT ignored when the manifest omits checksums)."""
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        del manifest["checksums"]
        _write_manifest(pkg_dir, manifest)
        _write_sidecar(pkg_dir, {"basins.gpkg": "sha256:" + "0" * 64})

        with pytest.raises(BasinPackageRejectedError, match="checksum mismatch"):
            load_basin_package(pkg_dir)

    def test_sidecar_only_correct_parses(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        del manifest["checksums"]
        _write_manifest(pkg_dir, manifest)
        _write_sidecar(
            pkg_dir, {"basins.gpkg": "sha256:" + _hex_of(pkg_dir, "basins.gpkg")}
        )

        loaded = load_basin_package(pkg_dir)

        assert loaded.computed_checksums["basins.gpkg"] == (
            "sha256:" + _hex_of(pkg_dir, "basins.gpkg")
        )

    def test_sidecar_and_manifest_value_disagree_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        # sidecar mirrors manifest keys but one value differs.
        sidecar = dict(manifest["checksums"])
        sidecar["basins.gpkg"] = "sha256:" + "1" * 64
        _write_sidecar(pkg_dir, sidecar)

        with pytest.raises(BasinPackageRejectedError, match="disagree"):
            load_basin_package(pkg_dir)

    def test_sidecar_missing_an_entry_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        sidecar = dict(manifest["checksums"])
        del sidecar["basins.gpkg"]  # sidecar declares a SMALLER set
        _write_sidecar(pkg_dir, sidecar)

        with pytest.raises(BasinPackageRejectedError, match="different file"):
            load_basin_package(pkg_dir)

    @pytest.mark.parametrize(
        "bad_path", ["../secret", "/etc/passwd"], ids=["dotdot", "absolute"]
    )
    def test_sidecar_only_unsafe_path_rejects(
        self, tmp_path: Path, bad_path: str
    ) -> None:
        """A `checksums.sha256` sidecar key bypasses `_validate_declared_paths`
        (which only sees `manifest.files`/`manifest.checksums`). An unsafe
        sidecar-declared path (absolute or `..`-escaping) MUST be path-safety
        rejected BEFORE any payload file is opened/hashed."""
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        del manifest["checksums"]  # sidecar becomes the sole checksum source
        _write_manifest(pkg_dir, manifest)
        _write_sidecar(pkg_dir, {bad_path: "sha256:" + "0" * 64})

        with pytest.raises(BasinPackageRejectedError, match="declared payload path"):
            load_basin_package(pkg_dir)

    @pytest.mark.parametrize(
        "alias_path",
        ["./manifest.json", "./checksums.sha256"],
        ids=["manifest_alias", "checksums_alias"],
    )
    def test_sidecar_self_referential_alias_rejects(
        self, tmp_path: Path, alias_path: str
    ) -> None:
        """A `checksums.sha256` sidecar key that spells a self-referential
        filename with a `./` alias prefix (e.g. `./manifest.json`) MUST be
        rejected the same as the bare form. The self-reference guard compares
        the declared path against `manifest.json`/`checksums.sha256` -- an
        unnormalized alias must not slip through and get treated as an
        ordinary payload file."""
        pkg_dir = _copy_fixture(tmp_path)
        manifest = _read_manifest(pkg_dir)
        del manifest["checksums"]  # sidecar becomes the sole checksum source
        _write_manifest(pkg_dir, manifest)
        _write_sidecar(pkg_dir, {alias_path: "sha256:" + "0" * 64})

        with pytest.raises(BasinPackageRejectedError, match="self-referential"):
            load_basin_package(pkg_dir)


class TestBandsValidation:
    """Finding #8: a present bands.gpkg is strict-parsed and fully validated."""

    def test_well_formed_bands_parse(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _write_bands_gpkg(pkg_dir, [_valid_band_row()])

        loaded = load_basin_package(pkg_dir)

        assert loaded.bands is not None
        assert len(loaded.bands) == 1
        assert loaded.bands[0].band_id == 1

    def test_fractional_band_id_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _write_bands_gpkg(pkg_dir, [_valid_band_row(band_id=1.5)])

        with pytest.raises(BasinPackageRejectedError, match="bands.gpkg"):
            load_basin_package(pkg_dir)

    def test_integral_float_band_id_rejects(self, tmp_path: Path) -> None:
        """A float-typed band_id (`1.0`) is REJECTED, not coerced to `1` — the
        column must be integer-typed (§5), consistent with StrictInt. Proves the
        band_id strictness is real: an integral-float no longer sneaks through."""
        pkg_dir = _copy_fixture(tmp_path)
        _write_bands_gpkg(pkg_dir, [_valid_band_row(band_id=1.0)])

        with pytest.raises(BasinPackageRejectedError, match="bands.gpkg"):
            load_basin_package(pkg_dir)

    def test_nan_band_area_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _write_bands_gpkg(pkg_dir, [_valid_band_row(area_km2=float("nan"))])

        with pytest.raises(BasinPackageRejectedError, match="bands.gpkg"):
            load_basin_package(pkg_dir)

    def test_band_max_not_greater_than_min_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _write_bands_gpkg(
            pkg_dir, [_valid_band_row(min_elevation_m=500.0, max_elevation_m=100.0)]
        )

        with pytest.raises(BasinPackageRejectedError, match="max_elevation_m"):
            load_basin_package(pkg_dir)


class TestGeoPackageLayerAndHru:
    """Finding: §3a — the internal layer/table name MUST start with a letter or
    underscore, and every feature in ONE GeoPackage must carry the SAME
    `gateway_hru_name` (a Gateway HRU IS a single GeoPackage — single-kind)."""

    def test_basins_layer_name_leading_digit_rejects(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        gdf = gpd.read_file(pkg_dir / "basins.gpkg")
        (pkg_dir / "basins.gpkg").unlink()
        # A layer/table name that starts with a digit violates §3a rule 1.
        gdf.to_file(pkg_dir / "basins.gpkg", driver="GPKG", layer="0bad")
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="layer/table name"):
            load_basin_package(pkg_dir)

    def test_basins_multiple_gateway_hru_names_reject(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
            second = gdf.iloc[[0]].copy()
            second["station_code"] = "456"
            second["basin_code"] = "456"
            second["name"] = "g_456"
            second["gauge_id"] = "nepal_456"
            # DIFFERENT gateway_hru_name in the SAME GeoPackage.
            second["gateway_hru_name"] = "nepal_dhm_v2"
            return _concat_gdf(gdf, second)

        _mutate_basins_gpkg(pkg_dir, mutate)
        _recompute_checksums(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="single GeoPackage"):
            load_basin_package(pkg_dir)

    def test_bands_multiple_gateway_hru_names_reject(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _write_bands_gpkg(
            pkg_dir,
            [
                _valid_band_row(
                    band_id=1, name="g_123_band_1", gateway_hru_name="nepal_dhm_v1"
                ),
                _valid_band_row(
                    band_id=2, name="g_123_band_2", gateway_hru_name="nepal_dhm_v2"
                ),
            ],
        )

        with pytest.raises(BasinPackageRejectedError, match="single GeoPackage"):
            load_basin_package(pkg_dir)


# ──────────────────────────────────────────────
# Task 1B — gauge_id join + per-basin acceptance
# ──────────────────────────────────────────────


class TestGaugeIdJoin:
    def test_matched_gauge_id_sets_clean_join(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        loaded = load_basin_package(pkg_dir)
        station_id = StationId(uuid4())

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: station_id
        )

        assert len(report.decisions) == 1
        assert report.decisions[0].outcome == "accepted"

    def test_gauge_id_in_only_one_file_raises(self, tmp_path: Path) -> None:
        pkg_dir = _copy_fixture(tmp_path)
        _mutate_static_parquet(
            pkg_dir,
            lambda df: df.with_columns(pl.lit("nepal_999").alias("gauge_id")),
        )
        _recompute_checksums(pkg_dir)
        loaded = load_basin_package(pkg_dir)

        with pytest.raises(BasinPackageRejectedError, match="gauge_id join"):
            evaluate_basin_acceptance(
                loaded, resolve_station=lambda code, network: StationId(uuid4())
            )


def _basin_record(**overrides: object) -> BasinRecord:
    defaults: dict[str, object] = dict(
        network="dhm",
        station_code="1",
        basin_code="1",
        gateway_hru_name="nepal_dhm_v1",
        name="g_1",
        display_name="disp",
        area_km2=10.0,
        outlet_lon=85.0,
        outlet_lat=27.0,
        delineation_method="method",
        geometry=_VALID_GEOM,
        gauge_id="nepal_1",
        latitude=27.0,
        longitude=85.0,
        regional_basin=None,
        outlet_snap_distance_m=10.0,
    )
    defaults.update(overrides)
    return BasinRecord(**defaults)  # type: ignore[arg-type]


def _report_entry(
    basin: BasinRecord, *, coverage: str = "inside", status: str = "passed"
) -> ValidationReportBasinEntry:
    """A §8-shaped per-basin validation entry whose identity agrees with the
    basin and whose ``checks.coverage_status`` is the coverage SOURCE Task 1B
    reads (finding #7 — never the GeoPackage column)."""
    return ValidationReportBasinEntry(
        network=basin.network,
        basin_code=basin.basin_code,
        station_code=basin.station_code,
        gateway_hru_name=basin.gateway_hru_name,
        name=basin.name,
        status=status,  # type: ignore[arg-type]
        checks={
            "geometry_present": True,
            "geometry_valid": True,
            "crs_epsg_4326": True,
            "geometry_2d": True,
            "area_positive": True,
            "ids_unique": True,
            "static_row_present": True,
            "required_static_features_present": True,
            "outlet_snap_distance_m": 10.0,
            "coverage_status": coverage,
        },
        warnings=(),
        errors=(),
    )


def _manifest(**overrides: object) -> PackageManifest:
    defaults: dict[str, object] = dict(
        contract_version="basin-static-artifact/v1",
        package_id="pkg-1",
        created_at="2026-01-01T00:00:00Z",
        network="dhm",
        crs="EPSG:4326",
        extractor_name="x",
        extractor_version="1",
        source_datasets=(),
        gateway_hru_names=frozenset({"nepal_dhm_v1"}),
        climatology_window=None,
        files={},
        checksums={},
    )
    defaults.update(overrides)
    return PackageManifest(**defaults)  # type: ignore[arg-type]


def _loaded_package(
    basins: tuple[BasinRecord, ...],
    *,
    static_attributes: dict[str, dict[str, float | None]] | None = None,
    feature_catalog: tuple[FeatureCatalogEntry, ...] = (),
    manifest: PackageManifest | None = None,
    coverage_by_basin_code: dict[str, str] | None = None,
) -> LoadedBasinPackage:
    if static_attributes is None:
        static_attributes = {b.gauge_id: {} for b in basins}
    coverage_by_basin_code = coverage_by_basin_code or {}
    entries = tuple(
        _report_entry(b, coverage=coverage_by_basin_code.get(b.basin_code, "inside"))
        for b in basins
    )
    return LoadedBasinPackage(
        manifest=manifest or _manifest(),
        basins=basins,
        bands=None,
        feature_catalog=feature_catalog,
        static_attributes=static_attributes,
        validation_report=ValidationReport(
            passed=len(basins), failed=0, warnings=0, basins=entries
        ),
        computed_checksums={},
    )


class TestPerBasinAcceptance:
    def test_clean_basin_is_accepted(self) -> None:
        basin = _basin_record()
        loaded = _loaded_package((basin,))
        station_id = StationId(uuid4())

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: station_id
        )

        assert report.decisions[0].outcome == "accepted"
        assert report.decisions[0].hold_reasons == ()
        assert report.decisions[0].station_id == station_id

    @pytest.mark.parametrize(
        "geometry",
        [None, Point(0, 0).buffer(0).difference(Point(0, 0).buffer(0)), Point(0, 0)],
        ids=["missing", "empty", "wrong_geom_type"],
    )
    def test_bad_geometry_holds_onboarding(self, geometry: object) -> None:
        basin = _basin_record(geometry=geometry)
        loaded = _loaded_package((basin,))

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        decision = report.decisions[0]
        assert decision.outcome == "onboarding_hold"
        assert any("geometry" in reason for reason in decision.hold_reasons)

    def test_invalid_geometry_holds_onboarding(self) -> None:
        # Self-intersecting bowtie polygon — OGC-invalid.
        bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
        basin = _basin_record(geometry=bowtie)
        loaded = _loaded_package((basin,))

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        decision = report.decisions[0]
        assert decision.outcome == "onboarding_hold"
        assert any("geometry" in reason for reason in decision.hold_reasons)

    @pytest.mark.parametrize("area_km2", [0.0, -5.0])
    def test_non_positive_area_holds_onboarding(self, area_km2: float) -> None:
        basin = _basin_record(area_km2=area_km2)
        loaded = _loaded_package((basin,))

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        decision = report.decisions[0]
        assert decision.outcome == "onboarding_hold"
        assert any("area_km2" in reason for reason in decision.hold_reasons)

    def test_unmatched_station_holds_onboarding(self) -> None:
        basin = _basin_record()
        loaded = _loaded_package((basin,))

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: None
        )

        decision = report.decisions[0]
        assert decision.outcome == "onboarding_hold"
        assert decision.station_id is None
        assert any("unmatched" in reason for reason in decision.hold_reasons)

    def test_station_matched_only_under_different_network_stays_unmatched(
        self,
    ) -> None:
        """Discriminating test: a station registered under a DIFFERENT
        network with the SAME code must NOT be bound — proves
        (network, station_code) matching, not code-alone."""
        basin = _basin_record(network="dhm", station_code="1")
        loaded = _loaded_package((basin,), manifest=_manifest(network="dhm"))
        other_network_station_id = StationId(uuid4())

        def resolve_station(code: str, network: str) -> StationId | None:
            # Only resolvable under "other_network", never "dhm".
            if (code, network) == ("1", "other_network"):
                return other_network_station_id
            return None

        report = evaluate_basin_acceptance(loaded, resolve_station=resolve_station)

        decision = report.decisions[0]
        assert decision.outcome == "onboarding_hold"
        assert decision.station_id is None
        assert any("unmatched" in reason for reason in decision.hold_reasons)

    def test_required_static_feature_missing_holds_when_basin_assigned(self) -> None:
        """Finding #10: a null required-static feature holds the basin ONLY when
        the basin is verifiably assigned (via the `assigned_model_features` seam)
        to a model needing it."""
        basin = _basin_record()
        catalog = (
            FeatureCatalogEntry(
                name="feat1",
                type="float",
                unit=None,
                source_dataset="HydroATLAS",
                aggregation="area_weighted_mean",
                description="d",
                climatology_window=None,
                required_by_models=("some_model",),
            ),
        )
        loaded = _loaded_package(
            (basin,),
            static_attributes={"nepal_1": {"feat1": None}},
            feature_catalog=catalog,
        )

        report = evaluate_basin_acceptance(
            loaded,
            resolve_station=lambda code, network: StationId(uuid4()),
            assigned_model_features=lambda b: frozenset({"feat1"}),
        )

        decision = report.decisions[0]
        assert decision.outcome == "onboarding_hold"
        assert any("feat1" in reason for reason in decision.hold_reasons)

    def test_required_static_missing_but_not_assigned_warns_not_holds(self) -> None:
        """Finding #10: with NO per-station model-assignment source (default
        seam), a catalog-`required_by_models` feature that is null must be a
        VISIBLE per-basin WARNING (accept-with-warning) — NOT an onboarding hold
        (§9/§10)."""
        basin = _basin_record()
        catalog = (
            FeatureCatalogEntry(
                name="feat1",
                type="float",
                unit=None,
                source_dataset="HydroATLAS",
                aggregation="area_weighted_mean",
                description="d",
                climatology_window=None,
                required_by_models=("some_model",),
            ),
        )
        loaded = _loaded_package(
            (basin,),
            static_attributes={"nepal_1": {"feat1": None}},
            feature_catalog=catalog,
        )

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        decision = report.decisions[0]
        assert decision.outcome == "accepted"
        assert decision.hold_reasons == ()
        assert any("feat1" in w for w in decision.warnings)

    def test_required_static_feature_present_is_accepted(self) -> None:
        basin = _basin_record()
        catalog = (
            FeatureCatalogEntry(
                name="feat1",
                type="float",
                unit=None,
                source_dataset="HydroATLAS",
                aggregation="area_weighted_mean",
                description="d",
                climatology_window=None,
                required_by_models=("some_model",),
            ),
        )
        loaded = _loaded_package(
            (basin,),
            static_attributes={"nepal_1": {"feat1": 3.0}},
            feature_catalog=catalog,
        )

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        assert report.decisions[0].outcome == "accepted"

    def test_not_yet_required_missing_feature_does_not_hold(self) -> None:
        """A feature nobody requires (`required_by_models` empty) being null
        is a routine, legitimate state (contract §6.1) — no hold."""
        basin = _basin_record()
        catalog = (
            FeatureCatalogEntry(
                name="feat1",
                type="float",
                unit=None,
                source_dataset="HydroATLAS",
                aggregation="area_weighted_mean",
                description="d",
                climatology_window=None,
                required_by_models=(),
            ),
        )
        loaded = _loaded_package(
            (basin,),
            static_attributes={"nepal_1": {"feat1": None}},
            feature_catalog=catalog,
        )

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        assert report.decisions[0].outcome == "accepted"

    def test_undeclared_gateway_hru_name_holds_onboarding(self) -> None:
        basin = _basin_record(gateway_hru_name="not_declared_anywhere")
        loaded = _loaded_package(
            (basin,), manifest=_manifest(gateway_hru_names=frozenset({"nepal_dhm_v1"}))
        )

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        decision = report.decisions[0]
        assert decision.outcome == "onboarding_hold"
        assert any("gateway_hru_name" in reason for reason in decision.hold_reasons)

    def test_coverage_outside_holds_onboarding(self) -> None:
        basin = _basin_record()
        loaded = _loaded_package(
            (basin,), coverage_by_basin_code={basin.basin_code: "outside"}
        )

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        decision = report.decisions[0]
        assert decision.outcome == "onboarding_hold"
        assert any("coverage" in reason for reason in decision.hold_reasons)

    @pytest.mark.parametrize("coverage_status", ["partial", "unknown"])
    def test_coverage_partial_or_unknown_is_accepted_with_visible_warning(
        self, coverage_status: str
    ) -> None:
        basin = _basin_record()
        loaded = _loaded_package(
            (basin,), coverage_by_basin_code={basin.basin_code: coverage_status}
        )

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        decision = report.decisions[0]
        assert decision.outcome == "accepted"
        assert decision.warnings != ()
        assert any(coverage_status in w for w in decision.warnings)

    def test_coverage_read_from_report_not_gpkg(self) -> None:
        """Finding #7: coverage is sourced from the REQUIRED validation-report
        `checks.coverage_status`, not from the basin record — an `outside`
        report entry holds the basin even though the basin itself carries no
        coverage column any more."""
        basin = _basin_record()
        loaded = _loaded_package(
            (basin,), coverage_by_basin_code={basin.basin_code: "outside"}
        )

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        assert report.decisions[0].outcome == "onboarding_hold"
        assert any("coverage" in r for r in report.decisions[0].hold_reasons)
