"""Plan 205 (M-A8) task T3 — the report and Exit.

Seam tests: assertions land on VALUES actually produced (a value, a
refusal's reason, a headline number) — the same discipline
`test_ma8_confound.py`/`test_ma8_gradient.py` already establish. T3's own
job is WIRING (pulling the right number from the right place, propagating
refusals, attaching companions), not re-deriving T1/T2's math — so most
assertions here compare `run_ma8_report`'s output against a DIRECT call to
the same T1/T2 function the report is supposed to be using, rather than a
hand-derived expected value.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import DatumReconciliationStatus, Station
from scripts.dhm_precip.ma6_estimands import (
    ElevationBand,
    matched_hour_mean_difference,
    scale_subset,
)
from scripts.dhm_precip.ma6_mass_fraction import (
    StationElevationInputs,
    build_sub_freezing_mass_fraction,
)
from scripts.dhm_precip.ma6_pairs import MaskedGaugeSeries, PairedSeries, Scale
from scripts.dhm_precip.ma6_run import AbsentResult, MagnitudeKind, StationMagnitudeCell
from scripts.dhm_precip.ma7_intensity import (
    StationIntensityDistribution,
    bootstrap_station_quantile,
)
from scripts.dhm_precip.ma7_run import StationSeasonIntensity
from scripts.dhm_precip.ma8_confound import (
    GroupElevationObservation,
    StationClassification,
    WithinGroupElevationRelationship,
    within_group_elevation_relationship,
)
from scripts.dhm_precip.ma8_gradient import (
    AWS0,
    AWS1,
    RAIN_SCREEN_THRESHOLDS_DEGC,
    RR_TRANSECT_STATIONS,
    ApparentRainPhaseGradient,
    SameElevationDiscrepancy,
)
from scripts.dhm_precip.ma8_run import (
    AWS0_EXCLUSION_REASON,
    BIAS_ESTIMAND_LABEL,
    TAIL_ESTIMAND_LABEL,
    Ma8Inputs,
    Ma8InputsConsistencyError,
    Refusal,
    _write_report,
    run_ma8_report,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.seasons import Season

if TYPE_CHECKING:
    from pathlib import Path

    from scripts.dhm_precip.ma7_transfer import ResolutionGroupLabel

# --- fixtures ---

_B1 = Station("B1")
_B2 = Station("B2")
_B3 = Station("B3")
_A1 = Station("A1")
_A2 = Station("A2")

_B1_ELEV = 100.0
_B2_ELEV = 1500.0
_B3_ELEV = 2100.0
_A1_ELEV = 2500.0
_A2_ELEV = 3200.0

_CLASSIFICATIONS = (
    StationClassification(
        station=_B1, group="B", band=ElevationBand.BELOW_700M, elev_m=_B1_ELEV
    ),
    StationClassification(
        station=_B2, group="B", band=ElevationBand.B700_2000M, elev_m=_B2_ELEV
    ),
    StationClassification(
        station=_B3, group="B", band=ElevationBand.B2000_3000M, elev_m=_B3_ELEV
    ),
    StationClassification(
        station=_A1, group="A", band=ElevationBand.B2000_3000M, elev_m=_A1_ELEV
    ),
    StationClassification(
        station=_A2, group="A", band=ElevationBand.ABOVE_3000M, elev_m=_A2_ELEV
    ),
)

_JJAS_START = datetime(2022, 7, 1)
_ELEV_INPUTS = StationElevationInputs(
    station_elev_m=1000.0,
    orography_elev_m=1000.0,
    datum_reconciled=DatumReconciliationStatus.UNRECONCILED,
)


def _paired_series(
    station: Station, gauge: list[float], era5: list[float]
) -> PairedSeries:
    n = len(gauge)
    ts = [_JJAS_START + timedelta(hours=i) for i in range(n)]
    frame = pl.DataFrame(
        {"timestamp": ts, "gauge_value_mm": gauge, "era5_nearest_mm_per_h": era5}
    ).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us")),
        pl.lit(str(station)).alias("station"),
    )
    return PairedSeries(frame=frame)


def _t2m_frame(station: Station, n: int, temp_degc: float) -> pl.DataFrame:
    ts = [_JJAS_START + timedelta(hours=i) for i in range(n)]
    return pl.DataFrame(
        {
            "station": [str(station)] * n,
            "timestamp": ts,
            "grid_t2m_degc": [temp_degc] * n,
        }
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us")))


def _bias_cell(
    station: Station, gauge: list[float], era5: list[float]
) -> StationMagnitudeCell:
    paired = _paired_series(station, gauge, era5)
    sub = scale_subset(paired, scale=Scale.JJAS, params=DEFAULT_PARAMS)
    estimand = matched_hour_mean_difference(sub)
    mass_fraction = build_sub_freezing_mass_fraction(
        estimand,
        t2m_by_station={station: _t2m_frame(station, len(gauge), 10.0)},
        elevations_by_station={station: _ELEV_INPUTS},
    )
    return StationMagnitudeCell(
        kind=MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE,
        estimand=estimand,
        mass_fraction=mass_fraction,
    )


def _intensity(
    station: Station, wet_values: list[float]
) -> StationIntensityDistribution:
    values = [0.0] * 20 + wet_values
    rows = [
        {"timestamp": _JJAS_START + timedelta(hours=i), "value_mm": v}
        for i, v in enumerate(values)
    ]
    frame = pl.DataFrame(rows).with_columns(
        pl.lit(str(station)).alias("station"),
        pl.col("timestamp").cast(pl.Datetime("us")),
    )
    series = MaskedGaugeSeries(frame=frame.select("station", "timestamp", "value_mm"))
    return StationIntensityDistribution(series=series, season=Season.JJAS)


def _station_season_intensity(
    station: Station, wet_values: list[float]
) -> StationSeasonIntensity:
    dist = _intensity(station, wet_values)
    q50 = bootstrap_station_quantile(
        dist,
        quantile=0.5,
        rng=random.Random(1),
        n_resamples=50,
        min_season_years_for_adequacy=5,
    )
    q99 = bootstrap_station_quantile(
        dist,
        quantile=0.99,
        rng=random.Random(1),
        n_resamples=50,
        min_season_years_for_adequacy=5,
    )
    return StationSeasonIntensity(
        distribution=dist, q50_bootstrap=q50, q99_bootstrap=q99
    )


# Group B: B2's magnitude cell is deliberately ABSENT (refused) -- proves
# D2's explicit-absence rendering AND its exclusion from the correlation's
# member count (n=2 for B1/B3 alone, below `_PEARSON_R_MIN_STATIONS=3`, so
# the bias relationship comes back a `Refusal`). B2's INTENSITY stays
# present, so the tail relationship (n=3) stays a real correlation --
# proving the two quantities are independently wired, not coupled.
_STATION_MAGNITUDE: dict[Station, StationMagnitudeCell | AbsentResult] = {
    _B1: _bias_cell(_B1, gauge=[0.1] * 10, era5=[0.05] * 10),
    _B2: AbsentResult(
        kind=str(MagnitudeKind.MATCHED_HOUR_MEAN_DIFFERENCE),
        scale=Scale.JJAS,
        label=str(_B2),
        reason="synthetic refusal for the seam test",
    ),
    _B3: _bias_cell(_B3, gauge=[0.3] * 10, era5=[0.1] * 10),
    _A1: _bias_cell(_A1, gauge=[0.2] * 10, era5=[0.15] * 10),
    _A2: _bias_cell(_A2, gauge=[0.4] * 10, era5=[0.2] * 10),
}

_STATION_INTENSITY: dict[Station, StationSeasonIntensity] = {
    _B1: _station_season_intensity(_B1, [0.2, 0.5, 1.0, 2.0]),
    _B2: _station_season_intensity(_B2, [0.2, 0.5, 1.0, 5.0]),
    _B3: _station_season_intensity(_B3, [0.2, 0.5, 1.0, 20.0]),
    _A1: _station_season_intensity(_A1, [0.2, 0.5, 1.0, 8.0]),
    _A2: _station_season_intensity(_A2, [0.2, 0.5, 1.0, 12.0]),
}

_STATION_GROUP: dict[Station, ResolutionGroupLabel] = {
    c.station: c.group for c in _CLASSIFICATIONS
}


def _present_q99(station: Station) -> float:
    q99 = _STATION_INTENSITY[station].distribution.q99_mm_per_h
    assert q99 is not None
    return q99


def _present_cell(
    magnitude: dict[Station, StationMagnitudeCell | AbsentResult], station: Station
) -> StationMagnitudeCell:
    cell = magnitude[station]
    assert isinstance(cell, StationMagnitudeCell)
    return cell


# --- Pyramid RR/AT: a clean, monotonically-declining-with-elevation rain
# constant per station, comfortably above every rain-screen threshold
# (RAIN_SCREEN_THRESHOLDS_DEGC tops out at 4.0), one hour per hour-of-day so
# the hour-of-day-equalised mean equals the constant exactly. ---

_STATION_RAIN_CONSTANT_MM: dict[Station, float] = {
    Station("AWS3 Lukla"): 5.0,
    Station("AWS5 Namche"): 4.0,
    Station("AWS2 Pheriche"): 3.0,
    Station("AWS0 Pyramid"): 2.0,
    Station("AWS1 Pyramid"): 2.2,
    Station("AWS4 Kala Patthar"): 1.5,
}


def _pyramid_rr_at() -> tuple[dict[Station, pl.DataFrame], dict[Station, pl.DataFrame]]:
    rr_by_station: dict[Station, pl.DataFrame] = {}
    at_by_station: dict[Station, pl.DataFrame] = {}
    for transect_station in RR_TRANSECT_STATIONS:
        station = transect_station.pyramid_station
        constant = _STATION_RAIN_CONSTANT_MM[station]
        ts = [_JJAS_START + timedelta(hours=i) for i in range(24)]
        rr_by_station[station] = pl.DataFrame(
            {"timestamp": ts, "value_mm": [constant] * 24}
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("us")))
        at_by_station[station] = pl.DataFrame(
            {"timestamp": ts, "value_degc": [6.0] * 24}
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("us")))
    return rr_by_station, at_by_station


_RR_BY_STATION, _AT_BY_STATION = _pyramid_rr_at()

_PROVENANCE = ("test provenance line",)


def _inputs(**overrides: object) -> Ma8Inputs:
    kwargs: dict[str, object] = {
        "classifications": _CLASSIFICATIONS,
        "station_magnitude": _STATION_MAGNITUDE,
        "station_intensity": _STATION_INTENSITY,
        "ma7_seed": 193,
        "rr_transect_stations": RR_TRANSECT_STATIONS,
        "rr_by_station": _RR_BY_STATION,
        "at_by_station": _AT_BY_STATION,
        "provenance_lines": _PROVENANCE,
        "params": DEFAULT_PARAMS,
    }
    kwargs.update(overrides)
    return Ma8Inputs(**kwargs)  # type: ignore[arg-type]


_FIXED_CLOCK = lambda: datetime(2026, 8, 27, tzinfo=UTC)  # noqa: E731


class TestMa8InputsConsistency:
    def test_empty_classifications_refused(self) -> None:
        with pytest.raises(Ma8InputsConsistencyError, match="empty"):
            _inputs(classifications=())

    def test_missing_station_magnitude_refused(self) -> None:
        magnitude = dict(_STATION_MAGNITUDE)
        del magnitude[_B1]
        with pytest.raises(Ma8InputsConsistencyError, match="station_magnitude"):
            _inputs(station_magnitude=magnitude)

    def test_missing_station_intensity_refused(self) -> None:
        intensity = dict(_STATION_INTENSITY)
        del intensity[_A2]
        with pytest.raises(Ma8InputsConsistencyError, match="station_intensity"):
            _inputs(station_intensity=intensity)

    def test_missing_rr_refused(self) -> None:
        rr = dict(_RR_BY_STATION)
        del rr[AWS0.pyramid_station]
        with pytest.raises(Ma8InputsConsistencyError, match="rr_by_station"):
            _inputs(rr_by_station=rr)

    def test_missing_at_refused(self) -> None:
        at = dict(_AT_BY_STATION)
        del at[AWS0.pyramid_station]
        with pytest.raises(Ma8InputsConsistencyError, match="at_by_station"):
            _inputs(at_by_station=at)


class TestGroupStructure:
    def test_group_ranges_and_between_group_statement(self) -> None:
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        by_group = {r.group: r for r in report.group_ranges}
        assert by_group["B"].min_elev_m == _B1_ELEV
        assert by_group["B"].max_elev_m == _B3_ELEV
        assert by_group["A"].min_elev_m == _A1_ELEV
        assert by_group["A"].max_elev_m == _A2_ELEV
        assert "UNIDENTIFIED" in report.between_group_statement.declined_attribution

    def test_cross_tabulation_cell_counts(self) -> None:
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        cells = {(c.group, c.band): c.station_count for c in report.cross_tabulation}
        assert cells[("B", ElevationBand.BELOW_700M)] == 1
        assert cells[("B", ElevationBand.B700_2000M)] == 1
        assert cells[("B", ElevationBand.B2000_3000M)] == 1
        assert cells[("A", ElevationBand.B2000_3000M)] == 1
        assert cells[("A", ElevationBand.ABOVE_3000M)] == 1

    def test_group_a_members_reported_without_a_fitted_relationship(self) -> None:
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        assert {m.station for m in report.group_a_members} == {_A1, _A2}


class TestWithinGroupBRelationships:
    def test_bias_relationship_refused_when_fewer_than_three_present(self) -> None:
        """B2's magnitude cell is an `AbsentResult` -- only B1/B3 remain,
        below `_PEARSON_R_MIN_STATIONS=3` -- so the bias relationship must
        come back a `Refusal`, never crash and never silently drop to a
        2-station "correlation"."""
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        assert isinstance(report.group_b_bias_relationship, Refusal)

    def test_tail_relationship_uses_q99_and_elevation_off_the_real_source(self) -> None:
        """T3's own job: pull `q99_mm_per_h`/`n_wet_retained` off the SAME
        `StationSeasonIntensity` objects the report was given, not
        recompute them. Reconstructed directly against `ma8_confound`'s own
        `within_group_elevation_relationship`."""
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        expected_obs = tuple(
            GroupElevationObservation(
                station=s,
                elev_m=elev,
                value=_present_q99(s),
                n=_STATION_INTENSITY[s].distribution.n_wet_retained,
            )
            for s, elev in ((_B1, _B1_ELEV), (_B2, _B2_ELEV), (_B3, _B3_ELEV))
        )
        expected = within_group_elevation_relationship(
            TAIL_ESTIMAND_LABEL, "B", expected_obs, station_group=_STATION_GROUP
        )
        assert isinstance(
            report.group_b_tail_relationship, WithinGroupElevationRelationship
        )
        assert report.group_b_tail_relationship.pearson_r == pytest.approx(
            expected.pearson_r
        )
        assert report.group_b_tail_relationship.n_stations == 3

    def test_bias_relationship_computed_when_all_present(self) -> None:
        """A second population where every Group B station has a present
        magnitude cell -- proves the relationship DOES compute (not just
        that it refuses) and matches the value read straight off each
        cell's own `.value`/`.n`."""
        magnitude = dict(_STATION_MAGNITUDE)
        magnitude[_B2] = _bias_cell(_B2, gauge=[0.2] * 10, era5=[0.1] * 10)
        report = run_ma8_report(
            inputs=_inputs(station_magnitude=magnitude), clock=_FIXED_CLOCK
        )
        expected_obs = tuple(
            GroupElevationObservation(
                station=s,
                elev_m=elev,
                value=_present_cell(magnitude, s).value,
                n=_present_cell(magnitude, s).n,
            )
            for s, elev in ((_B1, _B1_ELEV), (_B2, _B2_ELEV), (_B3, _B3_ELEV))
        )
        expected = within_group_elevation_relationship(
            BIAS_ESTIMAND_LABEL, "B", expected_obs, station_group=_STATION_GROUP
        )
        assert isinstance(
            report.group_b_bias_relationship, WithinGroupElevationRelationship
        )
        assert report.group_b_bias_relationship.pearson_r == pytest.approx(
            expected.pearson_r
        )


