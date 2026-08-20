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
    ExtractedSeries,
    assert_expected_station_cardinality,
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
from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams

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

    @pytest.mark.parametrize("value", [np.inf, -np.inf])
    def test_infinite_value_at_a_station_is_gated_as_non_finite(
        self, value: float
    ) -> None:
        """MAJOR (2026-08-17) — gating used to check only `np.isnan`, so an
        isolated `+inf`/`-inf` (not `np.isnan`) silently passed D11.2's
        primary non-finite gate. `np.isnan(np.inf)` is `False`, confirmed
        empirically."""
        ds = _clean_ds()
        ds["precipitation"].values[:, 1, 1] = value
        result = extract_nearest_series(ds, _station(26.1, 85.1))  # node (1,1)
        assert result.n_nan == 0
        assert result.n_inf == result.values.size
        with pytest.raises(NonFiniteExtractionError):
            assert_no_missing_primary(result)

    @pytest.mark.parametrize("value", [np.inf, -np.inf])
    def test_infinite_neighbour_is_counted_not_silently_finite_for_bilinear(
        self, value: float
    ) -> None:
        """MAJOR (2026-08-17) — linear interpolation of an infinite
        contributing neighbour propagates +-inf, not NaN (confirmed
        empirically), so counting only `np.isnan` under-counted D11.3's
        missing-neighbour accounting."""
        ds = _clean_ds()
        ds["precipitation"].values[:, 1, 1] = value
        coord = _station(26.03, 85.03)  # bilinear cell includes node (1,1)
        result = extract_bilinear_series(ds, coord)
        assert result.n_nan == 0
        assert result.n_inf == result.values.size
        assert bool(np.all(~np.isfinite(result.values)))


class TestVariableParameterisation:
    """Plan 191 T4 — `extract_nearest_series` reads `ds[variable]`, defaulting
    to `"precipitation"` so the existing precipitation callers (above) are
    byte-for-byte unaffected. D8 seam test: prove the parameter is actually
    THREADED THROUGH (a real different read site), not a name that merely
    happens to coincide with the default."""

    def test_default_variable_is_precipitation(self) -> None:
        ds = _clean_ds()
        default_result = extract_nearest_series(ds, _station(26.1, 85.1))
        explicit_result = extract_nearest_series(
            ds, _station(26.1, 85.1), variable="precipitation"
        )
        assert default_result.values[0] == pytest.approx(explicit_result.values[0])

    def test_explicit_variable_reads_a_different_data_variable(self) -> None:
        ds = _clean_ds().rename({"precipitation": "temperature"})
        ds["temperature"].values[:] = 42.0
        # The default ("precipitation") must fail on this dataset — proof
        # the parameter genuinely selects the read site, not a fallback.
        with pytest.raises(KeyError):
            extract_nearest_series(ds, _station(26.1, 85.1))
        result = extract_nearest_series(
            ds, _station(26.1, 85.1), variable="temperature"
        )
        assert result.values[0] == pytest.approx(42.0)


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


class TestExpectedStationCardinalityTripwire:
    """D8/2d (CORRECTED 2026-08-16, blocker) — the single-inventory decision
    removed the only thing pinning the COUNT. The loader checks the extracted
    set *equals* the inventory, and an inventory of 25 satisfies that
    perfectly: a workbook that silently yields 25 stations extracts 25 and
    publishes happily. Equality to a self-supplied list is not a constraint,
    so the count is pinned independently, on the frozen parameter object, as
    a tripwire on the boundary input."""

    def test_the_pinned_count_is_twenty_six(self) -> None:
        assert DEFAULT_PARAMS.expected_station_count == 26

    @pytest.mark.parametrize("n", [25, 27])
    def test_an_inventory_of_the_wrong_size_raises_naming_the_count(
        self, n: int
    ) -> None:
        inventory = frozenset(Station(f"S{i}") for i in range(n))
        with pytest.raises(StationSetMismatchError, match=str(n)):
            assert_expected_station_cardinality(
                inventory, expected_count=DEFAULT_PARAMS.expected_station_count
            )

    def test_an_inventory_of_exactly_the_pinned_size_passes(self) -> None:
        inventory = frozenset(Station(f"S{i}") for i in range(26))
        assert_expected_station_cardinality(
            inventory, expected_count=DEFAULT_PARAMS.expected_station_count
        )


