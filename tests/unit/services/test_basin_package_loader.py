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
        pkg_dir = _copy_fixture(tmp_path)

        def mutate(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
            second = gdf.iloc[[0]].copy()
            second["station_code"] = "456"
            second["basin_code"] = "456"
            second["gauge_id"] = "nepal_456"
            # SAME `name` as the first row -> a package-level ID collision.
            return _concat_gdf(gdf, second)

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
        coverage_status="inside",
    )
    defaults.update(overrides)
    return BasinRecord(**defaults)  # type: ignore[arg-type]


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
) -> LoadedBasinPackage:
    if static_attributes is None:
        static_attributes = {b.gauge_id: {} for b in basins}
    return LoadedBasinPackage(
        manifest=manifest or _manifest(),
        basins=basins,
        bands=None,
        feature_catalog=feature_catalog,
        static_attributes=static_attributes,
        validation_report=ValidationReport(passed=len(basins), failed=0, warnings=0),
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

    def test_required_static_feature_missing_holds_onboarding(self) -> None:
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
        assert decision.outcome == "onboarding_hold"
        assert any("feat1" in reason for reason in decision.hold_reasons)

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
        basin = _basin_record(coverage_status="outside")
        loaded = _loaded_package((basin,))

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
        basin = _basin_record(coverage_status=coverage_status)
        loaded = _loaded_package((basin,))

        report = evaluate_basin_acceptance(
            loaded, resolve_station=lambda code, network: StationId(uuid4())
        )

        decision = report.decisions[0]
        assert decision.outcome == "accepted"
        assert decision.warnings != ()
        assert any(coverage_status in w for w in decision.warnings)