class TestBandSplit:
    def test_band_split_includes_both_groups_2000_3000m(self) -> None:
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        assert not isinstance(report.band_split, Refusal)
        members = {m.station: m for m in report.band_split.members}
        assert set(members) == {_B3, _A1}
        assert members[_B3].group == "B"
        assert members[_A1].group == "A"
        assert (
            members[_B3].q99_mm_per_h
            == _STATION_INTENSITY[_B3].distribution.q99_mm_per_h
        )

    def test_band_split_refused_when_band_has_no_members(self) -> None:
        classifications = tuple(
            c for c in _CLASSIFICATIONS if c.band is not ElevationBand.B2000_3000M
        )
        report = run_ma8_report(
            inputs=_inputs(classifications=classifications), clock=_FIXED_CLOCK
        )
        assert isinstance(report.band_split, Refusal)


class TestGradient:
    def test_window_excludes_aws0_by_name(self) -> None:
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        window = report.gradient_window
        assert AWS0.pyramid_station not in window.fit_stations
        assert AWS0.pyramid_station in window.excluded_stations
        assert window.exclusion_reasons[AWS0.pyramid_station] == AWS0_EXCLUSION_REASON
        assert AWS1.pyramid_station in window.fit_stations

    def test_every_threshold_leg_present(self) -> None:
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        assert set(report.gradients_by_threshold) == set(RAIN_SCREEN_THRESHOLDS_DEGC)

    def test_gradient_declines_with_elevation_at_every_screen(self) -> None:
        """The fixture's rain constants strictly decrease with elevation
        (5.0 -> 1.5 mm/h) -- every threshold leg clears (AT=6 degC on every
        hour), so every leg's `percent_per_km` must come back negative."""
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        for threshold in RAIN_SCREEN_THRESHOLDS_DEGC:
            outcome = report.gradients_by_threshold[threshold]
            assert isinstance(outcome, ApparentRainPhaseGradient)
            assert outcome.percent_per_km < 0.0

    def test_gradient_refused_when_at_never_clears_the_screen(self) -> None:
        at_by_station = {
            station: frame.with_columns(pl.lit(-10.0).alias("value_degc"))
            for station, frame in _AT_BY_STATION.items()
        }
        report = run_ma8_report(
            inputs=_inputs(at_by_station=at_by_station), clock=_FIXED_CLOCK
        )
        for threshold in RAIN_SCREEN_THRESHOLDS_DEGC:
            assert isinstance(report.gradients_by_threshold[threshold], Refusal)