# --- 3a: per-station grid + elevation table ---


def _orography_ds(ds: xr.Dataset, *, scale: float = 10.0) -> xr.Dataset:
    base = ds["precipitation"].isel(valid_time=0).values * scale
    return xr.Dataset(
        {"orography_elev_m": (["latitude", "longitude"], base)},
        coords={"latitude": ds["latitude"].values, "longitude": ds["longitude"].values},
    )


def _independent_haversine_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Written out here on purpose: `offset_km` must be checked against an
    INDEPENDENT computation, not against the implementation's own helper.
    WGS84 spherical radius, as the plan's "Measured facts" table states."""
    import math

    radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


class TestStationGridElevationTable:
    def test_row_matches_an_independently_derived_cell_value_and_offset(self) -> None:
        """MINOR (2026-08-16) — the previous version of this test asserted
        `elev_mismatch_m == row.station_elev_m - row.orography_elev_m`,
        recomputing the implementation's own formula from its own output. It
        passed against ANY wrong orography cell, because both sides came
        from the same (possibly wrong) lookup.

        Everything expected below is derived here, from the fixture's
        declared analytic surface and the station's coordinate, without
        reading a single field the implementation produced:

        * the 3x3 fixture grid is lat [26.0, 26.1, 26.2] x lon
          [85.0, 85.1, 85.2] (`build_era5_land_product_fixture`'s default
          area at 0.1 deg spacing);
        * its field is `0.5 + 1.0*i + 0.1*j`, and `_orography_ds` scales it
          by 10;
        * the station at (26.13, 85.17) is nearest node (i=1, j=2) —
          |26.13-26.1| = 0.03 against 0.07/0.13 for the other rows, and
          |85.17-85.2| = 0.03 against 0.07/0.17 for the other columns, so
          there is no tie to break;
        * therefore orography = (0.5 + 1 + 0.2) * 10 = 17.0 m exactly, and
          the mismatch against the fixture's 1000.0 m station elevation is
          983.0 m.
        """
        ds = _clean_ds()
        oro_ds = _orography_ds(ds)
        station_lat, station_lon = 26.13, 85.17
        expected_grid_lat, expected_grid_lon = 26.1, 85.2
        expected_i, expected_j = 1, 2
        expected_orography_m = (0.5 + 1.0 * expected_i + 0.1 * expected_j) * 10.0
        expected_mismatch_m = 1000.0 - expected_orography_m
        expected_offset_km = _independent_haversine_km(
            station_lat, station_lon, expected_grid_lat, expected_grid_lon
        )

        table = StationCoordinateTable(
            by_station={
                Station("A"): _replace_station(_station(station_lat, station_lon), "A"),
            }
        )
        rows = build_station_grid_elevation_table(
            table,
            nearest_by_station=_nearest_by_station(ds, table),
            orography_ds=oro_ds,
            orography_source=OrographySource.MODEL_OROGRAPHY,
            orography_product_id="p",
            orography_product_version="1",
            orography_vertical_reference=VerticalDatum.LOCAL_MSL,
        )
        assert len(rows) == 1
        row = rows[0]
        assert (row.grid_i, row.grid_j) == (expected_i, expected_j)
        assert row.grid_lat == pytest.approx(expected_grid_lat)
        assert row.grid_lon == pytest.approx(expected_grid_lon)
        assert row.orography_elev_m == pytest.approx(expected_orography_m, abs=1e-6)
        assert row.elev_mismatch_m == pytest.approx(expected_mismatch_m, abs=1e-9)
        # 3a's stated tolerance: `offset_km` matches an independent haversine
        # to 1 m.
        assert row.offset_km == pytest.approx(expected_offset_km, abs=0.001)
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
            nearest_by_station=_nearest_by_station(ds, table),
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
                nearest_by_station=_nearest_by_station(ds, table),
                orography_ds=oro_ds,
                orography_source=OrographySource.MODEL_OROGRAPHY,
                orography_product_id="p",
                orography_product_version="1",
                orography_vertical_reference=VerticalDatum.LOCAL_MSL,
            )


def _replace_station(coord: StationCoordinate, name: str) -> StationCoordinate:
    return replace(coord, station=Station(name))


def _nearest_by_station(
    ds: xr.Dataset, table: StationCoordinateTable
) -> dict[Station, ExtractedSeries]:
    return {
        station: extract_nearest_series(ds, coord)
        for station, coord in table.by_station.items()
    }


