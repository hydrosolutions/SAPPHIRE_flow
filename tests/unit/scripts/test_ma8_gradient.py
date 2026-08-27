"""Plan 205 (M-A8) task T2 — the apparent rain-phase gradient.

Seam tests: every assertion is on a VALUE actually produced, never on an
argument passed to a mock (`ma6_pairs`'s own convention, reused here)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ma6_lapse_check import TransectStation
from scripts.dhm_precip.ma8_gradient import (
    APPARENT_RAIN_PHASE_GRADIENT_LABEL,
    RAIN_SCREEN_THRESHOLDS_DEGC,
    ApparentRainPhaseGradient,
    DuplicateStationError,
    EmptyGroupError,
    GradientFitWindow,
    MixedThresholdError,
    NotSameElevationError,
    StationRainPhaseObservation,
    UnscreenedGradientRefusedError,
    compute_gradient_fit_window,
    fit_apparent_rain_phase_gradient,
    same_elevation_discrepancy,
    station_rain_phase_observation,
)

_STATION_A = Station("StationA")
_STATION_B = Station("StationB")
_WINDOW_START = datetime(2010, 1, 1)
_WINDOW_END = datetime(2015, 1, 1)


def _rr_frame(rows: list[tuple[datetime, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {"timestamp": [r[0] for r in rows], "value_mm": [r[1] for r in rows]}
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us")))


def _at_frame(rows: list[tuple[datetime, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {"timestamp": [r[0] for r in rows], "value_degc": [r[1] for r in rows]}
    ).with_columns(pl.col("timestamp").cast(pl.Datetime("us")))


def _hourly_jjas_rows(
    year: int, n_hours: int, value_fn
) -> list[tuple[datetime, float]]:
    base = datetime(year, 7, 1)
    return [(base + timedelta(hours=i), value_fn(i)) for i in range(n_hours)]


_TRANSECT_STATION = TransectStation(
    pyramid_station=_STATION_A,
    csv_filename="unused.csv",
    lat=27.0,
    lon=86.0,
    elevation_m=3000.0,
)


class TestStationRainPhaseObservation:
    def test_basic_computation(self) -> None:
        rr_rows = _hourly_jjas_rows(2012, 4, lambda i: 1.0 + i)
        at_rows = _hourly_jjas_rows(2012, 4, lambda i: 5.0)
        obs = station_rain_phase_observation(
            _TRANSECT_STATION,
            rr=_rr_frame(rr_rows),
            at=_at_frame(at_rows),
            window_start=_WINDOW_START,
            window_end=_WINDOW_END,
            threshold_degc=1.5,
            jjas_months=(6, 7, 8, 9),
        )
        assert obs.n_rain_phase_hours == 4
        assert obs.mean_hourly_intensity_mm_per_h is not None
        # all in the same hour-of-day bucket wouldn't occur here since each
        # row is a distinct hour -- just check it is the plain mean of a
        # single-hour-per-bucket population (4 distinct hours -> 4 buckets,
        # one value each -> equalised mean == arithmetic mean).
        assert obs.mean_hourly_intensity_mm_per_h == pytest.approx(2.5)

    def test_below_threshold_excluded(self) -> None:
        rr_rows = _hourly_jjas_rows(2012, 4, lambda i: 10.0)
        at_rows = [
            (datetime(2012, 7, 1, 0), 0.5),
            (datetime(2012, 7, 1, 1), 2.0),
            (datetime(2012, 7, 1, 2), 3.0),
            (datetime(2012, 7, 1, 3), -1.0),
        ]
        obs = station_rain_phase_observation(
            _TRANSECT_STATION,
            rr=_rr_frame(rr_rows),
            at=_at_frame(at_rows),
            window_start=_WINDOW_START,
            window_end=_WINDOW_END,
            threshold_degc=1.5,
            jjas_months=(6, 7, 8, 9),
        )
        # only hours 1 and 2 clear the >= 1.5 screen.
        assert obs.n_rain_phase_hours == 2

    def test_zero_rain_phase_hours_yields_none_mean(self) -> None:
        rr_rows = _hourly_jjas_rows(2012, 2, lambda i: 10.0)
        at_rows = [
            (datetime(2012, 7, 1, 0), -5.0),
            (datetime(2012, 7, 1, 1), -5.0),
        ]
        obs = station_rain_phase_observation(
            _TRANSECT_STATION,
            rr=_rr_frame(rr_rows),
            at=_at_frame(at_rows),
            window_start=_WINDOW_START,
            window_end=_WINDOW_END,
            threshold_degc=1.5,
            jjas_months=(6, 7, 8, 9),
        )
        assert obs.n_rain_phase_hours == 0
        assert obs.mean_hourly_intensity_mm_per_h is None

    def test_outside_window_excluded(self) -> None:
        rr_rows = _hourly_jjas_rows(2020, 2, lambda i: 10.0)  # outside window
        at_rows = _hourly_jjas_rows(2020, 2, lambda i: 5.0)
        obs = station_rain_phase_observation(
            _TRANSECT_STATION,
            rr=_rr_frame(rr_rows),
            at=_at_frame(at_rows),
            window_start=_WINDOW_START,
            window_end=_WINDOW_END,
            threshold_degc=1.5,
            jjas_months=(6, 7, 8, 9),
        )
        assert obs.n_rain_phase_hours == 0

    def test_outside_jjas_excluded(self) -> None:
        rr_rows = [(datetime(2012, 1, 15, 0), 10.0)]
        at_rows = [(datetime(2012, 1, 15, 0), 5.0)]
        obs = station_rain_phase_observation(
            _TRANSECT_STATION,
            rr=_rr_frame(rr_rows),
            at=_at_frame(at_rows),
            window_start=_WINDOW_START,
            window_end=_WINDOW_END,
            threshold_degc=1.5,
            jjas_months=(6, 7, 8, 9),
        )
        assert obs.n_rain_phase_hours == 0

    def test_unscreened_all_phase_gradient_is_refused(self) -> None:
        """Plan 205 T2's Verify: an unscreened (all-phase) gradient must be
        REFUSED, not computed. `-inf` would admit every temperature --
        i.e. no phase screening at all."""
        rr_rows = _hourly_jjas_rows(2012, 2, lambda i: 10.0)
        at_rows = _hourly_jjas_rows(2012, 2, lambda i: -50.0)
        with pytest.raises(UnscreenedGradientRefusedError):
            station_rain_phase_observation(
                _TRANSECT_STATION,
                rr=_rr_frame(rr_rows),
                at=_at_frame(at_rows),
                window_start=_WINDOW_START,
                window_end=_WINDOW_END,
                threshold_degc=float("-inf"),
                jjas_months=(6, 7, 8, 9),
            )

    def test_only_pinned_thresholds_accepted(self) -> None:
        for threshold in RAIN_SCREEN_THRESHOLDS_DEGC:
            rr_rows = _hourly_jjas_rows(2012, 2, lambda i: 10.0)
            at_rows = _hourly_jjas_rows(2012, 2, lambda i: 10.0)
            station_rain_phase_observation(
                _TRANSECT_STATION,
                rr=_rr_frame(rr_rows),
                at=_at_frame(at_rows),
                window_start=_WINDOW_START,
                window_end=_WINDOW_END,
                threshold_degc=threshold,
                jjas_months=(6, 7, 8, 9),
            )
        with pytest.raises(UnscreenedGradientRefusedError):
            station_rain_phase_observation(
                _TRANSECT_STATION,
                rr=_rr_frame(_hourly_jjas_rows(2012, 2, lambda i: 10.0)),
                at=_at_frame(_hourly_jjas_rows(2012, 2, lambda i: 10.0)),
                window_start=_WINDOW_START,
                window_end=_WINDOW_END,
                threshold_degc=3.0,
                jjas_months=(6, 7, 8, 9),
            )


class TestGradientFitWindow:
    def test_computes_intersection_of_remaining_stations(self) -> None:
        extents = {
            Station("S1"): (datetime(2000, 1, 1), datetime(2023, 1, 1)),
            Station("S2"): (datetime(2001, 1, 1), datetime(2023, 1, 1)),
            Station("S3"): (datetime(2009, 1, 1), datetime(2018, 5, 9)),
            Station("AWS0"): (datetime(1994, 1, 1), datetime(2005, 1, 1)),
        }
        window = compute_gradient_fit_window(
            extents, excluded={Station("AWS0"): "record ends 2005"}
        )
        assert window.start == datetime(2009, 1, 1)
        assert window.end == datetime(2018, 5, 9)
        assert set(window.fit_stations) == {Station("S1"), Station("S2"), Station("S3")}
        assert window.excluded_stations == (Station("AWS0"),)

    def test_excluding_everything_raises(self) -> None:
        extents = {Station("S1"): (datetime(2000, 1, 1), datetime(2023, 1, 1))}
        with pytest.raises(EmptyGroupError):
            compute_gradient_fit_window(extents, excluded={Station("S1"): "gone"})

    def test_no_overlap_raises(self) -> None:
        extents = {
            Station("S1"): (datetime(2000, 1, 1), datetime(2005, 1, 1)),
            Station("S2"): (datetime(2010, 1, 1), datetime(2015, 1, 1)),
        }
        with pytest.raises(ValueError, match="do not overlap"):
            compute_gradient_fit_window(extents, excluded={})

    def test_fit_and_excluded_overlap_refused(self) -> None:
        with pytest.raises(ValueError, match="both fit and excluded"):
            GradientFitWindow(
                start=datetime(2000, 1, 1),
                end=datetime(2001, 1, 1),
                fit_stations=(_STATION_A,),
                excluded_stations=(_STATION_A,),
                exclusion_reasons={_STATION_A: "x"},
            )

    def test_missing_exclusion_reason_refused(self) -> None:
        with pytest.raises(ValueError, match="no exclusion_reasons entry"):
            GradientFitWindow(
                start=datetime(2000, 1, 1),
                end=datetime(2001, 1, 1),
                fit_stations=(_STATION_A,),
                excluded_stations=(_STATION_B,),
                exclusion_reasons={},
            )


def _obs(
    station: Station, elev_m: float, mean: float | None, *, n: int = 100
) -> StationRainPhaseObservation:
    return StationRainPhaseObservation(
        station=station,
        elev_m=elev_m,
        threshold_degc=1.5,
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
        n_rain_phase_hours=n if mean is not None else 0,
        mean_hourly_intensity_mm_per_h=mean,
    )


class TestApparentRainPhaseGradient:
    def test_quantity_label_is_d4_mandatory_name(self) -> None:
        assert (
            ApparentRainPhaseGradient.quantity_label
            == "apparent rain-phase gradient, uncorrected for wind catch"
        )
        assert (
            ApparentRainPhaseGradient.quantity_label
            == APPARENT_RAIN_PHASE_GRADIENT_LABEL
        )
        assert ApparentRainPhaseGradient.quantity_label != "the precipitation gradient"
        assert (
            ApparentRainPhaseGradient.quantity_label != "the precipitation lapse rate"
        )

    def test_ols_recovers_a_known_exact_log_linear_relationship(self) -> None:
        # y_i = 10 * exp(-0.1 * elev_km) -- an exact (noiseless) relationship,
        # so OLS must recover slope=-0.1 exactly.
        slope_true = -0.1
        stations = [
            (Station(f"S{i}"), elev_km * 1000.0)
            for i, elev_km in enumerate([1.0, 2.0, 3.0, 4.0, 5.0])
        ]
        observations = tuple(
            _obs(station, elev_m, 10.0 * math.exp(slope_true * (elev_m / 1000.0)))
            for station, elev_m in stations
        )
        gradient = ApparentRainPhaseGradient(
            threshold_degc=1.5, observations=observations
        )
        assert gradient.n_stations_in_fit == 5
        assert gradient.slope_log_per_km == pytest.approx(slope_true, abs=1e-9)
        expected_pct = (math.exp(slope_true) - 1.0) * 100.0
        assert gradient.percent_per_km == pytest.approx(expected_pct, abs=1e-6)
        lo, hi = gradient.percent_per_km_ci95
        # noiseless fit -> zero standard error -> CI collapses to the point estimate.
        assert lo == pytest.approx(expected_pct, abs=1e-6)
        assert hi == pytest.approx(expected_pct, abs=1e-6)

    def test_zero_mean_stations_excluded_from_fit(self) -> None:
        observations = (
            _obs(Station("S1"), 1000.0, 1.0),
            _obs(Station("S2"), 2000.0, 2.0),
            _obs(Station("S3"), 3000.0, None),  # zero rain-phase hours
            _obs(Station("S4"), 4000.0, 4.0),
        )
        gradient = ApparentRainPhaseGradient(
            threshold_degc=1.5, observations=observations
        )
        assert gradient.n_stations_in_fit == 3
        assert Station("S3") not in {o.station for o in gradient.fit_observations}
        # but the zero-hour station is still carried in `observations`
        # (the rain line stays visible).
        assert Station("S3") in {o.station for o in gradient.observations}

    def test_unscreened_threshold_refused(self) -> None:
        with pytest.raises(UnscreenedGradientRefusedError):
            ApparentRainPhaseGradient(
                threshold_degc=float("-inf"),
                observations=(_obs(Station("S1"), 1000.0, 1.0),),
            )

    def test_empty_observations_refused(self) -> None:
        with pytest.raises(EmptyGroupError):
            ApparentRainPhaseGradient(threshold_degc=1.5, observations=())

    def test_duplicate_station_refused(self) -> None:
        obs = _obs(Station("S1"), 1000.0, 1.0)
        with pytest.raises(DuplicateStationError):
            ApparentRainPhaseGradient(threshold_degc=1.5, observations=(obs, obs))

    def test_mixed_threshold_refused(self) -> None:
        wrong = StationRainPhaseObservation(
            station=Station("S2"),
            elev_m=2000.0,
            threshold_degc=2.0,
            window_start=_WINDOW_START,
            window_end=_WINDOW_END,
            n_rain_phase_hours=10,
            mean_hourly_intensity_mm_per_h=1.0,
        )
        right = _obs(Station("S1"), 1000.0, 1.0)
        with pytest.raises(MixedThresholdError):
            ApparentRainPhaseGradient(threshold_degc=1.5, observations=(right, wrong))

    def test_fewer_than_three_fit_stations_raises_on_slope_access(self) -> None:
        observations = (
            _obs(Station("S1"), 1000.0, 1.0),
            _obs(Station("S2"), 2000.0, None),
        )
        gradient = ApparentRainPhaseGradient(
            threshold_degc=1.5, observations=observations
        )
        with pytest.raises(ValueError, match="at least 3"):
            _ = gradient.slope_log_per_km

    def test_fit_apparent_rain_phase_gradient_wiring(self) -> None:
        stations = tuple(
            TransectStation(
                pyramid_station=Station(f"S{i}"),
                csv_filename="unused.csv",
                lat=27.0,
                lon=86.0,
                elevation_m=elev_km * 1000.0,
            )
            for i, elev_km in enumerate([1.0, 2.0, 3.0])
        )
        rr_by_station = {
            s.pyramid_station: _rr_frame(_hourly_jjas_rows(2012, 4, lambda i: 5.0))
            for s in stations
        }
        at_by_station = {
            s.pyramid_station: _at_frame(_hourly_jjas_rows(2012, 4, lambda i: 5.0))
            for s in stations
        }
        window = GradientFitWindow(
            start=_WINDOW_START,
            end=_WINDOW_END,
            fit_stations=tuple(s.pyramid_station for s in stations),
            excluded_stations=(),
            exclusion_reasons={},
        )
        gradient = fit_apparent_rain_phase_gradient(
            stations,
            rr_by_station=rr_by_station,
            at_by_station=at_by_station,
            window=window,
            threshold_degc=1.5,
            jjas_months=(6, 7, 8, 9),
        )
        assert gradient.n_stations_in_fit == 3
        for obs in gradient.observations:
            assert obs.n_rain_phase_hours == 4


class TestSameElevationDiscrepancy:
    def test_ratios_computed_from_real_join(self) -> None:
        station_a = TransectStation(
            pyramid_station=Station("AWS0"),
            csv_filename="a.csv",
            lat=27.0,
            lon=86.0,
            elevation_m=5035.0,
        )
        station_b = TransectStation(
            pyramid_station=Station("AWS1"),
            csv_filename="b.csv",
            lat=27.0,
            lon=86.0,
            elevation_m=5035.0,
        )
        base = datetime(2001, 7, 1)
        timestamps = [base + timedelta(hours=i) for i in range(6)]
        rr_a = pl.DataFrame(
            {"timestamp": timestamps, "value_mm": [0.0, 1.0, 0.0, 2.0, 0.0, 3.0]}
        )
        rr_b = pl.DataFrame(
            {"timestamp": timestamps, "value_mm": [0.0, 1.0, 1.0, 2.0, 0.0, 0.0]}
        )
        at_a = pl.DataFrame(
            {"timestamp": timestamps, "value_degc": [5.0, 5.0, 5.0, -5.0, 5.0, 5.0]}
        )
        at_b = pl.DataFrame(
            {"timestamp": timestamps, "value_degc": [5.0, 5.0, 5.0, 5.0, 5.0, 5.0]}
        )
        result = same_elevation_discrepancy(
            station_a, station_b, rr_a=rr_a, rr_b=rr_b, at_a=at_a, at_b=at_b
        )
        assert result.n_common_retained == 6
        # wet (>0) hours: a has 3 (idx 1,3,5), b has 3 (idx 1,2,3).
        assert result.wet_hour_count_a == 3
        assert result.wet_hour_count_b == 3
        assert result.wet_hour_count_ratio == pytest.approx(1.0)
        # jointly rain-screened (both AT >= 1.5): idx 0,1,2,4,5
        # (idx 3 excluded, a is -5).
        assert result.n_rain_screened_common == 5
        assert result.rain_amount_a_mm == pytest.approx(0.0 + 1.0 + 0.0 + 0.0 + 3.0)
        assert result.rain_amount_b_mm == pytest.approx(0.0 + 1.0 + 1.0 + 0.0 + 0.0)
        assert result.rain_amount_ratio == pytest.approx(4.0 / 2.0)

    def test_different_elevation_refused(self) -> None:
        station_a = TransectStation(
            pyramid_station=Station("AWS0"),
            csv_filename="a.csv",
            lat=27.0,
            lon=86.0,
            elevation_m=5035.0,
        )
        station_b = TransectStation(
            pyramid_station=Station("AWS3"),
            csv_filename="b.csv",
            lat=27.0,
            lon=86.0,
            elevation_m=2660.0,
        )
        empty_rr = pl.DataFrame(
            {"timestamp": pl.Series([], dtype=pl.Datetime("us")), "value_mm": []}
        )
        empty_at = pl.DataFrame(
            {"timestamp": pl.Series([], dtype=pl.Datetime("us")), "value_degc": []}
        )
        with pytest.raises(NotSameElevationError):
            same_elevation_discrepancy(
                station_a,
                station_b,
                rr_a=empty_rr,
                rr_b=empty_rr,
                at_a=empty_at,
                at_b=empty_at,
            )

    def test_zero_common_hours_refused(self) -> None:
        station_a = TransectStation(
            pyramid_station=Station("AWS0"),
            csv_filename="a.csv",
            lat=27.0,
            lon=86.0,
            elevation_m=5035.0,
        )
        station_b = TransectStation(
            pyramid_station=Station("AWS1"),
            csv_filename="b.csv",
            lat=27.0,
            lon=86.0,
            elevation_m=5035.0,
        )
        rr_a = pl.DataFrame({"timestamp": [datetime(2001, 1, 1)], "value_mm": [1.0]})
        rr_b = pl.DataFrame({"timestamp": [datetime(2002, 1, 1)], "value_mm": [1.0]})
        at = pl.DataFrame({"timestamp": [datetime(2001, 1, 1)], "value_degc": [5.0]})
        with pytest.raises(EmptyGroupError):
            same_elevation_discrepancy(
                station_a, station_b, rr_a=rr_a, rr_b=rr_b, at_a=at, at_b=at
            )
