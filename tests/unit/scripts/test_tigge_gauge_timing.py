"""Plan 216 (M-A11) T2 — locked red-first against the spec:
- "Where two initialisations yield a forecast at the same valid time, take
  the most recent initialisation within the band, deterministically —
  never both, never an average."
- "A window with any missing gauge hour is dropped, never partially
  summed."
- D5: "each station's cycle sums to 100%" (per-station normalisation) and
  the reported band figure is the station-EQUAL median, never a pooled
  sum of raw mass across stations.

No network, no real gauge workbook — every test is against small synthetic
frames, mirroring `tests/unit/scripts/test_era5_deaccumulate.py`.
"""

from __future__ import annotations

import inspect

import polars as pl
import pytest

import scripts.dhm_precip.diurnal_phase as diurnal_phase
import scripts.dhm_precip.tigge_gauge_timing as tigge_gauge_timing
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.tigge_gauge_timing import (
    PhaseEstimate,
    _gauge_window_lookup,
    dedup_most_recent_init,
    estimate_phase,
    estimate_station_phase,
    restrict_to_pinned_season,
    station_day_count,
)


def _row(
    *, station: str, valid_time: str, gauge_mm: float, tigge_mm: float
) -> dict[str, object]:
    return {
        "station": station,
        "valid_time_utc": valid_time,
        "gauge_mm": gauge_mm,
        "tigge_mm": tigge_mm,
    }


def _paired_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("valid_time_utc").str.to_datetime())


class TestCleanCheckoutImport:
    """Locks the blocker: unit-test collection (and CI, which never
    provisions `data/`) must never require the gitignored M-A6 output
    artefact `data/dhm_precip/figures/era5-timing/era5_gauge_timing_figure.py`
    to exist on disk. Reproduced pre-fix: moving that file aside made
    `pytest tests/unit/scripts/test_tigge_gauge_timing.py` fail at
    COLLECTION with `FileNotFoundError` before a single test ran."""

    def test_module_never_references_the_gitignored_data_tree(self) -> None:
        source = inspect.getsource(tigge_gauge_timing)
        assert "data/dhm_precip/figures" not in source
        assert "importlib.util" not in source  # no dynamic by-path loading

    def test_phase_estimator_is_imported_from_the_tracked_package_module(
        self,
    ) -> None:
        # `diurnal_phase.py` lives under `scripts/dhm_precip/` — a normal,
        # tracked package import, never a `data/`-relative path.
        module_path = diurnal_phase.__file__.replace("\\", "/")
        assert "scripts/dhm_precip/diurnal_phase.py" in module_path
        assert "/data/" not in module_path
        assert tigge_gauge_timing.band_of is diurnal_phase.band_of
        assert tigge_gauge_timing.harmonic_phase_h is diurnal_phase.harmonic_phase_h


