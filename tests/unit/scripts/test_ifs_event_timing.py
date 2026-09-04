"""M-A11c — the four corrected conventions, locked.

Scope is deliberately narrow (this is a measurement tool, not a service): one
test per defect the independent review found in the untracked scratch scripts,
plus the precondition the null depends on. The aggregate rates themselves are
not asserted — they are a property of the data, and the Regenerate command in
`docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md` is what reproduces
them.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ifs_event_timing import (
    EventTimingInputError,
    EventTimingParams,
    StationSeasonCell,
    gauge_season_windows,
    measure_events,
    season_bounds,
    station_phases,
    whole_day_shift,
)
from scripts.dhm_precip.ma6_pairs import MaskedGaugeSeries

STATION = Station("Aiselukhark")


def _gauge_series(hours: list[datetime], values: list[float]) -> MaskedGaugeSeries:
    return MaskedGaugeSeries(
        frame=pl.DataFrame(
            {
                "station": [str(STATION)] * len(hours),
                "timestamp": hours,
                "value_mm": values,
            }
        ).sort("timestamp")
    )


def _epoch_hours(times: list[datetime]) -> np.ndarray:
    epoch = datetime(1970, 1, 1)  # noqa: DTZ001 — the parquet's axis is naive UTC
    return np.array(
        [int((t - epoch).total_seconds()) // 3600 for t in times], dtype=np.int64
    )


class TestSeasonBounds:
    def test_grid_is_a_whole_number_of_days(self) -> None:
        """The null's precondition: a whole-day roll only preserves the clock
        when the season grid holds complete days."""
        for year in (2020, 2021, 2024):
            start, end = season_bounds(year)
            windows = int((end - start).total_seconds() // (6 * 3600)) + 1
            assert windows % 4 == 0, year

    def test_first_window_ends_six_hours_into_the_season(self) -> None:
        start, end = season_bounds(2024)
        assert start == datetime(2024, 6, 1, 6)  # noqa: DTZ001 — naive UTC axis
        assert end == datetime(2024, 10, 1, 0)  # noqa: DTZ001 — naive UTC axis


class TestGaugeSeasonWindows:
    def test_an_observation_stamped_06z_lands_in_the_window_ending_06z(self) -> None:
        """Defect 1. The convention is `(h−6, h]`, so the 06Z hour belongs to
        the window ending 06Z — NOT the one ending 12Z."""
        hours = [datetime(2024, 6, 1, h) for h in range(1, 13)]  # noqa: DTZ001
        values = [0.0] * 12
        values[hours.index(datetime(2024, 6, 1, 6))] = 5.0  # noqa: DTZ001
        windows = gauge_season_windows(_gauge_series(hours, values), year=2024)
        totals = dict(
            zip(
                windows["valid_time_utc"].to_list(),
                windows["gauge_mm"].to_list(),
                strict=True,
            )
        )
        assert totals[datetime(2024, 6, 1, 6)] == 5.0  # noqa: DTZ001
        assert totals[datetime(2024, 6, 1, 12)] == 0.0  # noqa: DTZ001

    def test_a_window_missing_an_antecedent_hour_is_dropped(self) -> None:
        hours = [datetime(2024, 6, 1, h) for h in range(1, 7) if h != 3]  # noqa: DTZ001
        windows = gauge_season_windows(
            _gauge_series(hours, [1.0] * len(hours)), year=2024
        )
        assert datetime(2024, 6, 1, 6) not in windows["valid_time_utc"].to_list()  # noqa: DTZ001


class TestWholeDayShift:
    def test_every_value_keeps_its_clock_hour(self) -> None:
        clock = np.tile(np.array([6.0, 12.0, 18.0, 0.0]), 5)
        shifted = whole_day_shift(clock, shift_days=3)
        assert np.array_equal(shifted, clock)

    def test_a_grid_that_is_not_whole_days_is_refused(self) -> None:
        with pytest.raises(EventTimingInputError, match="whole number of days"):
            whole_day_shift(np.zeros(6), shift_days=1)


class TestMeasureEvents:
    def test_search_reaches_an_ifs_window_the_gauge_is_missing(self) -> None:
        """Defect 4. The peak search runs over the FULL IFS series, so a gap in
        the gauge must not remove an otherwise-available IFS candidate."""
        day = [datetime(2024, 6, 1, h) for h in (6, 12, 18)] + [  # noqa: DTZ001
            datetime(2024, 6, 2, 0)  # noqa: DTZ001
        ]
        # The gauge event is at 06Z; its 12Z window is absent entirely.
        cell = StationSeasonCell(
            station=STATION,
            year=2024,
            gauge_hour=_epoch_hours([day[0], day[2], day[3]]),
            gauge_mm=np.array([20.0, 0.0, 0.0]),
            ifs_hour=_epoch_hours(day),
            ifs_mm=np.array([1.0, 9.0, 0.5, 0.5]),
            event_threshold_mm=10.0,
        )
        result = measure_events(
            [cell], params=EventTimingParams(search_window_h=12, min_candidates=3)
        )
        assert result.n_events == 1
        assert result.offsets_h.tolist() == [6.0]

    def test_an_ifs_peak_below_the_threshold_fraction_counts_as_missed(self) -> None:
        day = [datetime(2024, 6, 1, h) for h in (6, 12, 18)] + [  # noqa: DTZ001
            datetime(2024, 6, 2, 0)  # noqa: DTZ001
        ]
        cell = StationSeasonCell(
            station=STATION,
            year=2024,
            gauge_hour=_epoch_hours(day),
            gauge_mm=np.array([20.0, 0.0, 0.0, 0.0]),
            ifs_hour=_epoch_hours(day),
            ifs_mm=np.array([1.0, 2.0, 0.5, 0.5]),
            event_threshold_mm=10.0,
        )
        result = measure_events([cell], params=EventTimingParams(search_window_h=12))
        assert (result.n_events, result.n_missed) == (1, 1)
        assert result.missed_fraction == 1.0


class TestStationPhases:
    def test_ifs_peaking_six_hours_after_the_gauge_reads_as_plus_six(self) -> None:
        """Defect 2. The sign follows the tracked `harmonic_phase_h`: a later
        IFS cycle is a POSITIVE offset. A negated exponent would report −6."""
        days = 8
        stamps = [
            datetime(2024, 6, 1 + day, hour)  # noqa: DTZ001
            for day in range(days)
            for hour in (6, 12, 18)
        ] + [
            datetime(2024, 6, 2 + day, 0)  # noqa: DTZ001
            for day in range(days)
        ]
        stamps.sort()
        hours = _epoch_hours(stamps)
        clock = np.array([t.hour for t in stamps])
        cell = StationSeasonCell(
            station=STATION,
            year=2024,
            gauge_hour=hours,
            gauge_mm=np.where(clock == 12, 10.0, 0.0),
            ifs_hour=hours,
            ifs_mm=np.where(clock == 18, 10.0, 0.0),
            event_threshold_mm=10.0,
        )
        phases = station_phases([cell], min_amplitude=0.05)
        assert phases[STATION].lag_principal_h == pytest.approx(6.0)

    def test_a_flat_cycle_fails_the_amplitude_gate(self) -> None:
        stamps = [
            datetime(2024, 6, 1 + day, hour)  # noqa: DTZ001
            for day in range(8)
            for hour in (0, 6, 12, 18)
        ]
        hours = _epoch_hours(stamps)
        flat = np.full(len(stamps), 5.0)
        cell = StationSeasonCell(
            station=STATION,
            year=2024,
            gauge_hour=hours,
            gauge_mm=flat,
            ifs_hour=hours,
            ifs_mm=flat,
            event_threshold_mm=10.0,
        )
        assert station_phases([cell], min_amplitude=0.05) == {}