class TestElevationTableAgreesWithTheSeriesOnAnExactMidpoint:
    """MAJOR (2026-08-17) — `build_station_grid_elevation_table` used to
    recompute the nearest grid cell independently via
    `np.argmin(np.abs(precip_lat - coord.lat))`, a SEPARATE tie-break rule
    from the one `extract_nearest_series` uses (xarray's own
    `.sel(method="nearest")`). On an exact midpoint the two disagree: on the
    fixture's [26.0, 26.1] axis, 26.05 is equidistant from both nodes —
    `np.argmin` picks the LOWER index (26.0), xarray's nearest picks the
    UPPER one (26.1) — confirmed empirically before writing this test. The
    elevation row could then reference a different cell than the station's
    own extracted series."""

    def test_series_and_elevation_row_reference_the_same_node(self) -> None:
        ds = _clean_ds()
        oro_ds = _orography_ds(ds)
        # An exact midpoint on the lat axis [26.0, 26.1, 26.2]; the lon
        # coordinate 85.0 lines up exactly with a node, so only the lat
        # tie-break is exercised.
        coord = _station(26.05, 85.0)
        table = StationCoordinateTable(
            by_station={Station("A"): _replace_station(coord, "A")}
        )
        nearest = _nearest_by_station(ds, table)
        rows = build_station_grid_elevation_table(
            table,
            nearest_by_station=nearest,
            orography_ds=oro_ds,
            orography_source=OrographySource.MODEL_OROGRAPHY,
            orography_product_id="p",
            orography_product_version="1",
            orography_vertical_reference=VerticalDatum.LOCAL_MSL,
        )
        row = rows[0]
        series = nearest[Station("A")]
        assert (row.grid_i, row.grid_j) == (series.grid_i, series.grid_j)
        assert row.grid_lat == pytest.approx(series.grid_lat)
        assert row.grid_lon == pytest.approx(series.grid_lon)
        assert row.shared_cell_id == f"{series.grid_i}_{series.grid_j}"


# --- 3b: operator-sensitivity envelope (D1a) ---


def _series(
    station: Station,
    operator: ExtractionOperator,
    valid_time: np.ndarray,
    values: np.ndarray,
) -> ExtractedSeries:
    return ExtractedSeries(
        station=station,
        operator=operator,
        valid_time=valid_time,
        values=values,
        grid_lat=0.0,
        grid_lon=0.0,
        grid_i=0,
        grid_j=0,
        n_finite=int(np.isfinite(values).sum()),
        n_nan=int(np.isnan(values).sum()),
    )


def _two_station_table() -> pl.DataFrame:
    """Two JJAS hours at two stations, all values above the 0.2 mm/h wet
    threshold, constructed so the per-station sign of (nearest - bilinear)
    SPLITS at the median and AGREES at q0.99:

      A  nearest [1, 10]  bilinear [2.0, 5.0]  -> med +2.0   q0.99 +4.94
      B  nearest [1, 10]  bilinear [5.5, 6.5]  -> med -0.5   q0.99 +3.42
    """
    valid_time = np.array(
        [np.datetime64("2021-06-01T00:00"), np.datetime64("2021-06-01T01:00")]
    )
    nearest = {
        s: _series(s, ExtractionOperator.NEAREST, valid_time, np.array([1.0, 10.0]))
        for s in (Station("A"), Station("B"))
    }
    bilinear = {
        Station("A"): _series(
            Station("A"),
            ExtractionOperator.BILINEAR,
            valid_time,
            np.array([2.0, 5.0]),
        ),
        Station("B"): _series(
            Station("B"),
            ExtractionOperator.BILINEAR,
            valid_time,
            np.array([5.5, 6.5]),
        ),
    }
    return build_operator_sensitivity_table(nearest, bilinear, params=DEFAULT_PARAMS)


def _hours(n: int) -> np.ndarray:
    """`n` consecutive JJAS hours from 2021-06-01T00 (season is fixed so the
    season grain is not what any assertion below turns on)."""
    return np.array(
        [np.datetime64("2021-06-01T00:00") + np.timedelta64(h, "h") for h in range(n)]
    )


