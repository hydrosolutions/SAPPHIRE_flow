"""Plan 184 (M-A6) task T4 — representativeness, characterised (D5/D7/D8/D9).

Seam tests: every assertion is on a VALUE actually produced, never on an
argument passed to a mock (this track's own recurring failure mode). Two
tests specifically prove the milestone's own trap, named four times in
Phase 1 review and repeated by D2/D8 here: (1) D9's `sign_agreement_fraction`
is legitimately null on every STATION row and must NOT be rejected; (2) D8's
within-cell pair must be computed on COMMONLY-retained hours, never on
either station's own retention.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import DatumReconciliationStatus, Station
from scripts.dhm_precip.era5_errors import (
    ExtractionInputAbsentError,
    ExtractionPostConditionError,
    StationSetMismatchError,
)
from scripts.dhm_precip.era5_extract_manifest import (
    ExtractionManifest,
    checksum_file,
    manifest_filename,
    points_root,
    write_extraction_manifest,
)
from scripts.dhm_precip.era5_manifest import product_artifact_path
from scripts.dhm_precip.fixtures import (
    build_era5_land_product_fixture,
    write_era5_land_product_fixture,
)
from scripts.dhm_precip.ma6_pairs import GaugeMaskedPopulation, MaskedGaugeSeries
from scripts.dhm_precip.ma6_representativeness import (
    KHUMALTAR,
    KIRTIPUR,
    ElevationMismatchCovariate,
    _neighbour_cells,
    _neighbour_range_and_cv,
    compute_neighbour_cell_variability,
    compute_within_cell_pair,
    read_elevation_mismatch_covariates,
    read_operator_sensitivity_envelope,
)

if TYPE_CHECKING:
    from pathlib import Path

_AREA = (26.2, 85.0, 26.0, 85.2)  # the 3x3-node fixture default
_YEAR = 2021

_MANIFEST_BASE_KWARGS: dict[str, object] = {
    "orography_identity": "o",
    "operator_id": "NEAREST",
    "coordinate_table_sha256": "a" * 64,
    "source_sha256s_by_year": {"2024": "b" * 64},
    "orography_spec": {},
    "orography_source_record": {},
    "accumulation_diagnostic": {},
    "generated_at": datetime(2026, 1, 1, tzinfo=UTC),
}


def _publish_fake_bundle(
    root: Path,
    *,
    run_number: str,
    identity: str,
    operator_sensitivity_csv: bytes,
    elevation_csv: bytes,
) -> Path:
    directory = points_root(root) / f"{run_number}-{identity}"
    directory.mkdir(parents=True)
    sensitivity_path = directory / "operator_sensitivity.csv"
    sensitivity_path.write_bytes(operator_sensitivity_csv)
    elevation_path = directory / "station_grid_elevation.csv"
    elevation_path.write_bytes(elevation_csv)
    write_extraction_manifest(
        ExtractionManifest(
            extraction_identity=identity,
            payload_sha256s={
                "operator_sensitivity.csv": checksum_file(sensitivity_path),
                "station_grid_elevation.csv": checksum_file(elevation_path),
            },
            **_MANIFEST_BASE_KWARGS,
        ),
        directory / manifest_filename(),
    )
    return directory


# --- fixture content: the REAL D9 schema, verified on the real bundle ---

_SENSITIVITY_HEADER = (
    "scope,station,season,statistic,quantile,nearest_value,bilinear_value,"
    "delta_absolute,delta_unit,ratio,n_hours_common_finite,n_hours_excluded,"
    "n_wet_nearest,n_wet_bilinear,sign_agreement_fraction\n"
)


def _sensitivity_csv_bytes() -> bytes:
    # Two STATION rows (null sign_agreement_fraction — D9's real, legitimate
    # shape) and one ACROSS_STATION row (populated) — mirrors the real
    # bundle's exact pattern (1,300/1,300 null on STATION, 0/50 null on
    # ACROSS_STATION).
    rows = (
        "STATION,Kirtipur,ALL,QUANTILE,0.5,0.64,0.62,0.02,MM_PER_H,1.03,"
        "100,0,40,42,\n"
        "STATION,Khumaltar,ALL,WET_FREQUENCY,,0.3,0.28,0.02,FRACTION,1.07,"
        "100,0,30,28,\n"
        "ACROSS_STATION,,ALL,QUANTILE,0.5,0.7,0.65,0.05,MM_PER_H,1.08,"
        "200,0,70,70,1.0\n"
    )
    return (_SENSITIVITY_HEADER + rows).encode()


def _elevation_csv_bytes() -> bytes:
    header = (
        "station,lat,lon,grid_lat,grid_lon,grid_i,grid_j,offset_km,"
        "station_elev_m,station_elevation_datum,orography_elev_m,"
        "orography_elevation_datum,orography_source,orography_product_id,"
        "orography_product_version,elev_mismatch_m,datum_reconciled,"
        "shared_cell_id,stations_in_cell\n"
    )
    rows = (
        "Kirtipur,27.68333,85.3,27.7,85.3,33,53,1.85,1364.0,UNKNOWN,"
        "1613.5,LOCAL_MSL,MODEL_OROGRAPHY,x,y,-249.5,UNRECONCILED,33_53,2\n"
        "Khumaltar,27.65175,85.32577,27.7,85.3,33,53,5.94,1334.0,UNKNOWN,"
        "1613.5,LOCAL_MSL,MODEL_OROGRAPHY,x,y,-279.5,UNRECONCILED,33_53,2\n"
        "Syangboche Airport,27.81667,86.71667,27.8,86.7,32,67,2.47,3700.0,"
        "UNKNOWN,4447.4,LOCAL_MSL,MODEL_OROGRAPHY,x,y,-747.4,UNRECONCILED,"
        "32_67,1\n"
    )
    return (header + rows).encode()


class TestOperatorSensitivityEnvelope:
    def test_does_not_reject_null_sign_agreement_on_station_rows(
        self, tmp_path: Path
    ) -> None:
        """D9's own trap: a validator demanding `sign_agreement_fraction`
        non-null rejects every valid STATION row. This must succeed and
        return the nulls intact."""
        directory = _publish_fake_bundle(
            tmp_path,
            run_number="0000",
            identity="ident0",
            operator_sensitivity_csv=_sensitivity_csv_bytes(),
            elevation_csv=_elevation_csv_bytes(),
        )
        manifest = ExtractionManifest(
            extraction_identity="ident0",
            payload_sha256s={
                "operator_sensitivity.csv": checksum_file(
                    directory / "operator_sensitivity.csv"
                ),
                "station_grid_elevation.csv": checksum_file(
                    directory / "station_grid_elevation.csv"
                ),
            },
            **_MANIFEST_BASE_KWARGS,
        )

        frame = read_operator_sensitivity_envelope(directory, manifest)

        station_rows = frame.filter(pl.col("scope") == "STATION")
        assert station_rows.height == 2
        assert station_rows["sign_agreement_fraction"].null_count() == 2
        across_rows = frame.filter(pl.col("scope") == "ACROSS_STATION")
        assert across_rows["sign_agreement_fraction"].null_count() == 0
        assert set(frame["statistic"].unique().to_list()) == {
            "QUANTILE",
            "WET_FREQUENCY",
        }
        # absolute/ratio/sign-agreement are COLUMNS, never STATISTIC values.
        assert "delta_absolute" not in frame["statistic"].unique().to_list()

    def test_raises_on_a_missing_required_column(self, tmp_path: Path) -> None:
        broken = b"scope,station\nSTATION,Kirtipur\n"
        directory = _publish_fake_bundle(
            tmp_path,
            run_number="0000",
            identity="ident0",
            operator_sensitivity_csv=broken,
            elevation_csv=_elevation_csv_bytes(),
        )
        manifest = ExtractionManifest(
            extraction_identity="ident0",
            payload_sha256s={
                "operator_sensitivity.csv": checksum_file(
                    directory / "operator_sensitivity.csv"
                ),
                "station_grid_elevation.csv": checksum_file(
                    directory / "station_grid_elevation.csv"
                ),
            },
            **_MANIFEST_BASE_KWARGS,
        )

        with pytest.raises(ExtractionPostConditionError, match="missing column"):
            read_operator_sensitivity_envelope(directory, manifest)

    def test_a_checksum_mismatch_raises_hard(self, tmp_path: Path) -> None:
        directory = _publish_fake_bundle(
            tmp_path,
            run_number="0000",
            identity="ident0",
            operator_sensitivity_csv=_sensitivity_csv_bytes(),
            elevation_csv=_elevation_csv_bytes(),
        )
        manifest = ExtractionManifest(
            extraction_identity="ident0",
            payload_sha256s={
                "operator_sensitivity.csv": checksum_file(
                    directory / "operator_sensitivity.csv"
                ),
                "station_grid_elevation.csv": checksum_file(
                    directory / "station_grid_elevation.csv"
                ),
            },
            **_MANIFEST_BASE_KWARGS,
        )
        # Tamper a VALUE, not the structure — the mismatch must be caught by
        # the checksum guard specifically, never by the (separate) required-
        # column check silently catching it for the wrong reason instead.
        tampered = _sensitivity_csv_bytes().replace(b"0.64,0.62", b"0.99,0.62")
        (directory / "operator_sensitivity.csv").write_bytes(tampered)

        with pytest.raises(ExtractionPostConditionError, match="P4a"):
            read_operator_sensitivity_envelope(directory, manifest)


class TestElevationMismatchCovariate:
    def test_reads_every_row_labelled_datum_unreconciled(self, tmp_path: Path) -> None:
        directory = _publish_fake_bundle(
            tmp_path,
            run_number="0000",
            identity="ident0",
            operator_sensitivity_csv=_sensitivity_csv_bytes(),
            elevation_csv=_elevation_csv_bytes(),
        )
        manifest = ExtractionManifest(
            extraction_identity="ident0",
            payload_sha256s={
                "operator_sensitivity.csv": checksum_file(
                    directory / "operator_sensitivity.csv"
                ),
                "station_grid_elevation.csv": checksum_file(
                    directory / "station_grid_elevation.csv"
                ),
            },
            **_MANIFEST_BASE_KWARGS,
        )

        rows = read_elevation_mismatch_covariates(directory, manifest)

        assert len(rows) == 3
        assert all(
            r.datum_reconciled == DatumReconciliationStatus.UNRECONCILED for r in rows
        )
        kirtipur = next(r for r in rows if r.station == "Kirtipur")
        khumaltar = next(r for r in rows if r.station == "Khumaltar")
        assert kirtipur.shared_cell_id == khumaltar.shared_cell_id == "33_53"
        assert kirtipur.stations_in_cell == 2
        assert kirtipur.elev_mismatch_m == pytest.approx(-249.5)

    def test_raises_on_a_missing_required_column(self, tmp_path: Path) -> None:
        broken = b"station,grid_i\nKirtipur,33\n"
        directory = _publish_fake_bundle(
            tmp_path,
            run_number="0000",
            identity="ident0",
            operator_sensitivity_csv=_sensitivity_csv_bytes(),
            elevation_csv=broken,
        )
        manifest = ExtractionManifest(
            extraction_identity="ident0",
            payload_sha256s={
                "operator_sensitivity.csv": checksum_file(
                    directory / "operator_sensitivity.csv"
                ),
                "station_grid_elevation.csv": checksum_file(
                    directory / "station_grid_elevation.csv"
                ),
            },
            **_MANIFEST_BASE_KWARGS,
        )

        with pytest.raises(ExtractionPostConditionError, match="missing column"):
            read_elevation_mismatch_covariates(directory, manifest)

    def test_a_checksum_mismatch_raises_hard(self, tmp_path: Path) -> None:
        directory = _publish_fake_bundle(
            tmp_path,
            run_number="0000",
            identity="ident0",
            operator_sensitivity_csv=_sensitivity_csv_bytes(),
            elevation_csv=_elevation_csv_bytes(),
        )
        manifest = ExtractionManifest(
            extraction_identity="ident0",
            payload_sha256s={
                "operator_sensitivity.csv": checksum_file(
                    directory / "operator_sensitivity.csv"
                ),
                "station_grid_elevation.csv": checksum_file(
                    directory / "station_grid_elevation.csv"
                ),
            },
            **_MANIFEST_BASE_KWARGS,
        )
        # Tamper a VALUE, not the structure — see the sensitivity-envelope
        # test's identical note: this must be caught by the checksum guard
        # specifically, not by a coincidental downstream parse failure.
        tampered = _elevation_csv_bytes().replace(b"1364.0", b"1364.9")
        (directory / "station_grid_elevation.csv").write_bytes(tampered)

        with pytest.raises(ExtractionPostConditionError, match="P4a"):
            read_elevation_mismatch_covariates(directory, manifest)


# --- 3: neighbouring-cell variability ---


class TestNeighbourCells:
    def test_a_centre_cell_on_a_3x3_grid_has_eight_neighbours(self) -> None:
        cells = _neighbour_cells(1, 1, n_lat=3, n_lon=3)
        assert len(cells) == 8
        assert (1, 1) not in cells

    def test_a_corner_cell_is_clipped_to_its_three_on_grid_neighbours(self) -> None:
        cells = _neighbour_cells(0, 0, n_lat=3, n_lon=3)
        assert set(cells) == {(0, 1), (1, 0), (1, 1)}


class TestNeighbourRangeAndCv:
    def test_hand_computed_range_and_cv(self) -> None:
        # population stdev of (1, 2, 3) is sqrt(2/3); mean is 2.
        rng, cv = _neighbour_range_and_cv([1.0, 2.0, 3.0])
        assert rng == pytest.approx(2.0)
        assert cv == pytest.approx((2.0 / 3.0) ** 0.5 / 2.0)

    def test_a_single_neighbour_has_no_cv_but_a_zero_range(self) -> None:
        rng, cv = _neighbour_range_and_cv([5.0])
        assert rng == pytest.approx(0.0)
        assert cv is None

    def test_a_zero_mean_neighbour_set_has_no_cv(self) -> None:
        rng, cv = _neighbour_range_and_cv([-1.0, 1.0])
        assert rng == pytest.approx(2.0)
        assert cv is None

    def test_an_empty_neighbour_set_raises(self) -> None:
        with pytest.raises(ValueError, match="zero on-grid neighbours"):
            _neighbour_range_and_cv([])


def _elevation_covariate(
    station: str, *, grid_i: int, grid_j: int
) -> ElevationMismatchCovariate:
    return ElevationMismatchCovariate(
        station=Station(station),
        station_elev_m=1000.0,
        orography_elev_m=1200.0,
        elev_mismatch_m=-200.0,
        datum_reconciled=DatumReconciliationStatus.UNRECONCILED,
        grid_i=grid_i,
        grid_j=grid_j,
        shared_cell_id=f"{grid_i}_{grid_j}",
        stations_in_cell=1,
    )


class TestComputeNeighbourCellVariability:
    def test_centre_station_matches_the_hand_computed_ramp_totals(
        self, tmp_path: Path
    ) -> None:
        ds = build_era5_land_product_fixture(year=_YEAR, area=_AREA)
        write_era5_land_product_fixture(ds, product_artifact_path(_YEAR, tmp_path))
        n_hours = ds.sizes["valid_time"]
        elevation_rows = (_elevation_covariate("Centre", grid_i=1, grid_j=1),)

        (result,) = compute_neighbour_cell_variability(
            elevation_rows, data_root=tmp_path, years=(_YEAR,)
        )

        # ramp(i, j) = 0.5 + 1.0*i + 0.1*j (fixture defaults), time-invariant.
        expected_assigned = (0.5 + 1.0 * 1 + 0.1 * 1) * n_hours
        expected_neighbours = sorted(
            (0.5 + 1.0 * i + 0.1 * j) * n_hours
            for i in range(3)
            for j in range(3)
            if (i, j) != (1, 1)
        )
        assert result.assigned_total_mm == pytest.approx(expected_assigned)
        assert result.n_neighbours == 8
        assert sorted(result.neighbour_total_mm) == pytest.approx(expected_neighbours)
        assert result.neighbour_range_mm == pytest.approx(
            max(expected_neighbours) - min(expected_neighbours)
        )
        assert result.neighbour_cv is not None

    def test_a_corner_station_reports_only_its_three_on_grid_neighbours(
        self, tmp_path: Path
    ) -> None:
        ds = build_era5_land_product_fixture(year=_YEAR, area=_AREA)
        write_era5_land_product_fixture(ds, product_artifact_path(_YEAR, tmp_path))
        elevation_rows = (_elevation_covariate("Corner", grid_i=0, grid_j=0),)

        (result,) = compute_neighbour_cell_variability(
            elevation_rows, data_root=tmp_path, years=(_YEAR,)
        )

        assert result.n_neighbours == 3

    def test_raises_when_the_product_is_absent(self, tmp_path: Path) -> None:
        elevation_rows = (_elevation_covariate("Centre", grid_i=1, grid_j=1),)
        with pytest.raises(ExtractionInputAbsentError):
            compute_neighbour_cell_variability(
                elevation_rows, data_root=tmp_path, years=(_YEAR,)
            )

    def test_raises_on_a_non_finite_neighbour_total(self, tmp_path: Path) -> None:
        ds = build_era5_land_product_fixture(
            year=_YEAR, area=_AREA, defect="nan_patch", defect_cell=(0, 1)
        )
        write_era5_land_product_fixture(ds, product_artifact_path(_YEAR, tmp_path))
        elevation_rows = (_elevation_covariate("Centre", grid_i=1, grid_j=1),)

        with pytest.raises(ExtractionPostConditionError, match="non-finite"):
            compute_neighbour_cell_variability(
                elevation_rows, data_root=tmp_path, years=(_YEAR,)
            )


# --- 4: the Kirtipur/Khumaltar within-cell pair (D8) ---


def _masked_series(
    station: str, timestamps: list[datetime], values: list[float]
) -> MaskedGaugeSeries:
    frame = pl.DataFrame({"timestamp": timestamps, "value_mm": values}).with_columns(
        pl.col("timestamp").cast(pl.Datetime("ms"))
    )
    return MaskedGaugeSeries(station=Station(station), frame=frame)


def _t(hour: int) -> datetime:
    return datetime(2024, 7, 1, tzinfo=UTC) + timedelta(hours=hour)


class TestComputeWithinCellPair:
    def test_computed_on_commonly_retained_hours_not_either_stations_own(
        self,
    ) -> None:
        """D8's own trap (this milestone's recurring failure mode): Kirtipur
        retains hours 0-4 (5 hours), Khumaltar retains hours 2-6 (5 hours),
        with only hours 2-4 (3 hours) retained by BOTH. `n_common_retained`
        must be 3 — neither station's own 5 — and the accumulated
        difference must rest on exactly those 3 hours' values, not on
        either station's full 5-hour sum."""
        kirtipur = _masked_series(
            "Kirtipur",
            [_t(h) for h in range(0, 5)],
            [1.0, 2.0, 3.0, 4.0, 5.0],
        )
        khumaltar = _masked_series(
            "Khumaltar",
            [_t(h) for h in range(2, 7)],
            [30.0, 40.0, 50.0, 60.0, 70.0],
        )
        population = GaugeMaskedPopulation(
            by_station={
                Station("Kirtipur"): kirtipur,
                Station("Khumaltar"): khumaltar,
            },
            excluded=(),
            accounting=(),
        )
        elevation_rows = (
            _elevation_covariate("Kirtipur", grid_i=33, grid_j=53),
            _elevation_covariate("Khumaltar", grid_i=33, grid_j=53),
        )

        result = compute_within_cell_pair(
            population,
            elevation_rows,
            station_a=Station("Kirtipur"),
            station_b=Station("Khumaltar"),
        )

        assert result.n_common_retained == 3  # hours 2, 3, 4 only
        assert result.n_a_gauge_retained == 5  # Kirtipur's OWN exposure
        assert result.n_b_gauge_retained == 5  # Khumaltar's OWN exposure
        # Kirtipur values at hours 2,3,4 = 3,4,5; Khumaltar = 30,40,50.
        assert result.sum_a_mm == pytest.approx(12.0)
        assert result.sum_b_mm == pytest.approx(120.0)
        assert result.accumulated_difference_mm == pytest.approx(12.0 - 120.0)
        assert result.mean_difference_mm_per_h == pytest.approx((12.0 - 120.0) / 3.0)

    def test_raises_when_the_named_pair_does_not_share_a_cell(self) -> None:
        kirtipur = _masked_series("Kirtipur", [_t(0)], [1.0])
        elsewhere = _masked_series("Elsewhere", [_t(0)], [2.0])
        population = GaugeMaskedPopulation(
            by_station={
                Station("Kirtipur"): kirtipur,
                Station("Elsewhere"): elsewhere,
            },
            excluded=(),
            accounting=(),
        )
        elevation_rows = (
            _elevation_covariate("Kirtipur", grid_i=33, grid_j=53),
            _elevation_covariate("Elsewhere", grid_i=1, grid_j=1),
        )

        with pytest.raises(StationSetMismatchError, match="do not share a cell"):
            compute_within_cell_pair(
                population,
                elevation_rows,
                station_a=Station("Kirtipur"),
                station_b=Station("Elsewhere"),
            )

    def test_zero_common_hours_reports_none_statistics_but_real_counts(
        self,
    ) -> None:
        kirtipur = _masked_series("Kirtipur", [_t(0)], [1.0])
        khumaltar = _masked_series("Khumaltar", [_t(10)], [2.0])
        population = GaugeMaskedPopulation(
            by_station={
                Station("Kirtipur"): kirtipur,
                Station("Khumaltar"): khumaltar,
            },
            excluded=(),
            accounting=(),
        )
        elevation_rows = (
            _elevation_covariate("Kirtipur", grid_i=33, grid_j=53),
            _elevation_covariate("Khumaltar", grid_i=33, grid_j=53),
        )

        result = compute_within_cell_pair(
            population,
            elevation_rows,
            station_a=Station("Kirtipur"),
            station_b=Station("Khumaltar"),
        )

        assert result.n_common_retained == 0
        assert result.n_a_gauge_retained == 1
        assert result.n_b_gauge_retained == 1
        assert result.mean_difference_mm_per_h is None
        assert result.accumulated_difference_mm is None

    def test_the_named_constants_are_kirtipur_and_khumaltar(self) -> None:
        assert KIRTIPUR == "Kirtipur"
        assert KHUMALTAR == "Khumaltar"


if __name__ == "__main__":
    pytest.main([__file__])
