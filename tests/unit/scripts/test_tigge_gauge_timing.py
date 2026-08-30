"""Plan 216 (M-A11) T2 — locked red-first against the spec:
- "Where two initialisations yield a forecast at the same valid time, take
  the most recent initialisation within the band, deterministically —
  never both, never an average."
- "A window with any missing gauge hour is dropped, never partially
  summed."
- D5: M-A6's OWN normalisation — clock-hour SUM / clock-hour OBSERVATION
  COUNT, normalised as a cycle of hourly MEANS (never raw totals) — and
  the reported band figure is the station-EQUAL median.
- D7: the alternate timezone reading is a UNIFORM rotation of the SAME
  contributing windows, never a second pairing with its own `n`.
- a station must cover all four 6-hourly clock positions to contribute.

No network, no real gauge workbook — every test is against small synthetic
frames, mirroring `tests/unit/scripts/test_era5_deaccumulate.py`.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
import pytest

import scripts.dhm_precip.diurnal_phase as diurnal_phase
import scripts.dhm_precip.tigge_gauge_timing as tigge_gauge_timing
from scripts.dhm_precip.domain_types import (
    Station,
    StationCoordinate,
    StationCoordinateTable,
)
from scripts.dhm_precip.ma6_pairs import GaugeMaskedPopulation, MaskedGaugeSeries
from scripts.dhm_precip.tigge_gauge_timing import (
    GAUGE_SHIFT_READINGS,
    MIN_HARMONIC_AMPLITUDE,
    REQUIRED_CLOCK_HOURS,
    PhaseEstimate,
    PhaseStatus,
    StationPhase,
    _gauge_window_lookup,
    _hour_of_day_share,
    dedup_most_recent_init,
    estimate_phase,
    estimate_station_phase,
    restrict_to_pinned_season,
    run_all_bands,
    station_day_count,
)
from scripts.dhm_precip.tigge_ifs import LEAD_BANDS

if TYPE_CHECKING:
    from pathlib import Path

_D1_STEPS = LEAD_BANDS["D+1"]  # sourced from the module, never a literal


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


def _cycle_rows(
    *,
    station: str,
    date: str,
    gauge_by_hour: dict[int, float],
    tigge_by_hour: dict[int, float],
) -> list[dict[str, object]]:
    """One station-day covering ALL FOUR clock positions (the only shape a
    diurnal fit is defined on)."""
    return [
        _row(
            station=station,
            valid_time=f"{date}T{h:02d}:00:00",
            gauge_mm=gauge_by_hour.get(h, 0.0),
            tigge_mm=tigge_by_hour.get(h, 0.0),
        )
        for h in REQUIRED_CLOCK_HOURS
    ]


class TestCleanCheckoutImport:
    """Locks the blocker: unit-test collection (and CI, which never
    provisions `data/`) must never require the gitignored M-A6 output
    artefact `data/dhm_precip/figures/era5-timing/era5_gauge_timing_figure.py`
    to exist on disk. Reproduced pre-fix: moving that file aside made
    `pytest tests/unit/scripts/test_tigge_gauge_timing.py` fail at
    COLLECTION with `FileNotFoundError` before a single test ran.

    ⚠️ This covers the TRACKED consumer only. `era5_gauge_timing_figure.py`
    lives under the gitignored `data/` tree; no test here can reach it, so
    nothing in this file backs any claim about how IT imports the shared
    module."""

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
        out = dedup_most_recent_init(series, band_steps=_D1_STEPS)
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
        out = dedup_most_recent_init(series, band_steps=_D1_STEPS)
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
        out = dedup_most_recent_init(series, band_steps=_D1_STEPS)
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


class TestHourlyMeanNormalisation:
    """THE BLOCKER's locking test. M-A6 normalises a cycle of hourly MEANS
    (`hourly_means()` sums and counts; `pooled()` divides by the count;
    only then `share = 100 * mean / mean.sum()`). Normalising RAW TOTALS
    instead is a different estimator: with gappy coverage it weights each
    clock position by HOW MANY windows survived there, so adding windows
    that do not change a bin's mean still moves the phase.

    Duplicating observations in ONE clock bin, at that bin's own mean, must
    therefore leave the cycle — and the phase — bit-identical."""

    def test_extra_observations_at_the_same_bin_mean_do_not_move_the_phase(
        self,
    ) -> None:
        gauge = {0: 0.0, 6: 4.0, 12: 1.0, 18: 0.0}
        tigge = {0: 0.0, 6: 1.0, 12: 4.0, 18: 0.0}
        base = _paired_frame(
            _cycle_rows(
                station="A", date="2025-06-01", gauge_by_hour=gauge, tigge_by_hour=tigge
            )
        )
        # Three EXTRA observations in the 06 UTC bin only, each carrying
        # exactly that bin's existing mean — so the bin's MEAN is unchanged
        # while its raw TOTAL quadruples.
        extra = _paired_frame(
            [
                _row(
                    station="A",
                    valid_time=f"2025-06-0{d}T06:00:00",
                    gauge_mm=gauge[6],
                    tigge_mm=tigge[6],
                )
                for d in (2, 3, 4)
            ]
        )
        thickened = pl.concat([base, extra])

        base_share = _hour_of_day_share(base, value_col="gauge_mm")
        thick_share = _hour_of_day_share(thickened, value_col="gauge_mm")
        assert thick_share == pytest.approx(base_share)

        base_phase = estimate_station_phase(Station("A"), base)
        thick_phase = estimate_station_phase(Station("A"), thickened)
        assert isinstance(base_phase, StationPhase)
        assert isinstance(thick_phase, StationPhase)
        assert thick_phase.lag_h == pytest.approx(base_phase.lag_h)

    def test_a_bins_share_is_its_mean_not_its_total(self) -> None:
        # 06 UTC: two observations of 2 mm (mean 2). 12 UTC: one of 4 mm.
        # Hourly means are 2 and 4 -> shares 1/3 and 2/3. Raw totals would
        # be 4 and 4 -> 1/2 and 1/2.
        frame = _paired_frame(
            _cycle_rows(
                station="A",
                date="2025-06-01",
                gauge_by_hour={6: 2.0, 12: 4.0},
                tigge_by_hour={6: 1.0},
            )
            + [
                _row(
                    station="A",
                    valid_time="2025-06-02T06:00:00",
                    gauge_mm=2.0,
                    tigge_mm=1.0,
                )
            ]
        )
        share = _hour_of_day_share(frame, value_col="gauge_mm")
        assert share[6] == pytest.approx(1 / 3)
        assert share[12] == pytest.approx(2 / 3)


class TestRequiredClockCoverage:
    """T2 needs a DIURNAL fit. One to three of the four 6-hourly clock
    positions cannot support one however much mass they carry, so such a
    station must not contribute — and the cell must say so explicitly
    rather than vanish from the matrix."""

    def test_a_station_missing_a_clock_position_does_not_contribute(self) -> None:
        partial = _paired_frame(
            [
                _row(
                    station="A",
                    valid_time=f"2025-06-01T{h:02d}:00:00",
                    gauge_mm=1.0,
                    tigge_mm=1.0,
                )
                for h in (0, 6, 12)  # 18 UTC missing
            ]
        )
        assert (
            estimate_station_phase(Station("A"), partial)
            is PhaseStatus.INSUFFICIENT_CLOCK_COVERAGE
        )

    def test_the_cell_is_reported_with_a_status_never_dropped(self) -> None:
        partial = _paired_frame(
            [
                _row(
                    station="A",
                    valid_time=f"2025-06-01T{h:02d}:00:00",
                    gauge_mm=1.0,
                    tigge_mm=1.0,
                )
                for h in (0, 6, 12)
            ]
        )
        est = estimate_phase(
            partial,
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        assert est.status is PhaseStatus.INSUFFICIENT_CLOCK_COVERAGE
        assert est.n_stations == 0
        assert np.isnan(est.lag_h)

    def test_an_empty_cell_is_reported_as_no_paired_windows(self) -> None:
        empty = _paired_frame(
            [
                _row(
                    station="A",
                    valid_time="2025-06-01T00:00:00",
                    gauge_mm=1.0,
                    tigge_mm=1.0,
                )
            ]
        ).clear()
        est = estimate_phase(
            empty,
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        assert est.status is PhaseStatus.NO_PAIRED_WINDOWS
        assert est.n_windows == 0


class TestEstimatePhaseStationEqualAggregation:
    """D5 — the reported band figure is the MEDIAN across per-station lags.
    Pooling raw mass across stations first would let the highest-mass
    station dominate, which this constructs to be numerically obvious."""

    def test_aggregate_lag_is_a_station_equal_median_not_a_mass_weighted_pool(
        self,
    ) -> None:
        # Low1/Low2: gauge peaks 06 UTC, TIGGE peaks 00 UTC -> lag -6 h.
        # Big: a HUNDRED-fold more mass, gauge peaks 06 UTC, TIGGE peaks
        # 12 UTC -> lag -18 h (same-day branch) / +6 h (shortest arc). A
        # pooled-mass estimator is swamped by Big and reports BIG's answer;
        # a station-equal median reports the -6 h the MAJORITY show.
        rows = (
            _cycle_rows(
                station="Low1",
                date="2025-06-01",
                gauge_by_hour={6: 10.0},
                tigge_by_hour={0: 10.0},
            )
            + _cycle_rows(
                station="Low2",
                date="2025-06-02",
                gauge_by_hour={6: 10.0},
                tigge_by_hour={0: 10.0},
            )
            + _cycle_rows(
                station="Big",
                date="2025-06-03",
                gauge_by_hour={6: 1000.0},
                tigge_by_hour={12: 1000.0},
            )
        )
        est = estimate_phase(
            _paired_frame(rows),
            lead_band="D+1",
            elevation_band="mid (1,000–2,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        assert est.status is PhaseStatus.OK
        assert est.n_stations == 3
        assert est.lag_h == pytest.approx(-6.0)
        assert est.lag_principal_h == pytest.approx(-6.0)
        # ... and never Big's own answer on either branch.
        assert est.lag_h != pytest.approx(-18.0)
        assert est.lag_principal_h != pytest.approx(6.0)


class TestPerStationNormalisation:
    def test_each_station_shares_sum_to_one_regardless_of_magnitude(self) -> None:
        big = _paired_frame(
            _cycle_rows(
                station="Big",
                date="2025-06-01",
                gauge_by_hour={6: 1000.0, 12: 200.0},
                tigge_by_hour={6: 500.0, 12: 1500.0},
            )
        )
        small = _paired_frame(
            _cycle_rows(
                station="Small",
                date="2025-06-01",
                gauge_by_hour={6: 1.0, 12: 0.2},
                tigge_by_hour={6: 0.5, 12: 1.5},
            )
        )
        big_phase = estimate_station_phase(Station("Big"), big)
        small_phase = estimate_station_phase(Station("Small"), small)
        assert isinstance(big_phase, StationPhase)
        assert isinstance(small_phase, StationPhase)
        assert big_phase.gauge_share.sum() == pytest.approx(1.0)
        assert small_phase.gauge_share.sum() == pytest.approx(1.0)
        # Same SHAPE (proportions), different SCALE -> identical lag.
        assert big_phase.lag_h == pytest.approx(small_phase.lag_h)


class TestD7UniformRotation:
    """D7 — 'An NPT reading shifts every offset uniformly +6 h while
    leaving the between-band contrast invariant.' That holds only if both
    readings are computed from the SAME contributing windows: the reading
    is a circular rotation of the built cycle, never a second pairing at a
    different window alignment (which changes which windows survive, and
    their `n`, so the shift is not uniform)."""

    def test_the_npt_reading_shifts_the_lag_by_exactly_six_hours(self) -> None:
        rows = _cycle_rows(
            station="A",
            date="2025-06-01",
            gauge_by_hour={6: 8.0, 12: 2.0},
            tigge_by_hour={12: 7.0, 18: 3.0},
        ) + _cycle_rows(
            station="B",
            date="2025-06-02",
            gauge_by_hour={0: 5.0, 6: 5.0},
            tigge_by_hour={6: 4.0, 12: 6.0},
        )
        paired = _paired_frame(rows)
        as_labelled = estimate_phase(
            paired,
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
            gauge_hour_shift=GAUGE_SHIFT_READINGS["as_labelled_utc"],
        )
        as_npt = estimate_phase(
            paired,
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="gauge_labels_are_npt",
            gauge_hour_shift=GAUGE_SHIFT_READINGS["gauge_labels_are_npt"],
        )
        assert as_npt.lag_h == pytest.approx(as_labelled.lag_h + 6.0)
        # ... on the IDENTICAL sample.
        assert as_npt.n_windows == as_labelled.n_windows
        assert as_npt.n_station_days == as_labelled.n_station_days
        assert as_npt.n_stations == as_labelled.n_stations
        # ... because the reading is literally the same cycle, rotated.
        base = estimate_station_phase(
            Station("A"), paired.filter(pl.col("station") == "A")
        )
        rotated = estimate_station_phase(
            Station("A"),
            paired.filter(pl.col("station") == "A"),
            gauge_hour_shift=GAUGE_SHIFT_READINGS["gauge_labels_are_npt"],
        )
        assert isinstance(base, StationPhase)
        assert isinstance(rotated, StationPhase)
        assert rotated.gauge_share == pytest.approx(np.roll(base.gauge_share, -6))
        assert rotated.tigge_share == pytest.approx(base.tigge_share)


class TestNonFinitePairsAreExcluded:
    """T1 carries gaps rather than filling them, so a masked raw point
    reaches T2 as a NaN — neither materially negative nor clipped away. A
    non-finite value on EITHER side is not an observation: it must be
    dropped at the paired-statistic boundary and excluded from every `n`,
    never survive into a share, a phase, a median or a station-day count."""

    def test_restrict_drops_non_finite_values_on_either_side(self) -> None:
        paired = _paired_frame(
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
                    tigge_mm=float("nan"),
                ),
                _row(
                    station="A",
                    valid_time="2025-06-01T18:00:00",
                    gauge_mm=float("nan"),
                    tigge_mm=1.0,
                ),
            ]
        )
        out = restrict_to_pinned_season(paired, jjas_year=2025, jjas_months=(6,))
        assert out.height == 1

    def test_the_phase_stays_finite_and_n_drops_when_a_window_is_nan(self) -> None:
        clean = _cycle_rows(
            station="A",
            date="2025-06-01",
            gauge_by_hour={6: 4.0, 12: 1.0},
            tigge_by_hour={12: 4.0, 18: 1.0},
        )
        poisoned = clean + [
            _row(
                station="A",
                valid_time="2025-06-02T06:00:00",
                gauge_mm=2.0,
                tigge_mm=float("nan"),
            )
        ]
        kept = restrict_to_pinned_season(
            _paired_frame(poisoned), jjas_year=2025, jjas_months=(6,)
        )
        est = estimate_phase(
            kept,
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        assert est.status is PhaseStatus.OK
        assert np.isfinite(est.lag_h)
        assert est.n_windows == 4  # the NaN window is excluded from `n`
        assert est.n_station_days == 1  # ... and from the station-day count


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
        rows = _cycle_rows(
            station="A",
            date="2025-06-01",
            gauge_by_hour={6: 1.0},
            tigge_by_hour={12: 1.0},
        ) + _cycle_rows(
            station="A",
            date="2025-06-02",
            gauge_by_hour={6: 1.0},
            tigge_by_hour={12: 1.0},
        )
        est = estimate_phase(
            _paired_frame(rows),
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        assert est.n_windows == 8
        assert est.n_station_days == 2  # 8 windows over TWO calendar dates


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
                "tigge_mm": [1.0, 1.0],
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
                "tigge_mm": [1.0],
            },
            schema={
                "valid_time_utc": pl.Utf8,
                "gauge_mm": pl.Float64,
                "tigge_mm": pl.Float64,
            },
        ).with_columns(pl.col("valid_time_utc").str.to_datetime())
        out = restrict_to_pinned_season(
            paired, jjas_year=2025, jjas_months=(6, 7, 8, 9)
        )
        assert out.height == 0


def _synthetic_inputs() -> tuple[
    pl.DataFrame, GaugeMaskedPopulation, StationCoordinateTable
]:
    """One low-band station, one 00Z init, every step D+1/D+2/D+3 need, and
    a complete hourly gauge record around them."""
    init = "2025-06-01T00:00:00"
    steps = [h for band in LEAD_BANDS.values() for h in band]
    series = pl.DataFrame(
        {
            "station": ["A"] * len(steps),
            "init_time_utc": [init] * len(steps),
            "ending_lead_hours": steps,
            "valid_time_utc": [
                (np.datetime64(init, "ns") + np.timedelta64(h, "h"))
                .astype("datetime64[s]")
                .astype(str)
                for h in steps
            ],
            "tigge_mm": [float(h % 24 == 12) for h in steps],
        }
    ).with_columns(
        pl.col("init_time_utc").str.to_datetime(),
        # ns precision, exactly as T1's parquet writes it — the gauge index
        # is built at polars' default precision, so the pairing join must
        # reconcile the two rather than assume they match.
        pl.col("valid_time_utc").str.to_datetime().cast(pl.Datetime("ns")),
    )
    stamps = pl.datetime_range(
        pl.datetime(2025, 5, 31, 0, 0),
        pl.datetime(2025, 6, 6, 0, 0),
        interval="1h",
        eager=True,
    )
    gauge = pl.DataFrame(
        {
            "station": ["A"] * len(stamps),
            "timestamp": stamps,
            "value_mm": [3.0 if 1 <= t.hour <= 6 else 1.0 for t in stamps],
        }
    )
    population = GaugeMaskedPopulation(
        by_station={Station("A"): MaskedGaugeSeries(frame=gauge)},
        excluded=(),
        accounting=(),
    )
    coords = StationCoordinateTable(
        by_station={
            Station("A"): StationCoordinate(
                station=Station("A"), excel_col="A", lat=28.0, lon=84.0, elev_m=500.0
            )
        }
    )
    return series, population, coords


class TestResultMatrixIsComplete:
    """T2 must report the FULL 3 lead bands x 3 elevation bands x 2 D7
    readings matrix. A combination nothing supports is a row with a status
    and its `n`, never an absent row a reader cannot tell from one nobody
    computed."""

    def test_every_lead_band_elevation_band_reading_cell_is_present(self) -> None:
        series, population, coords = _synthetic_inputs()
        results = run_all_bands(series, population, coords)
        assert len(results) == 3 * 3 * 2
        assert {
            (r.lead_band, r.elevation_band, r.gauge_shift_reading) for r in results
        } == {
            (lead, band, reading)
            for lead in LEAD_BANDS
            for band in diurnal_phase.BAND_NAMES
            for reading in GAUGE_SHIFT_READINGS
        }
        low = [r for r in results if r.elevation_band.startswith("low")]
        assert len(low) == 6
        assert all(r.status is PhaseStatus.OK for r in low)
        assert all(
            r.status is PhaseStatus.NO_PAIRED_WINDOWS
            for r in results
            if not r.elevation_band.startswith("low")
        )


class TestWriteCsv:
    def test_csv_carries_status_and_station_days(self, tmp_path: Path) -> None:
        estimates = [
            PhaseEstimate(
                lead_band="D+1",
                elevation_band="low (< 1,000 m)",
                gauge_shift_reading="as_labelled_utc",
                status=PhaseStatus.OK,
                n_windows=10,
                n_station_days=4,
                n_stations=2,
                gauge_peak_hour_utc=6,
                tigge_peak_hour_utc=12,
                lag_h=6.0,
                lag_principal_h=6.0,
                gauge_amplitude=0.42,
                tigge_amplitude=0.31,
            ),
            PhaseEstimate(
                lead_band="D+1",
                elevation_band="high (≥ 2,000 m)",
                gauge_shift_reading="as_labelled_utc",
                status=PhaseStatus.NO_PAIRED_WINDOWS,
                n_windows=0,
                n_station_days=0,
                n_stations=0,
                gauge_peak_hour_utc=None,
                tigge_peak_hour_utc=None,
                lag_h=float("nan"),
                lag_principal_h=float("nan"),
                gauge_amplitude=float("nan"),
                tigge_amplitude=float("nan"),
            ),
        ]
        path = tmp_path / "out.csv"
        tigge_gauge_timing.write_csv(estimates, path)
        out = pl.read_csv(path)
        assert out["n_paired_windows"][0] == 10
        assert out["n_station_days"][0] == 4
        assert out["status"].to_list() == ["ok", "no_paired_windows"]
        assert out["gauge_peak_hour_npt"][1] in ("", None)
        # The identifiability diagnostic travels with the published number.
        assert out["gauge_harmonic_amplitude"][0] == pytest.approx(0.42)
        assert out["tigge_harmonic_amplitude"][0] == pytest.approx(0.31)
        assert out["min_harmonic_amplitude"][0] == pytest.approx(MIN_HARMONIC_AMPLITUDE)


class TestPhaseIdentifiability:
    """`harmonic_phase_h` takes the ANGLE of the first harmonic and
    never looks at its MAGNITUDE. With four bins, near-equal opposite bins
    drive that harmonic to ~zero: `np.angle(0)` is 0.0, so the estimator
    returns a plausible-looking 00:00 phase for a cycle that has no
    identified phase at all. Mass alone does not catch it — the cycle below
    carries full mass."""

    def test_equal_opposite_bins_are_unidentifiable_not_a_phase(self) -> None:
        # w0 == w12 and w6 == w18 -> first harmonic is exactly zero.
        flat = _paired_frame(
            _cycle_rows(
                station="A",
                date="2025-06-01",
                gauge_by_hour={0: 10.0, 12: 10.0},
                tigge_by_hour={6: 5.0},
            )
        )
        assert (
            estimate_station_phase(Station("A"), flat)
            is PhaseStatus.PHASE_UNIDENTIFIABLE
        )
        est = estimate_phase(
            flat,
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        assert est.status is PhaseStatus.PHASE_UNIDENTIFIABLE
        assert est.n_stations == 0
        assert np.isnan(est.lag_h)

    def test_a_cell_reports_the_furthest_gate_any_station_reached(self) -> None:
        rows = _cycle_rows(
            station="Flat",
            date="2025-06-01",
            gauge_by_hour={0: 10.0, 12: 10.0},
            tigge_by_hour={6: 5.0},
        ) + [
            _row(
                station="Partial",
                valid_time="2025-06-01T06:00:00",
                gauge_mm=1.0,
                tigge_mm=1.0,
            )
        ]
        est = estimate_phase(
            _paired_frame(rows),
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        # "Partial" never covered four clocks; "Flat" did and failed later.
        assert est.status is PhaseStatus.PHASE_UNIDENTIFIABLE

    def test_a_resolved_cycle_publishes_its_amplitude(self) -> None:
        est = estimate_phase(
            _paired_frame(
                _cycle_rows(
                    station="A",
                    date="2025-06-01",
                    gauge_by_hour={0: 10.0},
                    tigge_by_hour={6: 10.0},
                )
            ),
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        assert est.status is PhaseStatus.OK
        # All the mass in one bin -> a unit-length first harmonic.
        assert est.gauge_amplitude == pytest.approx(1.0)
        assert est.tigge_amplitude == pytest.approx(1.0)
        assert est.gauge_amplitude >= MIN_HARMONIC_AMPLITUDE


class TestNoPrecipitationMassIsNotAClockCoverageFailure:
    """A cell whose stations DO cover all four clock positions but
    carry no usable precipitation has a truthful status of its own. Calling
    it `insufficient_clock_coverage` states something false about the data."""

    def test_a_complete_but_dry_cycle_is_no_precipitation_mass(self) -> None:
        dry = _paired_frame(
            _cycle_rows(
                station="A",
                date="2025-06-01",
                gauge_by_hour={},  # all four clocks present, every value 0.0
                tigge_by_hour={6: 5.0},
            )
        )
        assert set(REQUIRED_CLOCK_HOURS) <= {
            int(h) for h in dry["valid_time_utc"].dt.hour().unique().to_list()
        }
        assert (
            estimate_station_phase(Station("A"), dry)
            is PhaseStatus.NO_PRECIPITATION_MASS
        )
        est = estimate_phase(
            dry,
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
        )
        assert est.status is PhaseStatus.NO_PRECIPITATION_MASS
        assert est.status is not PhaseStatus.INSUFFICIENT_CLOCK_COVERAGE
        assert est.n_windows == 4  # the windows are there; the rain is not


class TestD7BranchCrossing:
    """Plan 216 D7 (amended) — the alternate NPT reading moves each station's
    CIRCULAR offset by +6 h modulo 24. The reported band figure is an
    ARITHMETIC median of same-day-branch representatives, which is not
    rotation-equivariant: once a station crosses the +6/-18 branch cut the
    band median need neither shift by +6 h nor stay put. The pre-existing
    D7 test exercises only a no-wrap sample."""

    def _rows(self) -> list[dict[str, object]]:
        # Gauge mass at 00 UTC for all three -> per-station offsets 0, -12, -6.
        return [
            row
            for station, tigge_hour in (("A", 0), ("B", 12), ("C", 18))
            for row in _cycle_rows(
                station=station,
                date="2025-06-01",
                gauge_by_hour={0: 10.0},
                tigge_by_hour={tigge_hour: 10.0},
            )
        ]

    def test_each_station_shifts_by_six_hours_modulo_twenty_four(self) -> None:
        paired = _paired_frame(self._rows())
        for station in ("A", "B", "C"):
            frame = paired.filter(pl.col("station") == station)
            base = estimate_station_phase(Station(station), frame)
            rotated = estimate_station_phase(
                Station(station),
                frame,
                gauge_hour_shift=GAUGE_SHIFT_READINGS["gauge_labels_are_npt"],
            )
            assert isinstance(base, StationPhase)
            assert isinstance(rotated, StationPhase)
            assert (rotated.lag_h - base.lag_h) % 24.0 == pytest.approx(6.0)

    def test_the_band_median_does_not_simply_shift_by_six_hours(self) -> None:
        paired = _paired_frame(self._rows())
        as_labelled = estimate_phase(
            paired,
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="as_labelled_utc",
            gauge_hour_shift=GAUGE_SHIFT_READINGS["as_labelled_utc"],
        )
        as_npt = estimate_phase(
            paired,
            lead_band="D+1",
            elevation_band="low (< 1,000 m)",
            gauge_shift_reading="gauge_labels_are_npt",
            gauge_hour_shift=GAUGE_SHIFT_READINGS["gauge_labels_are_npt"],
        )
        assert as_labelled.n_stations == as_npt.n_stations == 3
        # Offsets {0, -12, -6} -> median -6; rotated {-18, -6, 0} -> median -6.
        assert as_labelled.lag_h == pytest.approx(-6.0)
        assert as_npt.lag_h == pytest.approx(-6.0)
        assert as_npt.lag_h != pytest.approx(as_labelled.lag_h + 6.0)