class TestDedupMostRecentInit:
    def test_keeps_only_the_most_recent_init_at_a_shared_valid_time(self) -> None:
        # Two inits both reach 2025-06-02 00:00 within band D+1's exact
        # step set: 2025-06-01 00Z step24, and 2025-05-31 12Z step36.
        series = pl.DataFrame(
            {
                "station": ["A", "A"],
                "init_time_utc": [
                    "2025-06-01T00:00:00",
                    "2025-05-31T12:00:00",
                ],
                "ending_lead_hours": [24, 36],
                "valid_time_utc": [
                    "2025-06-02T00:00:00",
                    "2025-06-02T00:00:00",
                ],
                "tigge_mm": [5.0, 9.0],
            }
        ).with_columns(
            pl.col("init_time_utc").str.to_datetime(),
            pl.col("valid_time_utc").str.to_datetime(),
        )
        out = dedup_most_recent_init(series, band_steps=(24, 30, 36, 42))
        assert out.height == 1  # never both
        row = out.row(0, named=True)
        assert (
            row["tigge_mm"] == 5.0
        )  # the MORE RECENT init (June 1 00Z), never averaged
        assert str(row["init_time_utc"]) == "2025-06-01 00:00:00"

    def test_restricts_to_the_bands_exact_steps(self) -> None:
        series = pl.DataFrame(
            {
                "station": ["A", "A"],
                "init_time_utc": ["2025-06-01T00:00:00", "2025-06-01T00:00:00"],
                "ending_lead_hours": [18, 24],  # 18h is D+0-territory, not in D+1
                "valid_time_utc": ["2025-06-01T18:00:00", "2025-06-02T00:00:00"],
                "tigge_mm": [1.0, 2.0],
            }
        ).with_columns(
            pl.col("init_time_utc").str.to_datetime(),
            pl.col("valid_time_utc").str.to_datetime(),
        )
        out = dedup_most_recent_init(series, band_steps=(24, 30, 36, 42))
        assert out.height == 1
        assert out.row(0, named=True)["ending_lead_hours"] == 24

    def test_distinct_valid_times_are_both_kept(self) -> None:
        series = pl.DataFrame(
            {
                "station": ["A", "A"],
                "init_time_utc": ["2025-06-01T00:00:00", "2025-06-01T00:00:00"],
                "ending_lead_hours": [24, 30],
                "valid_time_utc": ["2025-06-02T00:00:00", "2025-06-02T06:00:00"],
                "tigge_mm": [1.0, 2.0],
            }
        ).with_columns(
            pl.col("init_time_utc").str.to_datetime(),
            pl.col("valid_time_utc").str.to_datetime(),
        )
        out = dedup_most_recent_init(series, band_steps=(24, 30, 36, 42))
        assert out.height == 2


class TestGaugeWindowLookup:
    def test_a_window_with_any_missing_hour_is_dropped_not_partially_summed(
        self,
    ) -> None:
        # Hourly gauge values for 06:00..11:00 on 2025-06-02, MISSING 09:00
        # (M-A3-excluded or absent) — the 6h window ending at 11:00 must be
        # null (dropped), never summed over the 5 present hours.
        gauge = pl.DataFrame(
            {
                "timestamp": [
                    "2025-06-02T06:00:00",
                    "2025-06-02T07:00:00",
                    "2025-06-02T08:00:00",
                    # 09:00 missing
                    "2025-06-02T10:00:00",
                    "2025-06-02T11:00:00",
                ],
                "value_mm": [1.0, 1.0, 1.0, 1.0, 1.0],
            }
        ).with_columns(pl.col("timestamp").str.to_datetime())
        full_index = pl.datetime_range(
            pl.datetime(2025, 6, 2, 0, 0),
            pl.datetime(2025, 6, 2, 23, 0),
            interval="1h",
            eager=True,
        ).alias("timestamp")
        lookup = _gauge_window_lookup(gauge, full_index=full_index)
        row = lookup.filter(pl.col("timestamp") == pl.datetime(2025, 6, 2, 11, 0)).row(
            0, named=True
        )
        assert (
            row["gauge_window_mm"] is None
        )  # dropped, never partially summed (4 of 6 hours)

    def test_a_complete_window_sums_all_six_hours(self) -> None:
        gauge = pl.DataFrame(
            {
                "timestamp": [f"2025-06-02T{h:02d}:00:00" for h in range(0, 6)],
                "value_mm": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            }
        ).with_columns(pl.col("timestamp").str.to_datetime())
        full_index = pl.datetime_range(
            pl.datetime(2025, 6, 2, 0, 0),
            pl.datetime(2025, 6, 2, 23, 0),
            interval="1h",
            eager=True,
        ).alias("timestamp")
        lookup = _gauge_window_lookup(gauge, full_index=full_index)
        row = lookup.filter(pl.col("timestamp") == pl.datetime(2025, 6, 2, 5, 0)).row(
            0, named=True
        )
        assert row["gauge_window_mm"] == pytest.approx(
            21.0
        )  # 1+2+3+4+5+6, never partial


