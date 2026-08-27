"""Plan 184 (M-A6) task T6 — the report and Exit compliance.

Seam tests: every assertion lands on a VALUE actually produced — a
rendered cell, a raised exception, a computed band mean — never on an
argument passed to a mock (the same discipline `test_ma6_pairs.py`/
`test_ma6_estimands.py` state for T1/T3). `run_ma6_comparison` is
exercised entirely against a synthetic, in-memory `Ma6Inputs` — no disk
I/O anywhere in this file, mirroring `test_dhm_precip_coloc_run.py`'s own
discipline for `run_coloc_adjudication`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import DatumReconciliationStatus, Station
from scripts.dhm_precip.ma6_estimands import (
    BandMember,
    ElevationBand,
    ElevationBandEstimand,
    matched_hour_mean_difference,
    scale_subset,
)
from scripts.dhm_precip.ma6_mass_fraction import (
    ElevationBandMassFraction,
    StationElevationInputs,
    build_sub_freezing_mass_fraction,
)
from scripts.dhm_precip.ma6_pairs import (
    GaugeMaskedPopulation,
    MaskedGaugeSeries,
    PairedSeries,
    Scale,
)
from scripts.dhm_precip.ma6_representativeness import (
    KHUMALTAR,
    KIRTIPUR,
    ElevationMismatchCovariate,
    NeighbourCellStat,
)
from scripts.dhm_precip.ma6_run import (
    AbsentResult,
    BandMagnitudeCell,
    Ma6Inputs,
    Ma6InputsConsistencyError,
    MagnitudeKind,
    MagnitudeMassFractionMismatchError,
    StationMagnitudeCell,
    StationSensitivityRow,
    _band_magnitude_table,
    _identities_lines,
    _lapse_transect_table,
    _retention_table,
    _sensitivity_table,
    _station_magnitude_table,
    _within_cell_table,
    build_parser,
    run_ma6_comparison,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.qc_mask import (
    ExclusionListEntry,
    RemovalAccountingRow,
    RetentionCategory,
)
from scripts.dhm_precip.seasons import Season

if TYPE_CHECKING:
    from collections.abc import Sequence

_A = Station("A_low")  # elevation band < 700 m
_B = Station("B_high")  # elevation band >= 3,000 m
_ELEV_ZERO_CORRECTION = 1000.0  # orography == station elev -> +0.0 correction


def _gauge_frame(
    station: Station, start: datetime, values: Sequence[float]
) -> pl.DataFrame:
    ts = [start + timedelta(hours=i) for i in range(len(values))]
    return pl.DataFrame(
        {"station": [str(station)] * len(values), "timestamp": ts, "value_mm": values}
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


def _paired_series(
    station: Station, start: datetime, gauge: Sequence[float], era5: Sequence[float]
) -> PairedSeries:
    ts = [start + timedelta(hours=i) for i in range(len(gauge))]
    frame = pl.DataFrame(
        {
            "station": [str(station)] * len(gauge),
            "timestamp": ts,
            "gauge_value_mm": gauge,
            "era5_nearest_mm_per_h": era5,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))
    return PairedSeries(frame=frame)


def _t2m_frame(
    station: Station, start: datetime, temps: Sequence[float]
) -> pl.DataFrame:
    ts = [start + timedelta(hours=i) for i in range(len(temps))]
    return pl.DataFrame(
        {
            "station": [str(station)] * len(temps),
            "timestamp": ts,
            "grid_t2m_degc": temps,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


def _elevation_inputs(elev_m: float) -> StationElevationInputs:
    return StationElevationInputs(
        station_elev_m=elev_m,
        orography_elev_m=elev_m,
        datum_reconciled=DatumReconciliationStatus.UNRECONCILED,
    )


def _elevation_mismatch_row(
    station: Station, elev_m: float, *, cell_id: str, stations_in_cell: int = 1
) -> ElevationMismatchCovariate:
    return ElevationMismatchCovariate(
        station=station,
        station_elev_m=elev_m,
        orography_elev_m=elev_m,
        elev_mismatch_m=0.0,
        datum_reconciled=DatumReconciliationStatus.UNRECONCILED,
        grid_i=0,
        grid_j=0,
        shared_cell_id=cell_id,
        stations_in_cell=stations_in_cell,
    )


def _neighbour_stat(station: Station) -> NeighbourCellStat:
    return NeighbourCellStat(
        station=station,
        grid_i=0,
        grid_j=0,
        years=(2024,),
        assigned_total_mm=10.0,
        neighbour_total_mm=(10.0, 11.0),
        n_neighbours=2,
        neighbour_range_mm=1.0,
        neighbour_cv=0.05,
    )


_JJAS_START = datetime(2024, 7, 1)


def _build_two_station_inputs(
    *,
    n_hours: int = 48,
    include_within_cell_pair: bool = True,
    accounting: tuple[RemovalAccountingRow, ...] = (),
    excluded: tuple[ExclusionListEntry, ...] = (),
    operator_sensitivity: pl.DataFrame | None = None,
) -> Ma6Inputs:
    """One low-elevation and one high-elevation station, `n_hours` of JJAS
    data only (no DJF data at all — deliberately, so DJF magnitudes exercise
    T6 P3's refusal path in every test that doesn't override it)."""
    gauge_by_station = {
        _A: MaskedGaugeSeries(frame=_gauge_frame(_A, _JJAS_START, [1.0] * n_hours)),
        _B: MaskedGaugeSeries(frame=_gauge_frame(_B, _JJAS_START, [2.0] * n_hours)),
    }
    paired = {
        _A: _paired_series(_A, _JJAS_START, [1.0] * n_hours, [0.5] * n_hours),
        _B: _paired_series(_B, _JJAS_START, [2.0] * n_hours, [1.5] * n_hours),
    }
    t2m = {
        _A: _t2m_frame(_A, _JJAS_START, [10.0] * n_hours),
        _B: _t2m_frame(_B, _JJAS_START, [-5.0] * n_hours),
    }
    elevations = {_A: _elevation_inputs(500.0), _B: _elevation_inputs(3500.0)}
    elevation_mismatch = [
        _elevation_mismatch_row(_A, 500.0, cell_id="cA"),
        _elevation_mismatch_row(_B, 3500.0, cell_id="cB"),
    ]
    neighbour_cell_stats = [_neighbour_stat(_A), _neighbour_stat(_B)]

    if include_within_cell_pair:
        gauge_by_station[KIRTIPUR] = MaskedGaugeSeries(
            frame=_gauge_frame(KIRTIPUR, _JJAS_START, [3.0] * n_hours)
        )
        gauge_by_station[KHUMALTAR] = MaskedGaugeSeries(
            frame=_gauge_frame(KHUMALTAR, _JJAS_START, [2.5] * n_hours)
        )
        paired[KIRTIPUR] = _paired_series(
            KIRTIPUR, _JJAS_START, [3.0] * n_hours, [1.0] * n_hours
        )
        paired[KHUMALTAR] = _paired_series(
            KHUMALTAR, _JJAS_START, [2.5] * n_hours, [1.0] * n_hours
        )
        t2m[KIRTIPUR] = _t2m_frame(KIRTIPUR, _JJAS_START, [15.0] * n_hours)
        t2m[KHUMALTAR] = _t2m_frame(KHUMALTAR, _JJAS_START, [15.0] * n_hours)
        elevations[KIRTIPUR] = _elevation_inputs(1300.0)
        elevations[KHUMALTAR] = _elevation_inputs(1300.0)
        elevation_mismatch += [
            _elevation_mismatch_row(
                KIRTIPUR, 1300.0, cell_id="cKK", stations_in_cell=2
            ),
            _elevation_mismatch_row(
                KHUMALTAR, 1300.0, cell_id="cKK", stations_in_cell=2
            ),
        ]

    gauge_population = GaugeMaskedPopulation(
        by_station=gauge_by_station, excluded=excluded, accounting=accounting
    )
    if operator_sensitivity is None:
        operator_sensitivity = pl.DataFrame(
            {
                "scope": ["STATION"],
                "station": ["A_low"],
                "season": ["JJAS"],
                "statistic": ["QUANTILE"],
                "quantile": [0.5],
                "nearest_value": [1.0],
                "bilinear_value": [1.1],
                "delta_absolute": [0.1],
                "delta_unit": ["mm"],
                "ratio": [1.1],
                "n_hours_common_finite": [10],
                "n_hours_excluded": [0],
                "n_wet_nearest": [5],
                "n_wet_bilinear": [5],
                "sign_agreement_fraction": [None],
            }
        )

    return Ma6Inputs(
        gauge_population=gauge_population,
        paired=paired,
        t2m_by_station=t2m,
        elevations=elevations,
        precip_extraction_identity="0001-precip-test",
        t2m_extraction_identity="0001-t2m-test",
        operator_sensitivity=operator_sensitivity,
        elevation_mismatch=tuple(elevation_mismatch),
        neighbour_cell_stats=tuple(neighbour_cell_stats),
        lapse_transect=(),
        lapse_gauge_diagnostic=None,
    )


class TestMa6InputsConsistency:
    def test_rejects_paired_station_absent_from_gauge_population(self) -> None:
        gauge_population = GaugeMaskedPopulation(
            by_station={
                _A: MaskedGaugeSeries(frame=_gauge_frame(_A, _JJAS_START, [1.0]))
            },
            excluded=(),
            accounting=(),
        )
        paired = {_B: _paired_series(_B, _JJAS_START, [1.0], [0.5])}

        with pytest.raises(Ma6InputsConsistencyError, match="gauge_population"):
            Ma6Inputs(
                gauge_population=gauge_population,
                paired=paired,
                t2m_by_station={_B: _t2m_frame(_B, _JJAS_START, [1.0])},
                elevations={_B: _elevation_inputs(500.0)},
                precip_extraction_identity="x",
                t2m_extraction_identity="y",
                operator_sensitivity=pl.DataFrame(),
                elevation_mismatch=(),
                neighbour_cell_stats=(),
                lapse_transect=(),
                lapse_gauge_diagnostic=None,
            )

    def test_rejects_paired_station_absent_from_t2m(self) -> None:
        gauge_population = GaugeMaskedPopulation(
            by_station={
                _A: MaskedGaugeSeries(frame=_gauge_frame(_A, _JJAS_START, [1.0]))
            },
            excluded=(),
            accounting=(),
        )
        paired = {_A: _paired_series(_A, _JJAS_START, [1.0], [0.5])}

        with pytest.raises(Ma6InputsConsistencyError, match="t2m_by_station"):
            Ma6Inputs(
                gauge_population=gauge_population,
                paired=paired,
                t2m_by_station={},
                elevations={_A: _elevation_inputs(500.0)},
                precip_extraction_identity="x",
                t2m_extraction_identity="y",
                operator_sensitivity=pl.DataFrame(),
                elevation_mismatch=(),
                neighbour_cell_stats=(),
                lapse_transect=(),
                lapse_gauge_diagnostic=None,
            )

    def test_rejects_paired_station_absent_from_elevations(self) -> None:
        gauge_population = GaugeMaskedPopulation(
            by_station={
                _A: MaskedGaugeSeries(frame=_gauge_frame(_A, _JJAS_START, [1.0]))
            },
            excluded=(),
            accounting=(),
        )
        paired = {_A: _paired_series(_A, _JJAS_START, [1.0], [0.5])}

        with pytest.raises(Ma6InputsConsistencyError, match="elevations"):
            Ma6Inputs(
                gauge_population=gauge_population,
                paired=paired,
                t2m_by_station={_A: _t2m_frame(_A, _JJAS_START, [1.0])},
                elevations={},
                precip_extraction_identity="x",
                t2m_extraction_identity="y",
                operator_sensitivity=pl.DataFrame(),
                elevation_mismatch=(),
                neighbour_cell_stats=(),
                lapse_transect=(),
                lapse_gauge_diagnostic=None,
            )


class TestStationMagnitudeCellExit2Enforcement:
    """T6 P4 — the SPECIFIC named test this task's verify command requires:
    a magnitude missing its mass-fraction companion (or paired with the
    WRONG one) is a hard failure, not a formatting choice."""

    def _estimand_and_mass_fraction(self, *, gauge: float, era5: float):
        paired = _paired_series(_A, _JJAS_START, [gauge] * 8, [era5] * 8)
        sub = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        estimand = matched_hour_mean_difference(sub)
        mass_fraction = build_sub_freezing_mass_fraction(
            estimand,
            t2m_by_station={_A: _t2m_frame(_A, _JJAS_START, [10.0] * 8)},
            elevations_by_station={_A: _elevation_inputs(500.0)},
        )
        return estimand, mass_fraction

    def test_accepts_a_mass_fraction_built_from_the_same_estimand(self) -> None:
        estimand, mass_fraction = self._estimand_and_mass_fraction(gauge=1.0, era5=0.5)

        cell = StationMagnitudeCell(
            kind=MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE,
            estimand=estimand,
            mass_fraction=mass_fraction,
        )

        assert cell.n == mass_fraction.n
        assert cell.value == pytest.approx(0.5)

    def test_rejects_a_mass_fraction_built_from_a_data_equal_but_different_subset(
        self,
    ) -> None:
        """The mutation-critical case: TWO separately-built subsets carrying
        IDENTICAL values (same station, same data) are still two DIFFERENT
        objects — `scale_subset` filters fresh every call. If the guard used
        `==` instead of `is`, this would wrongly PASS (polars frame equality
        would either match or raise, never protect against this). `is`
        catches it because object identity, not data, is what Exit 2's
        pairing promise is about."""
        estimand, _own_mass_fraction = self._estimand_and_mass_fraction(
            gauge=1.0, era5=0.5
        )
        _other_estimand, other_mass_fraction = self._estimand_and_mass_fraction(
            gauge=1.0, era5=0.5
        )

        with pytest.raises(MagnitudeMassFractionMismatchError):
            StationMagnitudeCell(
                kind=MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE,
                estimand=estimand,
                mass_fraction=other_mass_fraction,
            )

    def test_rejects_a_mass_fraction_from_a_different_station(self) -> None:
        estimand_a, _ = self._estimand_and_mass_fraction(gauge=1.0, era5=0.5)
        paired_b = _paired_series(_B, _JJAS_START, [2.0] * 8, [1.5] * 8)
        sub_b = scale_subset(paired_b, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        estimand_b = matched_hour_mean_difference(sub_b)
        mass_fraction_b = build_sub_freezing_mass_fraction(
            estimand_b,
            t2m_by_station={_B: _t2m_frame(_B, _JJAS_START, [10.0] * 8)},
            elevations_by_station={_B: _elevation_inputs(3500.0)},
        )

        with pytest.raises(MagnitudeMassFractionMismatchError):
            StationMagnitudeCell(
                kind=MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE,
                estimand=estimand_a,
                mass_fraction=mass_fraction_b,
            )


class TestBandMagnitudeCellExit2Enforcement:
    """The band-level analogue of `TestStationMagnitudeCellExit2Enforcement`
    — a single mismatched member must be enough to reject the whole band
    cell, since the band figure is a mean over exactly these members."""

    def test_rejects_a_member_whose_mass_fraction_was_built_from_a_different_subset(
        self,
    ) -> None:
        paired = _paired_series(_A, _JJAS_START, [1.0] * 8, [0.5] * 8)
        sub1 = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        sub2 = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)
        estimand = matched_hour_mean_difference(sub1)
        other_estimand = matched_hour_mean_difference(sub2)
        t2m_by_station = {_A: _t2m_frame(_A, _JJAS_START, [10.0] * 8)}
        elevations_by_station = {_A: _elevation_inputs(500.0)}
        # `other_estimand.subset` is data-equal to `estimand.subset` but a
        # DIFFERENT object — the same mutation-critical case as the
        # station-level test, one aggregation level up.
        mismatched_mass_fraction = build_sub_freezing_mass_fraction(
            other_estimand,
            t2m_by_station=t2m_by_station,
            elevations_by_station=elevations_by_station,
        )
        estimand_band = ElevationBandEstimand(
            band=ElevationBand.BELOW_700M,
            members=(BandMember(estimand=estimand),),
            station_elev_m={_A: 500.0},
        )
        mass_fraction_band = ElevationBandMassFraction(
            band=ElevationBand.BELOW_700M,
            members=(mismatched_mass_fraction,),
            station_elev_m={_A: 500.0},
        )

        with pytest.raises(MagnitudeMassFractionMismatchError):
            BandMagnitudeCell(
                kind=MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE,
                estimand_band=estimand_band,
                mass_fraction_band=mass_fraction_band,
            )


class TestRunMa6ComparisonStationMagnitudes:
    def test_every_present_cell_carries_n_and_mass_fraction_together(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        present = [
            c for c in report.station_magnitudes if isinstance(c, StationMagnitudeCell)
        ]
        assert present, "expected at least one present magnitude cell"
        for cell in present:
            assert cell.n == cell.mass_fraction.n
            assert cell.mass_fraction.subset is cell.estimand.subset

    def test_ten_kind_scale_combinations_per_station(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        per_station: dict[Station, int] = {}
        for cell in report.station_magnitudes:
            label = (
                cell.station
                if isinstance(cell, StationMagnitudeCell)
                else Station(cell.label)
            )
            per_station[label] = per_station.get(label, 0) + 1
        assert per_station == {_A: 10, _B: 10, KIRTIPUR: 10, KHUMALTAR: 10}

    def test_djf_magnitudes_are_absent_with_a_reason_never_dropped(self) -> None:
        """T6 P3 — no DJF data was given at all; the DJF rows must still
        APPEAR, as explicit absences, not vanish from the collection."""
        report = run_ma6_comparison(_build_two_station_inputs())

        djf_absences = [
            c
            for c in report.station_magnitudes
            if c.scale == Scale.DJF and isinstance(c, AbsentResult)
        ]
        assert len(djf_absences) == 16  # 4 kinds x 4 stations
        assert all(c.reason for c in djf_absences)

    def test_matched_hour_mean_difference_value_is_correct(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        cell = next(
            c
            for c in report.station_magnitudes
            if isinstance(c, StationMagnitudeCell)
            and c.station == _A
            and c.scale == Scale.JJAS
            and c.kind == MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE
        )
        assert cell.value == pytest.approx(0.5)  # gauge 1.0 - era5 0.5
        assert cell.unit == "mm/h"

    def test_below_freezing_station_reports_full_sub_freezing_mass_fraction(
        self,
    ) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        cell = next(
            c
            for c in report.station_magnitudes
            if isinstance(c, StationMagnitudeCell)
            and c.station == _B
            and c.scale == Scale.JJAS
            and c.kind == MagnitudeKind.CONDITIONAL_ACCUMULATED_DIFFERENCE
        )
        assert cell.mass_fraction.sub_freezing_mass_fraction == pytest.approx(1.0)


class TestRunMa6ComparisonBandMagnitudes:
    def test_band_value_is_the_unweighted_mean_of_its_present_members(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        below_700 = next(
            c
            for c in report.band_magnitudes
            if isinstance(c, BandMagnitudeCell)
            and str(c.band) == "< 700 m"
            and c.scale == Scale.JJAS
            and c.kind == MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE
        )
        assert below_700.station_count == 1
        assert below_700.mean_value == pytest.approx(0.5)

    def test_an_empty_band_renders_as_absent(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        mid_band = next(
            c
            for c in report.band_magnitudes
            if isinstance(c, AbsentResult)
            and c.label == "2,000-3,000 m"
            and c.scale == Scale.JJAS
            and c.kind == str(MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE)
        )
        assert "zero member stations" in mid_band.reason


class TestRunMa6ComparisonCategoricalAndSensitivities:
    def test_categorical_scores_present_at_daily_and_monthly_only(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        scales = {
            row.scale for row in report.categorical if not isinstance(row, AbsentResult)
        }
        assert scales == {Scale.DAILY, Scale.MONTHLY}

    def test_sensitivity_row_carries_exactly_five_combinations_sharing_n(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        row = next(
            r
            for r in report.sensitivities
            if isinstance(r, StationSensitivityRow)
            and r.station == _A
            and r.scale == Scale.JJAS
        )
        assert len(row.fractions) == 5
        ns = {f.n for f in row.fractions}
        assert ns == {row.fractions[0].n}

    def test_djf_sensitivity_is_absent_not_dropped(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        djf_rows = [r for r in report.sensitivities if r.scale == Scale.DJF]
        assert len(djf_rows) == 4  # A, B, Kirtipur, Khumaltar
        assert all(isinstance(r, AbsentResult) for r in djf_rows)


class TestRunMa6ComparisonRetentionAndExclusion:
    def test_retention_row_present_per_station(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        stations = {row.station for row in report.retention}
        assert stations == {_A, _B, KIRTIPUR, KHUMALTAR}

    def test_exclusion_list_carried_through_unmodified(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())
        assert report.exclusion_list == ()


class TestRunMa6ComparisonWithinCellPair:
    def test_computed_on_kirtipur_khumaltar_common_hours(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())

        pair = report.within_cell_pair
        assert pair.station_a == KIRTIPUR
        assert pair.station_b == KHUMALTAR
        assert pair.n_common_retained == 48
        assert pair.accumulated_difference_mm == pytest.approx((3.0 - 2.5) * 48)


class TestRenderingRefusalsAppearNeverVanish:
    """T6 P3 — a refusal renders. Every table-building function is checked
    directly on a report carrying `AbsentResult` rows."""

    def test_station_magnitude_table_renders_absent_marker(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())
        lines = "\n".join(_station_magnitude_table(report))

        assert "ABSENT" in lines
        assert "zero commonly-retained hours" in lines

    def test_band_magnitude_table_renders_absent_marker(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())
        lines = "\n".join(_band_magnitude_table(report))

        assert "ABSENT" in lines

    def test_sensitivity_table_renders_absent_marker_and_no_ranking_language(
        self,
    ) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())
        lines = "\n".join(_sensitivity_table(report))

        assert "ABSENT" in lines
        for banned in ("spread", "worse", "better", "dominant"):
            assert banned not in lines.lower()


class TestRetentionTableRendersMeasuredResult:
    def test_empty_exclusion_list_states_the_measured_worst_retention(self) -> None:
        accounting = (
            RemovalAccountingRow(
                station=_A,
                season=Season.JJAS,
                hour_of_day=0,
                category=RetentionCategory.RETAINED_NONMISSING,
                count=90,
            ),
            RemovalAccountingRow(
                station=_A,
                season=Season.JJAS,
                hour_of_day=0,
                category=RetentionCategory.QC_REMOVED,
                count=10,
            ),
        )
        report = run_ma6_comparison(_build_two_station_inputs(accounting=accounting))
        lines = "\n".join(_retention_table(report))

        assert "**Empty**" in lines
        assert "MEASURED result" in lines
        assert "0.9000" in lines  # 90 / (90 + 10)


class TestIdentitiesLines:
    def test_reports_all_three_consumed_identities(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())
        lines = "\n".join(_identities_lines(report))

        assert "reanalysis-era5-land" in lines
        assert "0001-precip-test" in lines
        assert "0001-t2m-test" in lines


class TestLapseTransectTable:
    def test_renders_empty_transect_without_crashing(self) -> None:
        report = run_ma6_comparison(_build_two_station_inputs())
        lines = _lapse_transect_table(report)

        assert lines[0].startswith("### (g)")


class TestWithinCellTableFormatting:
    def test_has_blank_line_separators_around_the_description(self) -> None:
        """Regression: an earlier draft filtered ALL empty strings from this
        table's lines, silently swallowing the intentional blank-line
        separators between the heading, the description and the bullets."""
        report = run_ma6_comparison(_build_two_station_inputs())
        lines = _within_cell_table(report)

        assert lines[0] == "### (f) Within-cell pair — Kirtipur/Khumaltar (D8, Exit 7)"
        assert lines[1] == ""
        assert lines[2].startswith("DESCRIPTIVE ONLY")
        assert lines[3] == ""


class TestBuildParser:
    def test_out_is_required(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_defaults_are_set(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--out", "/tmp/whatever"])
        assert args.precip_data_root is None
        assert str(args.t2m_data_root).endswith("era5_land_t2m")
        assert str(args.pyramid_dir).endswith("pyramid")