class TestSameElevationDiscrepancy:
    def test_aws0_aws1_ratios_computed_from_the_same_rr_at(self) -> None:
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        same = report.same_elevation
        assert isinstance(same, SameElevationDiscrepancy)
        assert same.wet_hour_count_ratio == pytest.approx(1.0)  # both wet every hour
        expected_amount_ratio = (
            _STATION_RAIN_CONSTANT_MM[AWS0.pyramid_station]
            / _STATION_RAIN_CONSTANT_MM[AWS1.pyramid_station]
        )
        assert same.rain_amount_ratio == pytest.approx(expected_amount_ratio)


class TestHeadlines:
    def test_headlines_mirror_the_report_components(self) -> None:
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        h = report.headlines
        assert h.within_group_b_pearson_r_bias is None  # B2 absent -> Refusal
        assert isinstance(
            report.group_b_tail_relationship, WithinGroupElevationRelationship
        )
        assert h.within_group_b_pearson_r_tail == pytest.approx(
            report.group_b_tail_relationship.pearson_r
        )
        assert h.fit_window_start == report.gradient_window.start
        assert h.fit_window_end == report.gradient_window.end
        assert set(h.gradient_percent_per_km_by_threshold) == set(
            RAIN_SCREEN_THRESHOLDS_DEGC
        )
        assert isinstance(report.same_elevation, SameElevationDiscrepancy)
        assert h.aws0_aws1_wet_hour_count_ratio == pytest.approx(
            report.same_elevation.wet_hour_count_ratio
        )