class TestEstimatePhaseStationEqualAggregation:
    """D5 — 'each station's cycle sums to 100%'; the reported band figure
    is the MEDIAN across per-station lags. This is the blocker's own
    locking test: pooling raw mass across stations first (the pre-fix
    behaviour) would let the highest-count station dominate the estimate,
    which this constructs to be numerically obvious."""

    def test_aggregate_lag_is_a_station_equal_median_not_a_mass_weighted_pool(
        self,
    ) -> None:
        # Low1 and Low2: a handful of mm, gauge peaks 06:00 UTC, TIGGE
        # peaks 12:00 UTC -> lag = +6 h (same-day branch).
        # Big: a THOUSAND-fold more mass, gauge peaks 06:00 UTC, TIGGE
        # peaks 00:00 UTC -> lag = -6 h. A pooled-mass estimator would be
        # swamped by Big and report ~-6 h; a station-equal median reports
        # the +6 h the MAJORITY of stations actually show.
        rows = [
            _row(
                station="Low1",
                valid_time="2025-06-01T06:00:00",
                gauge_mm=10.0,
                tigge_mm=0.0,
            ),
            _row(
                station="Low1",
                valid_time="2025-06-01T11:00:00",
                gauge_mm=0.0,
                tigge_mm=10.0,
            ),
            _row(
                station="Low2",
                valid_time="2025-06-02T06:00:00",
                gauge_mm=10.0,
                tigge_mm=0.0,
            ),
            _row(
                station="Low2",
                valid_time="2025-06-02T11:00:00",
                gauge_mm=0.0,
                tigge_mm=10.0,
            ),
            _row(
                station="Big",
                valid_time="2025-06-03T06:00:00",
                gauge_mm=1000.0,
                tigge_mm=0.0,
            ),
            _row(
                station="Big",
                valid_time="2025-06-03T23:00:00",
                gauge_mm=0.0,
                tigge_mm=1000.0,
            ),
        ]
        paired = _paired_frame(rows)
        est = estimate_phase(
            paired,
            lead_band="D+1",
            elevation_band="mid (1,000–2,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        assert est is not None
        assert est.n_stations == 3
        # Low1/Low2 each read +5 h; Big alone reads -7 h. The STATION-EQUAL
        # median is +5 h (the majority) on BOTH branches — never Big's -7 h,
        # which a mass-weighted pool would report (Big's 1000 mm swamps
        # Low1+Low2's combined 20 mm at every hour).
        assert est.lag_h == pytest.approx(5.0)
        assert est.lag_principal_h == pytest.approx(5.0)


class TestPerStationNormalisation:
    def test_each_station_shares_sum_to_one_regardless_of_magnitude(self) -> None:
        big = _paired_frame(
            [
                _row(
                    station="Big",
                    valid_time="2025-06-01T06:00:00",
                    gauge_mm=1000.0,
                    tigge_mm=500.0,
                ),
                _row(
                    station="Big",
                    valid_time="2025-06-01T12:00:00",
                    gauge_mm=200.0,
                    tigge_mm=1500.0,
                ),
            ]
        )
        small = _paired_frame(
            [
                _row(
                    station="Small",
                    valid_time="2025-06-01T06:00:00",
                    gauge_mm=1.0,
                    tigge_mm=0.5,
                ),
                _row(
                    station="Small",
                    valid_time="2025-06-01T12:00:00",
                    gauge_mm=0.2,
                    tigge_mm=1.5,
                ),
            ]
        )
        big_phase = estimate_station_phase(Station("Big"), big)
        small_phase = estimate_station_phase(Station("Small"), small)
        assert big_phase is not None
        assert small_phase is not None
        assert big_phase.gauge_share.sum() == pytest.approx(1.0)
        assert small_phase.gauge_share.sum() == pytest.approx(1.0)
        # Same SHAPE (proportions), different SCALE -> identical lag.
        assert big_phase.lag_h == pytest.approx(small_phase.lag_h)


class TestStationDayCount:
    def test_counts_distinct_station_date_pairs_not_windows(self) -> None:
        frame = _paired_frame(
            [
                _row(
                    station="A",
                    valid_time="2025-06-01T06:00:00",
                    gauge_mm=1.0,
                    tigge_mm=1.0,
                ),
                _row(
                    station="A",
                    valid_time="2025-06-01T12:00:00",
                    gauge_mm=1.0,
                    tigge_mm=1.0,
                ),
                _row(
                    station="B",
                    valid_time="2025-06-02T06:00:00",
                    gauge_mm=1.0,
                    tigge_mm=1.0,
                ),
            ]
        )
        assert frame.height == 3  # 3 windows
        assert station_day_count(frame) == 2  # A's two windows share ONE date

    def test_empty_frame_is_zero(self) -> None:
        assert station_day_count(pl.DataFrame()) == 0


class TestPhaseEstimateStationDays:
    def test_n_station_days_can_differ_from_n_windows(self) -> None:
        rows = [
            _row(
                station="A",
                valid_time="2025-06-01T06:00:00",
                gauge_mm=1.0,
                tigge_mm=1.0,
            ),
            _row(
                station="A",
                valid_time="2025-06-01T12:00:00",
                gauge_mm=1.0,
                tigge_mm=2.0,
            ),
            _row(
                station="B",
                valid_time="2025-06-02T06:00:00",
                gauge_mm=1.0,
                tigge_mm=1.0,
            ),
        ]
        paired = _paired_frame(rows)
        est = estimate_phase(
            paired,
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        assert est is not None
        assert est.n_windows == 3
        assert est.n_station_days == 2  # A's two windows share ONE date


class TestRestrictToPinnedSeason:
    """D2/T2 — the CLI must never silently label another year's JJAS data
    as `TIGGE_YEAR`; the filter checks YEAR as well as month."""

    def test_drops_rows_outside_the_pinned_year_even_within_jjas_months(
        self,
    ) -> None:
        paired = pl.DataFrame(
            {
                "valid_time_utc": ["2025-06-15T00:00:00", "2024-06-15T00:00:00"],
                "gauge_mm": [1.0, 1.0],
            }
        ).with_columns(pl.col("valid_time_utc").str.to_datetime())
        out = restrict_to_pinned_season(
            paired, jjas_year=2025, jjas_months=(6, 7, 8, 9)
        )
        assert out.height == 1
        assert out["valid_time_utc"][0].year == 2025

    def test_drops_rows_with_a_null_gauge_pairing(self) -> None:
        paired = pl.DataFrame(
            {
                "valid_time_utc": ["2025-06-15T00:00:00"],
                "gauge_mm": [None],
            },
            schema={"valid_time_utc": pl.Utf8, "gauge_mm": pl.Float64},
        ).with_columns(pl.col("valid_time_utc").str.to_datetime())
        out = restrict_to_pinned_season(
            paired, jjas_year=2025, jjas_months=(6, 7, 8, 9)
        )
        assert out.height == 0


class TestWriteCsvIncludesStationDays:
    def test_csv_has_a_station_days_column_distinct_from_window_count(
        self, tmp_path: object
    ) -> None:
        est = PhaseEstimate(
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
            n_windows=10,
            n_station_days=4,
            n_stations=2,
            gauge_peak_hour_utc=6,
            tigge_peak_hour_utc=12,
            lag_h=6.0,
            lag_principal_h=6.0,
        )
        path = tmp_path / "out.csv"  # type: ignore[attr-defined]
        tigge_gauge_timing.write_csv([est], path)
        out = pl.read_csv(path)
        assert out["n_paired_windows"][0] == 10
        assert out["n_station_days"][0] == 4
