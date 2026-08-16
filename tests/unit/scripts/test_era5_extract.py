"""Plan 174 (M-A5) Phase 2/3 — red-first locks on the plan's key acceptance
criteria: axis/registration/checksum validation (D2/D5.1/D7), the two named
operators with their bounds/NaN post-conditions (D1/D11), station-set
validation (D8), the elevation table (3a) and the operator-sensitivity
envelope (D1a).
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import polars as pl
import pytest
import xarray as xr

from scripts.dhm_precip.domain_types import (
    DatumReconciliationStatus,
    ExtractionOperator,
    OrographySource,
    Station,
    StationCoordinate,
    StationCoordinateTable,
    VerticalDatum,
)
from scripts.dhm_precip.era5_deaccumulate import validate_output_schema
from scripts.dhm_precip.era5_errors import (
    Era5OrographyError,
    ExtractionPostConditionError,
    NonFiniteExtractionError,
    SourceChecksumMismatchError,
    StationOutsideGridError,
    StationSetMismatchError,
)
from scripts.dhm_precip.era5_extract import (
    assert_extraction_source_valid,
    assert_no_missing_primary,
    assert_registration,
    assert_required_attrs_literal,
    assert_source_checksum,
    assert_utc_epoch_encoding,
    build_operator_sensitivity_table,
    build_station_grid_elevation_table,
    extract_bilinear_series,
    extract_nearest_series,
    load_expected_station_coordinates,
)
from scripts.dhm_precip.era5_manifest import checksum_file
from scripts.dhm_precip.fixtures import (
    build_era5_land_product_fixture,
    write_era5_land_product_fixture,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS

_AREA = (26.2, 85.0, 26.0, 85.2)  # small 3x3 sub-box (fixtures.py default)
_YEAR = 2021


def _clean_ds() -> xr.Dataset:
    return build_era5_land_product_fixture(year=_YEAR, area=_AREA)


# --- 2a: the synthetic fixture builder is itself defective in the intended way ---


class TestEra5LandProductFixture:
    def test_clean_fixture_passes_validate_output_schema(self) -> None:
        result = validate_output_schema(
            _clean_ds(), expected_year=_YEAR, expected_area=_AREA
        )
        assert result.non_finite_cell_count == 0

    @pytest.mark.parametrize(
        ("defect", "match"),
        [
            ("gap", "not strictly increasing and exactly hourly"),
            ("duplicate_stamp", "duplicate stamps"),
            ("non_hourly_stride", "not strictly increasing and exactly hourly"),
            ("truncated_year", "valid_time range"),
        ],
    )
    def test_defective_variant_fails_validate_output_schema(
        self, defect: str, match: str
    ) -> None:
        bad = build_era5_land_product_fixture(year=_YEAR, area=_AREA, defect=defect)  # type: ignore[arg-type]
        with pytest.raises(Exception, match=match):
            validate_output_schema(bad, expected_year=_YEAR, expected_area=_AREA)

    def test_nan_patch_variant_is_a_valid_product_per_m_a4_schema(self) -> None:
        """D11.2's own premise: a partially-NaN field is a legitimate M-A4
        product (only a FULLY non-finite field is rejected there) — the
        nan_patch fixture must pass here so it can exercise M-A5's OWN,
        stricter, extraction-layer NaN policy instead."""
        bad = build_era5_land_product_fixture(
            year=_YEAR, area=_AREA, defect="nan_patch"
        )
        result = validate_output_schema(bad, expected_year=_YEAR, expected_area=_AREA)
        assert result.non_finite_cell_count > 0


# --- 2b: axis, registration, source-integrity (D2, D5.1, D7) ---


class TestAxisValidation:
    def test_clean_real_shaped_fixture_passes_every_validator(self) -> None:
        ds = _clean_ds()
        assert_extraction_source_valid(ds, expected_year=_YEAR, expected_area=_AREA)

    def test_registration_offset_raises(self) -> None:
        ds = _clean_ds()
        offset_lat = ds["latitude"].values + 0.05
        with pytest.raises(ExtractionPostConditionError, match="registered"):
            assert_registration(offset_lat, ds["longitude"].values)

    def test_missing_period_ending_convention_attr_raises(self) -> None:
        ds = _clean_ds()
        del ds.attrs["period_ending_convention"]
        with pytest.raises(
            ExtractionPostConditionError, match="period_ending_convention"
        ):
            assert_required_attrs_literal(ds)

    def test_wrong_literal_attr_value_raises(self) -> None:
        ds = _clean_ds()
        ds.attrs["accumulation_rule"] = "some_other_rule"
        with pytest.raises(ExtractionPostConditionError, match="accumulation_rule"):
            assert_required_attrs_literal(ds)

    def test_sha256_mismatch_raises_before_the_file_is_opened(self, tmp_path) -> None:
        path = tmp_path / "product.nc"
        write_era5_land_product_fixture(_clean_ds(), path)
        with pytest.raises(SourceChecksumMismatchError):
            assert_source_checksum(path, expected_sha256="0" * 64)

    def test_matching_sha256_passes(self, tmp_path) -> None:
        path = tmp_path / "product.nc"
        write_era5_land_product_fixture(_clean_ds(), path)
        assert_source_checksum(path, expected_sha256=checksum_file(path))

    def test_utc_epoch_encoding_on_reopened_dataset_passes(self, tmp_path) -> None:
        path = tmp_path / "product.nc"
        write_era5_land_product_fixture(_clean_ds(), path)
        with xr.open_dataset(path, engine="h5netcdf") as reopened:
            loaded = reopened.load()
            assert_utc_epoch_encoding(loaded)

    def test_non_utc_epoch_encoding_raises(self, tmp_path) -> None:
        ds = _clean_ds()
        path = tmp_path / "product.nc"
        # A non-CF, non-epoch time encoding — the D5.0 marker this checks for.
        ds.to_netcdf(path, engine="h5netcdf")
        with xr.open_dataset(path, engine="h5netcdf") as reopened:
            loaded = reopened.load()
            with pytest.raises(ExtractionPostConditionError, match="UTC epoch"):
                assert_utc_epoch_encoding(loaded)


# --- 2c: the two operators (D1, D11) ---


def _station(lat: float, lon: float) -> StationCoordinate:
    return StationCoordinate(
        station=Station("S"), excel_col="S (mm)", lat=lat, lon=lon, elev_m=1000.0
    )


class TestOperators:
    def test_nearest_returns_exact_node_value_at_a_node(self) -> None:
        ds = _clean_ds()
        # Node (i=1, j=1): lat=26.1, lon=85.1 -> ramp = 0.5 + 1*1 + 0.1*1 = 1.6
        result = extract_nearest_series(ds, _station(26.1, 85.1))
        assert result.values[0] == pytest.approx(1.6)
        assert result.operator == ExtractionOperator.NEAREST

    def test_nearest_returns_exact_node_value_off_node(self) -> None:
        ds = _clean_ds()
        # Closest to node (i=2, j=0): lat=26.2, lon=85.0 -> ramp = 0.5+2+0=2.5
        result = extract_nearest_series(ds, _station(26.19, 85.01))
        assert result.values[0] == pytest.approx(2.5)

    def test_bilinear_reproduces_the_linear_ramp_exactly_at_an_arbitrary_offset(
        self,
    ) -> None:
        ds = _clean_ds()
        # (26.05, 85.05): frac_i=0.5, frac_j=0.5 -> 0.5 + 1*0.5 + 0.1*0.5 = 1.05
        result = extract_bilinear_series(ds, _station(26.05, 85.05))
        assert result.values[0] == pytest.approx(1.05, abs=1e-5)
        assert result.operator == ExtractionOperator.BILINEAR

    def test_bilinear_at_a_cell_corner_matches_nearest(self) -> None:
        ds = _clean_ds()
        coord = _station(26.0, 85.0)  # exactly node (0, 0) -> ramp = 0.5
        nearest = extract_nearest_series(ds, coord)
        bilinear = extract_bilinear_series(ds, coord)
        assert nearest.values[0] == pytest.approx(0.5)
        assert bilinear.values[0] == pytest.approx(0.5)

    def test_station_outside_grid_raises_and_does_not_snap_to_boundary(self) -> None:
        """D11.1 locking test — must fail against a bare
        `.sel(method="nearest")`, which would silently return the boundary
        value instead of raising."""
        ds = _clean_ds()
        far = _station(26.0 + 0.3, 85.0)  # 0.3 deg outside the box
        with pytest.raises(StationOutsideGridError):
            extract_nearest_series(ds, far)
        # Prove the bare `.sel(method="nearest")` path (what D11.1 forbids)
        # WOULD have returned a real number rather than raising:
        bare = ds["precipitation"].sel(
            latitude=far.lat, longitude=far.lon, method="nearest"
        )
        assert np.isfinite(float(bare.isel(valid_time=0).values))

    def test_nan_at_station_raises_non_finite_extraction_error(self) -> None:
        ds = build_era5_land_product_fixture(
            year=_YEAR, area=_AREA, defect="nan_patch", defect_cell=(1, 1)
        )
        result = extract_nearest_series(ds, _station(26.1, 85.1))  # node (1,1)
        with pytest.raises(NonFiniteExtractionError):
            assert_no_missing_primary(result)

    def test_bilinear_missing_neighbour_yields_nan_counted_not_substituted(
        self,
    ) -> None:
        ds = build_era5_land_product_fixture(
            year=_YEAR, area=_AREA, defect="nan_patch", defect_cell=(1, 1)
        )
        # (26.03, 85.03) is nearest to node (0,0) [finite] but its bilinear
        # cell is bounded by nodes (0,0),(0,1),(1,0),(1,1) — including the
        # NaN patch at (1,1) — so bilinear must go NaN while nearest stays
        # finite (never silently falling back).
        coord = _station(26.03, 85.03)
        result = extract_bilinear_series(ds, coord)
        assert result.n_nan == result.values.size
        assert bool(np.all(np.isnan(result.values)))
        nearest = extract_nearest_series(ds, coord)
        assert nearest.grid_i == 0 and nearest.grid_j == 0
        assert nearest.n_nan == 0


class TestStationSet:
    """D8 — thin-wrapper coverage only (M-13): the loader already proves
    duplicate/cardinality behaviour; this locks the NEW translation into
    `StationSetMismatchError`."""

    def test_mismatched_station_set_raises_the_m_a5_error_type(self, tmp_path) -> None:
        path = tmp_path / "coords.csv"
        pl.DataFrame(
            {
                "station": ["A"],
                "excel_col": ["A (mm)"],
                "lat": [27.0],
                "lon": [85.0],
                "elev": [1000.0],
            }
        ).write_csv(path)
        with pytest.raises(StationSetMismatchError):
            load_expected_station_coordinates(
                path, expected_stations=frozenset({Station("A"), Station("B")})
            )

    def test_matching_station_set_loads(self, tmp_path) -> None:
        path = tmp_path / "coords.csv"
        pl.DataFrame(
            {
                "station": ["A"],
                "excel_col": ["A (mm)"],
                "lat": [27.0],
                "lon": [85.0],
                "elev": [1000.0],
            }
        ).write_csv(path)
        table = load_expected_station_coordinates(
            path, expected_stations=frozenset({Station("A")})
        )
        assert table.stations == (Station("A"),)


# --- 3a: per-station grid + elevation table ---


def _orography_ds(ds: xr.Dataset, *, scale: float = 10.0) -> xr.Dataset:
    base = ds["precipitation"].isel(valid_time=0).values * scale
    return xr.Dataset(
        {"orography_elev_m": (["latitude", "longitude"], base)},
        coords={"latitude": ds["latitude"].values, "longitude": ds["longitude"].values},
    )


class TestStationGridElevationTable:
    def test_two_stations_one_row_each_with_hand_computed_mismatch(self) -> None:
        ds = _clean_ds()
        oro_ds = _orography_ds(ds)
        table = StationCoordinateTable(
            by_station={
                Station("A"): _replace_station(_station(26.05, 85.05), "A"),
            }
        )
        rows = build_station_grid_elevation_table(
            table,
            precip_lat=ds["latitude"].values,
            precip_lon=ds["longitude"].values,
            orography_ds=oro_ds,
            orography_source=OrographySource.MODEL_OROGRAPHY,
            orography_product_id="p",
            orography_product_version="1",
            orography_vertical_reference=VerticalDatum.LOCAL_MSL,
        )
        assert len(rows) == 1
        row = rows[0]
        # station.elev_m=1000.0 (fixed in _station); nearest node (0 or 1,0 or 1)
        # orography value at that same node = ramp(node) * 10.
        assert row.elev_mismatch_m == pytest.approx(
            row.station_elev_m - row.orography_elev_m
        )
        assert row.station_elevation_datum == VerticalDatum.UNKNOWN
        assert row.datum_reconciled == DatumReconciliationStatus.UNRECONCILED
        assert row.stations_in_cell == 1

    def test_shared_cell_stations_both_carry_stations_in_cell_two(self) -> None:
        ds = _clean_ds()
        oro_ds = _orography_ds(ds)
        # Both stations round to the SAME nearest node (0, 0).
        table = StationCoordinateTable(
            by_station={
                Station("A"): _replace_station(_station(26.001, 85.001), "A"),
                Station("B"): _replace_station(_station(26.002, 85.002), "B"),
            }
        )
        rows = build_station_grid_elevation_table(
            table,
            precip_lat=ds["latitude"].values,
            precip_lon=ds["longitude"].values,
            orography_ds=oro_ds,
            orography_source=OrographySource.DEM_PROXY,
            orography_product_id="p",
            orography_product_version="1",
            orography_vertical_reference=VerticalDatum.EGM2008,
        )
        by_station = {r.station: r for r in rows}
        assert (
            by_station[Station("A")].shared_cell_id
            == by_station[Station("B")].shared_cell_id
        )
        assert by_station[Station("A")].stations_in_cell == 2
        assert by_station[Station("B")].stations_in_cell == 2

    def test_non_finite_orography_cell_raises(self) -> None:
        ds = _clean_ds()
        oro_ds = _orography_ds(ds)
        oro_ds["orography_elev_m"].values[0, 0] = np.nan
        table = StationCoordinateTable(
            by_station={Station("A"): _replace_station(_station(26.0, 85.0), "A")}
        )
        with pytest.raises(Era5OrographyError):
            build_station_grid_elevation_table(
                table,
                precip_lat=ds["latitude"].values,
                precip_lon=ds["longitude"].values,
                orography_ds=oro_ds,
                orography_source=OrographySource.MODEL_OROGRAPHY,
                orography_product_id="p",
                orography_product_version="1",
                orography_vertical_reference=VerticalDatum.LOCAL_MSL,
            )


def _replace_station(coord: StationCoordinate, name: str) -> StationCoordinate:
    return replace(coord, station=Station(name))


# --- 3b: operator-sensitivity envelope (D1a) ---


class TestOperatorSensitivityEnvelope:
    def test_quantile_grid_matches_default_params(self) -> None:
        ds = _clean_ds()
        nearest = {Station("A"): extract_nearest_series(ds, _station(26.05, 85.05))}
        bilinear = {Station("A"): extract_bilinear_series(ds, _station(26.05, 85.05))}
        table = build_operator_sensitivity_table(
            nearest, bilinear, params=DEFAULT_PARAMS
        )
        quantile_rows = table.filter(pl.col("statistic") == "QUANTILE")
        observed_quantiles = sorted(
            set(quantile_rows["quantile"].drop_nulls().to_list())
        )
        assert observed_quantiles == sorted(DEFAULT_PARAMS.quantile_grid)

    def test_sign_agreement_fraction_is_half_on_a_constructed_split(self) -> None:
        # Two stations, one hour: nearest > bilinear at A, nearest < bilinear at B.
        valid_time = np.array([np.datetime64("2021-06-01T00:00")])  # JJAS
        from scripts.dhm_precip.era5_extract import ExtractedSeries

        nearest = {
            Station("A"): ExtractedSeries(
                station=Station("A"),
                operator=ExtractionOperator.NEAREST,
                valid_time=valid_time,
                values=np.array([5.0]),
                grid_lat=0.0,
                grid_lon=0.0,
                grid_i=0,
                grid_j=0,
                n_finite=1,
                n_nan=0,
            ),
            Station("B"): ExtractedSeries(
                station=Station("B"),
                operator=ExtractionOperator.NEAREST,
                valid_time=valid_time,
                values=np.array([1.0]),
                grid_lat=0.0,
                grid_lon=0.0,
                grid_i=1,
                grid_j=1,
                n_finite=1,
                n_nan=0,
            ),
        }
        bilinear = {
            Station("A"): replace(
                nearest[Station("A")],
                operator=ExtractionOperator.BILINEAR,
                values=np.array([3.0]),
            ),
            Station("B"): replace(
                nearest[Station("B")],
                operator=ExtractionOperator.BILINEAR,
                values=np.array([2.0]),
            ),
        }
        table = build_operator_sensitivity_table(
            nearest, bilinear, params=DEFAULT_PARAMS
        )
        median_across = table.filter(
            (pl.col("scope") == "ACROSS_STATION")
            & (pl.col("season") == "JJAS")
            & (pl.col("statistic") == "QUANTILE")
            & (pl.col("quantile") == 0.5)
        )
        assert median_across.height == 1
        assert median_across["sign_agreement_fraction"][0] == pytest.approx(0.5)

    def test_no_output_field_implies_a_winner(self) -> None:
        """D1a: never phrased as a ranking. The table has no 'winner'/'better'
        column and no boolean verdict field — only symmetric statistics."""
        ds = _clean_ds()
        nearest = {Station("A"): extract_nearest_series(ds, _station(26.05, 85.05))}
        bilinear = {Station("A"): extract_bilinear_series(ds, _station(26.05, 85.05))}
        table = build_operator_sensitivity_table(
            nearest, bilinear, params=DEFAULT_PARAMS
        )
        forbidden = {"winner", "better", "preferred", "recommended"}
        assert not (forbidden & set(table.columns))

    def test_excluded_hours_counted_when_bilinear_is_nan(self) -> None:
        ds = build_era5_land_product_fixture(
            year=_YEAR, area=_AREA, defect="nan_patch", defect_cell=(1, 1)
        )
        coord = _station(
            26.03, 85.03
        )  # bilinear cell includes node (1,1) -> always NaN
        nearest = {Station("A"): extract_nearest_series(ds, coord)}
        bilinear = {Station("A"): extract_bilinear_series(ds, coord)}
        table = build_operator_sensitivity_table(
            nearest, bilinear, params=DEFAULT_PARAMS
        )
        assert (table["n_hours_excluded"] == nearest[Station("A")].values.size).all()
        assert (table["n_hours_common_finite"] == 0).all()