def _sensitivity_of(
    values: dict[str, tuple[list[float], list[float]]],
    *,
    params: DhmPrecipParams = DEFAULT_PARAMS,
) -> pl.DataFrame:
    """station -> (nearest values, bilinear values), one series per operator."""
    nearest = {
        Station(name): _series(
            Station(name),
            ExtractionOperator.NEAREST,
            _hours(len(near)),
            np.array(near),
        )
        for name, (near, _) in values.items()
    }
    bilinear = {
        Station(name): _series(
            Station(name),
            ExtractionOperator.BILINEAR,
            _hours(len(bil)),
            np.array(bil),
        )
        for name, (_, bil) in values.items()
    }
    return build_operator_sensitivity_table(nearest, bilinear, params=params)


def _row(
    table: pl.DataFrame, *, statistic: str, station: str | None, quantile: float | None
) -> dict[str, object]:
    frame = table.filter(
        (pl.col("season") == "JJAS")
        & (pl.col("statistic") == statistic)
        & (pl.col("scope") == ("ACROSS_STATION" if station is None else "STATION"))
    )
    frame = (
        frame.filter(pl.col("station").is_null())
        if station is None
        else frame.filter(pl.col("station") == station)
    )
    frame = (
        frame.filter(pl.col("quantile").is_null())
        if quantile is None
        else frame.filter(pl.col("quantile") == quantile)
    )
    assert frame.height == 1, f"expected exactly one row, got {frame.height}"
    return frame.row(0, named=True)