class TestDeterminism:
    def test_two_runs_on_the_same_inputs_produce_identical_headlines(self) -> None:
        """D8 -- report determinism is an exit condition. Two calls to the
        pure core on the SAME `Ma8Inputs` (different `clock`) must produce
        bit-identical headline numbers."""
        report_1 = run_ma8_report(
            inputs=_inputs(), clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)
        )
        report_2 = run_ma8_report(
            inputs=_inputs(), clock=lambda: datetime(2026, 2, 2, tzinfo=UTC)
        )
        assert report_1.headlines == report_2.headlines


class TestWriteReport:
    def test_report_contains_every_q1_section_and_the_mandatory_gradient_name(
        self, tmp_path: Path
    ) -> None:
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        out = tmp_path / "ma8_report.md"
        _write_report(out, report)
        text = out.read_text()
        assert text  # non-empty
        for marker in (
            "## (a)",
            "## (b)",
            "## (c)",
            "## (d)",
            "## (e)",
            "## (f)",
            "## (g)",
        ):
            assert marker in text
        assert "apparent rain-phase gradient, uncorrected for wind catch" in text
        assert "the precipitation gradient" not in text.lower().replace(
            "apparent rain-phase gradient, uncorrected for wind catch", ""
        )

    def test_refused_bias_relationship_renders_as_explicit_absence(
        self, tmp_path: Path
    ) -> None:
        report = run_ma8_report(inputs=_inputs(), clock=_FIXED_CLOCK)
        out = tmp_path / "ma8_report.md"
        _write_report(out, report)
        text = out.read_text()
        assert "refused —" in text
        assert "synthetic refusal for the seam test" in text