class TestSensitivityValuesAgainstHandComputedArithmetic:
    """D1a — the NUMBERS, not the plumbing. Every assertion below is stated
    as the arithmetic it should be, never as a value copied out of an
    implementation run, because M-A6 reads these four columns directly: a
    reversed subtraction (`bilinear - nearest`), an inverted ratio
    (`bilinear / nearest`), a wet mean taken over the wrong population or a
    `delta_unit` mislabelled on the frequency row would all have passed the
    previous test set, which asserted only populations, counts and sign
    agreement.

    Fixture (five JJAS hours at station A, two at station B; wet threshold
    `>= 0.2 mm/h`, `zero_policy="exclude_zero"`, `quantile_definition=
    "linear"`, all from `DEFAULT_PARAMS`):

        A  nearest  [1.0, 2.0, 4.0, 8.0, 0.0]   wet -> 1, 2, 4, 8   (4 hours)
        A  bilinear [0.5, 1.5, 4.0, 0.1, 0.0]   wet -> 0.5, 1.5, 4  (3 hours)
        B  nearest  [1.0, 2.0]                  wet -> 1, 2
        B  bilinear [3.0, 4.0]                  wet -> 3, 4

    The two operators' wet populations differ in SIZE at A (4 vs 3), and the
    per-station delta signs OPPOSE each other (A positive, B negative), so
    every direction-sensitive column is pinned by a value that changes if the
    direction flips.
    """

    A_NEAREST = [1.0, 2.0, 4.0, 8.0, 0.0]
    A_BILINEAR = [0.5, 1.5, 4.0, 0.1, 0.0]
    B_NEAREST = [1.0, 2.0]
    B_BILINEAR = [3.0, 4.0]

    def _table(self) -> pl.DataFrame:
        return _sensitivity_of(
            {
                "A": (self.A_NEAREST, self.A_BILINEAR),
                "B": (self.B_NEAREST, self.B_BILINEAR),
            }
        )

    def test_median_row_values_delta_and_ratio(self) -> None:
        # linear interpolation at q=0.5 over the WET population:
        #   nearest  [1, 2, 4, 8] -> midpoint of 2 and 4
        #   bilinear [0.5, 1.5, 4] -> the middle order statistic, 1.5
        expected_nearest = (2.0 + 4.0) / 2
        expected_bilinear = 1.5
        row = _row(self._table(), statistic="QUANTILE", station="A", quantile=0.5)
        assert row["nearest_value"] == pytest.approx(expected_nearest)
        assert row["bilinear_value"] == pytest.approx(expected_bilinear)
        # DIRECTION: nearest - bilinear, positive here; the reverse is -1.5.
        assert row["delta_absolute"] == pytest.approx(
            expected_nearest - expected_bilinear
        )
        assert row["delta_absolute"] > 0.0
        # DIRECTION: nearest / bilinear = 2.0; the inverse is 0.5.
        assert row["ratio"] == pytest.approx(expected_nearest / expected_bilinear)
        assert row["ratio"] > 1.0
        assert row["delta_unit"] == "MM_PER_H"
        # The two wet populations DIFFER in size, so a row that silently
        # shared one population would be visible here.
        assert row["n_wet_nearest"] == 4
        assert row["n_wet_bilinear"] == 3
        assert row["n_hours_common_finite"] == 5
        assert row["n_hours_excluded"] == 0

    def test_wet_mean_intensity_row_values_delta_and_ratio(self) -> None:
        expected_nearest = (1.0 + 2.0 + 4.0 + 8.0) / 4
        expected_bilinear = (0.5 + 1.5 + 4.0) / 3
        row = _row(
            self._table(),
            statistic="WET_MEAN_INTENSITY",
            station="A",
            quantile=None,
        )
        assert row["nearest_value"] == pytest.approx(expected_nearest)  # 3.75
        assert row["bilinear_value"] == pytest.approx(expected_bilinear)  # 2.0
        assert row["delta_absolute"] == pytest.approx(
            expected_nearest - expected_bilinear
        )
        assert row["delta_absolute"] > 0.0
        assert row["ratio"] == pytest.approx(expected_nearest / expected_bilinear)
        assert row["ratio"] > 1.0
        assert row["delta_unit"] == "MM_PER_H"
        assert row["n_wet_nearest"] == 4
        assert row["n_wet_bilinear"] == 3
        # Distinct from the median row above (3.0 / 1.5) — a swapped
        # statistic would otherwise be invisible.
        assert row["nearest_value"] != pytest.approx((2.0 + 4.0) / 2)

    def test_wet_frequency_row_values_delta_ratio_and_unit(self) -> None:
        expected_nearest = 4 / 5
        expected_bilinear = 3 / 5
        row = _row(self._table(), statistic="WET_FREQUENCY", station="A", quantile=None)
        assert row["nearest_value"] == pytest.approx(expected_nearest)  # 0.8
        assert row["bilinear_value"] == pytest.approx(expected_bilinear)  # 0.6
        assert row["delta_absolute"] == pytest.approx(
            expected_nearest - expected_bilinear
        )
        assert row["delta_absolute"] > 0.0
        assert row["ratio"] == pytest.approx(expected_nearest / expected_bilinear)
        assert row["ratio"] > 1.0
        # A FRACTION, never mm/h — the one row whose unit differs, and the
        # one a copy-paste of the wet-mean row would get wrong.
        assert row["delta_unit"] == "FRACTION"

    def test_the_opposing_station_carries_the_negative_delta(self) -> None:
        """The sign is a property of the DATA, not of the column: station B's
        bilinear exceeds its nearest, so its delta must be negative and its
        ratio below 1. Together with station A this pins the direction from
        both sides."""
        row = _row(self._table(), statistic="QUANTILE", station="B", quantile=0.5)
        expected_nearest = (1.0 + 2.0) / 2
        expected_bilinear = (3.0 + 4.0) / 2
        assert row["nearest_value"] == pytest.approx(expected_nearest)  # 1.5
        assert row["bilinear_value"] == pytest.approx(expected_bilinear)  # 3.5
        assert row["delta_absolute"] == pytest.approx(
            expected_nearest - expected_bilinear
        )
        assert row["delta_absolute"] < 0.0
        assert row["ratio"] == pytest.approx(expected_nearest / expected_bilinear)
        assert row["ratio"] < 1.0

    def test_across_station_sign_agreement_is_half_on_the_opposing_pair(self) -> None:
        """A's median delta is positive and B's is negative, so exactly one of
        the two stations carries the majority sign: 1/2."""
        row = _row(self._table(), statistic="QUANTILE", station=None, quantile=0.5)
        assert row["sign_agreement_fraction"] == pytest.approx(1 / 2)
        assert row["n_hours_common_finite"] == 5 + 2

    def test_ratio_is_null_on_a_zero_denominator_but_the_delta_is_not(self) -> None:
        """A zero bilinear wet FREQUENCY is a real, reportable value (0.0),
        so the delta stays a number while the ratio must be null rather than
        an infinity or a silently substituted 1.0. The wet MEAN over an empty
        population is a different null: there is no value to divide at all."""
        table = _sensitivity_of({"Z": ([1.0, 2.0], [0.0, 0.1])})
        freq = _row(table, statistic="WET_FREQUENCY", station="Z", quantile=None)
        assert freq["nearest_value"] == pytest.approx(2 / 2)
        assert freq["bilinear_value"] == pytest.approx(0 / 2)
        assert freq["delta_absolute"] == pytest.approx(1.0 - 0.0)
        assert freq["ratio"] is None
        assert freq["n_wet_nearest"] == 2
        assert freq["n_wet_bilinear"] == 0

        mean = _row(table, statistic="WET_MEAN_INTENSITY", station="Z", quantile=None)
        assert mean["nearest_value"] == pytest.approx((1.0 + 2.0) / 2)
        assert mean["bilinear_value"] is None
        assert mean["delta_absolute"] is None
        assert mean["ratio"] is None


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

    def test_quantiles_are_computed_on_the_wet_hour_population(self) -> None:
        """D1a (CORRECTED 2026-08-16) — `zero_policy="exclude_zero"` was
        pinned AND hashed into `extraction_identity` and then never applied:
        quantiles ran over all common-finite hours while the manifest
        asserted the wet-hour policy. That is false provenance, and it makes
        the numbers incomparable with every other quantile in this track
        (all eight M-A1 intensity expectations are stated over JJAS wet-hour
        >= 0.2 mm/h non-null observations).

        This asserts the POPULATION, not merely that `quantile_grid` was
        used: with five dry hours and the wet hours 1..5, the wet-hour median
        is 3.0 while the all-hours median is 0.5.
        """
        n = 10
        valid_time = np.array(
            [
                np.datetime64("2021-06-01T00:00") + np.timedelta64(h, "h")
                for h in range(n)
            ]
        )
        values = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        nearest = {
            Station("A"): _series(
                Station("A"), ExtractionOperator.NEAREST, valid_time, values
            )
        }
        bilinear = {
            Station("A"): _series(
                Station("A"), ExtractionOperator.BILINEAR, valid_time, values
            )
        }
        table = build_operator_sensitivity_table(
            nearest, bilinear, params=DEFAULT_PARAMS
        )
        median = table.filter(
            (pl.col("scope") == "STATION")
            & (pl.col("season") == "JJAS")
            & (pl.col("statistic") == "QUANTILE")
            & (pl.col("quantile") == 0.5)
        )
        assert median.height == 1
        all_hours_median = float(np.quantile(values, 0.5))
        wet_hours_median = float(np.quantile(values[values >= 0.2], 0.5))
        assert all_hours_median != wet_hours_median  # the test can distinguish
        assert median["nearest_value"][0] == pytest.approx(wet_hours_median)
        assert median["bilinear_value"][0] == pytest.approx(wet_hours_median)
        # And the wet-hour counts on the row say which population it was.
        assert median["n_wet_nearest"][0] == 5
        assert median["n_hours_common_finite"][0] == n

    def test_zero_policy_actually_changes_the_computed_quantile(self) -> None:
        """BLOCKER/P7 (2026-08-17) — `zero_policy` was pinned, hashed into
        `extraction_identity`, and NEVER READ: the wet-hour filter ran
        unconditionally, so `zero_policy="include_zero"` produced
        byte-identical output to `"exclude_zero"` — false provenance, per
        P7's own rule that an identity may hash only inputs that are
        actually read.

        P7's standing obligation: a test must change the INPUT and change
        an OUTPUT, not merely change the identity (`test_extract_era5_cli.py`
        already proves the identity changes; this proves the identity
        change reflects a REAL computation difference). Same fixture as the
        wet-hour-population test above: `exclude_zero` must reproduce that
        test's wet-hour median (3.0); `include_zero` must instead reproduce
        the all-hours median (0.5) — the two must differ."""
        n = 10
        valid_time = np.array(
            [
                np.datetime64("2021-06-01T00:00") + np.timedelta64(h, "h")
                for h in range(n)
            ]
        )
        values = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        nearest = {
            Station("A"): _series(
                Station("A"), ExtractionOperator.NEAREST, valid_time, values
            )
        }
        bilinear = {
            Station("A"): _series(
                Station("A"), ExtractionOperator.BILINEAR, valid_time, values
            )
        }

        def _median(params: DhmPrecipParams) -> float:
            table = build_operator_sensitivity_table(nearest, bilinear, params=params)
            row = table.filter(
                (pl.col("scope") == "STATION")
                & (pl.col("season") == "JJAS")
                & (pl.col("statistic") == "QUANTILE")
                & (pl.col("quantile") == 0.5)
            )
            assert row.height == 1
            return float(row["nearest_value"][0])

        exclude_zero_median = _median(DEFAULT_PARAMS)
        include_zero_params = replace(DEFAULT_PARAMS, zero_policy="include_zero")
        include_zero_median = _median(include_zero_params)

        all_hours_median = float(np.quantile(values, 0.5))
        wet_hours_median = float(np.quantile(values[values >= 0.2], 0.5))
        assert exclude_zero_median == pytest.approx(wet_hours_median)
        assert include_zero_median == pytest.approx(all_hours_median)
        assert exclude_zero_median != include_zero_median

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

    def test_sign_agreement_is_null_when_every_station_delta_ties(self) -> None:
        """MINOR (2026-08-17 review) — an EXACT tie (nearest == bilinear,
        delta == 0.0) used to be counted as a POSITIVE sign, so an
        all-tied population reported `sign_agreement_fraction=1.0`,
        misleadingly implying systematic ordering where there is none.
        Both stations tie exactly here; the correct report is `None` (no
        direction), never a spurious 1.0."""
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
                values=np.array([5.0]),
                grid_lat=0.0,
                grid_lon=0.0,
                grid_i=1,
                grid_j=1,
                n_finite=1,
                n_nan=0,
            ),
        }
        # bilinear == nearest for BOTH stations: every delta is EXACTLY 0.
        bilinear = {
            Station("A"): replace(
                nearest[Station("A")], operator=ExtractionOperator.BILINEAR
            ),
            Station("B"): replace(
                nearest[Station("B")], operator=ExtractionOperator.BILINEAR
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
        assert median_across["sign_agreement_fraction"][0] is None

    def test_sign_agreement_excludes_a_tie_from_the_majority_vote(self) -> None:
        """A tie must not be silently counted toward EITHER sign: with one
        station strictly positive and one exactly tied, the majority sign
        (positive, the only non-tied vote) is what one non-tied vote out of
        two total stations means — 0.5, not 1.0 (which the OLD "tie counts
        as positive" bug would have reported, since both the tie and the
        genuine positive would have counted as +1)."""
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
                values=np.array([5.0]),
                grid_lat=0.0,
                grid_lon=0.0,
                grid_i=1,
                grid_j=1,
                n_finite=1,
                n_nan=0,
            ),
        }
        bilinear = {
            # A: nearest(5.0) > bilinear(3.0) -> strictly positive delta.
            Station("A"): replace(
                nearest[Station("A")],
                operator=ExtractionOperator.BILINEAR,
                values=np.array([3.0]),
            ),
            # B: nearest(5.0) == bilinear(5.0) -> an EXACT tie.
            Station("B"): replace(
                nearest[Station("B")], operator=ExtractionOperator.BILINEAR
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

    def test_sign_agreement_is_populated_for_every_across_station_quantile_row(
        self,
    ) -> None:
        """D1a (CORRECTED 2026-08-16) — sign agreement was produced only at a
        hard-coded q=0.5, leaving it null for the other seven quantiles. The
        one column that reveals whether an ordering is systematic was
        absent everywhere it matters: D1's bilinear-damps-the-tail question
        lives at q0.99/q0.999, not the median. Restricted to QUANTILE rows
        here — `_two_station_table`'s WET_FREQUENCY is a genuine, correctly
        reported EXACT TIE (both operators show 100% wet hours for both
        stations, MINOR 2026-08-17 review), covered separately below."""
        table = _two_station_table()
        across_quantiles = table.filter(
            (pl.col("scope") == "ACROSS_STATION") & (pl.col("statistic") == "QUANTILE")
        )
        assert across_quantiles.height > 0
        assert across_quantiles["sign_agreement_fraction"].null_count() == 0
        # ... and only there (D9: populated only on ACROSS_STATION rows).
        per_station = table.filter(pl.col("scope") == "STATION")
        assert per_station["sign_agreement_fraction"].null_count() == per_station.height

    def test_sign_agreement_differs_between_the_median_and_the_tail(self) -> None:
        """Constructed so the two stations SPLIT at the median but AGREE at
        q0.99 — a table that reports one hard-coded q=0.5 figure cannot tell
        these apart."""
        table = _two_station_table()

        def _agreement(q: float) -> float:
            row = table.filter(
                (pl.col("scope") == "ACROSS_STATION")
                & (pl.col("season") == "JJAS")
                & (pl.col("statistic") == "QUANTILE")
                & (pl.col("quantile") == q)
            )
            assert row.height == 1
            return float(row["sign_agreement_fraction"][0])

        assert _agreement(0.5) == pytest.approx(0.5)
        assert _agreement(0.99) == pytest.approx(1.0)

    def test_wet_hour_statistics_also_carry_sign_agreement(self) -> None:
        """WET_MEAN_INTENSITY differs by station (a real signal) and must
        report a non-null fraction. WET_FREQUENCY is a genuine EXACT TIE in
        this fixture (every value at every station, both operators, is
        above the 0.2 mm/h wet threshold, so both operators report 100% wet
        frequency for both stations) — MINOR (2026-08-17 review): a tie
        correctly reports `None` (no direction), not the OLD buggy `1.0`
        ("ties count as positive")."""
        table = _two_station_table()
        wet_mean_row = table.filter(
            (pl.col("scope") == "ACROSS_STATION")
            & (pl.col("season") == "JJAS")
            & (pl.col("statistic") == "WET_MEAN_INTENSITY")
        )
        assert wet_mean_row.height == 1
        assert wet_mean_row["sign_agreement_fraction"][0] is not None

        wet_freq_row = table.filter(
            (pl.col("scope") == "ACROSS_STATION")
            & (pl.col("season") == "JJAS")
            & (pl.col("statistic") == "WET_FREQUENCY")
        )
        assert wet_freq_row.height == 1
        assert wet_freq_row["sign_agreement_fraction"][0] is None

    def test_excluded_hour_count_is_at_each_rows_own_grain(self) -> None:
        """D1a (CORRECTED 2026-08-16) — a single global excluded-hour count
        was copied onto every row, so a station-and-season figure silently
        reported a whole-run total."""
        valid_time = np.array(
            [
                np.datetime64("2021-06-01T00:00") + np.timedelta64(h, "h")
                for h in range(4)
            ]
        )
        clean = np.array([1.0, 2.0, 3.0, 4.0])
        holed = np.array([1.0, np.nan, 3.0, np.nan])
        nearest = {
            Station("A"): _series(
                Station("A"), ExtractionOperator.NEAREST, valid_time, clean
            ),
            Station("B"): _series(
                Station("B"), ExtractionOperator.NEAREST, valid_time, clean
            ),
        }
        bilinear = {
            Station("A"): _series(
                Station("A"), ExtractionOperator.BILINEAR, valid_time, clean
            ),
            Station("B"): _series(
                Station("B"), ExtractionOperator.BILINEAR, valid_time, holed
            ),
        }
        table = build_operator_sensitivity_table(
            nearest, bilinear, params=DEFAULT_PARAMS
        )

        def _counts(scope: str, station: str | None) -> tuple[int, int]:
            row = table.filter(
                (pl.col("scope") == scope)
                & (pl.col("season") == "JJAS")
                & (pl.col("statistic") == "WET_FREQUENCY")
                & (
                    pl.col("station").is_null()
                    if station is None
                    else pl.col("station") == station
                )
            )
            assert row.height == 1
            return (
                int(row["n_hours_common_finite"][0]),
                int(row["n_hours_excluded"][0]),
            )

        assert _counts("STATION", "A") == (4, 0)
        assert _counts("STATION", "B") == (2, 2)
        assert _counts("ACROSS_STATION", None) == (6, 2)

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
        total_hours = nearest[Station("A")].values.size
        assert (table["n_hours_common_finite"] == 0).all()
        # UPDATED for the corrected D1a grain rule: `n_hours_excluded` is no
        # longer one global figure stamped onto every row. The whole-period
        # ("ALL") row carries the whole-run total, and the four disjoint
        # seasons partition it — which is what a per-grain count means.
        one_key = table.filter(
            (pl.col("scope") == "STATION") & (pl.col("statistic") == "WET_FREQUENCY")
        )
        by_season = dict(
            zip(
                one_key["season"].to_list(),
                one_key["n_hours_excluded"].to_list(),
                strict=True,
            )
        )
        assert by_season["ALL"] == total_hours
        assert sum(v for k, v in by_season.items() if k != "ALL") == total_hours
