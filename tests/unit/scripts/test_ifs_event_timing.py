"""M-A11c — the four corrected conventions, locked, plus Plan 238's
reproducibility schema (D1/D3/D7).

Scope is deliberately narrow (this is a measurement tool, not a service): one
test per defect the independent review found in the untracked scratch scripts,
plus the precondition the null depends on. The aggregate rates on SYNTHETIC
cells are not asserted — they are a property of the data. `TestBuildReport`
below is the exception for the published figures specifically: gated on the
real, checksum-pinned production inputs being present locally, it asserts the
ALREADY-MEASURED M-A11c table (Plan 238 T1 acceptance), because pinning the
inputs by SHA-256 is what makes asserting them reproducible rather than
tautological. The Regenerate command in
`docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md` is what a human
reproduces them with.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import cast

import numpy as np
import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.ifs_event_timing import (
    CONTINUOUS_DAY_LABEL,
    CONTINUOUS_DAY_LEADS,
    DEFAULT_DECLUSTER_H,
    DEFAULT_EVENT_QUANTILE,
    DEFAULT_INIT_HOUR,
    DEFAULT_MIN_CANDIDATES,
    DEFAULT_MIN_WET_WINDOWS,
    DEFAULT_MISS_FRACTION,
    DEFAULT_NULL_SHIFT_DAYS,
    DEFAULT_SEARCH_WINDOWS_H,
    DEFAULT_SEASONS,
    DEFAULT_TIGGE_ROOT,
    MIN_HARMONIC_AMPLITUDE,
    EventTimingInputError,
    EventTimingParams,
    EventTimingReport,
    FrozenConvention,
    StationSeasonCell,
    StatisticRow,
    build_cells,
    build_report,
    consumed_input_digests,
    format_report,
    gauge_season_windows,
    measure_events,
    render_machine_block,
    report_payload,
    season_bounds,
    station_phases,
    tigge_series_paths,
    whole_day_shift,
)
from scripts.dhm_precip.loader import resolve_coords_path, resolve_source_path
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


def _synthetic_cell() -> StationSeasonCell:
    day = [datetime(2024, 6, 1, h) for h in (6, 12, 18)] + [  # noqa: DTZ001
        datetime(2024, 6, 2, 0)  # noqa: DTZ001
    ]
    return StationSeasonCell(
        station=STATION,
        year=2024,
        gauge_hour=_epoch_hours(day),
        gauge_mm=np.array([20.0, 0.0, 0.0, 0.0]),
        ifs_hour=_epoch_hours(day),
        ifs_mm=np.array([1.0, 9.0, 0.5, 0.5]),
        event_threshold_mm=10.0,
    )


class TestBuildReport:
    """D1/D3 — the structured report schema, on a synthetic cell so this
    class needs no production data. `build_report` is pure: it reorganises
    `measure_events` output, adding no statistic."""

    def _report(self, *, digests: dict[str, str]) -> EventTimingReport:
        return build_report(
            [_synthetic_cell()],
            windows_h=(12,),
            base=EventTimingParams(search_window_h=12),
            leads=CONTINUOUS_DAY_LEADS,
            lead_label=CONTINUOUS_DAY_LABEL,
            init_hour=DEFAULT_INIT_HOUR,
            seasons=DEFAULT_SEASONS,
            null_shift_days=(1,),
            min_amplitude=MIN_HARMONIC_AMPLITUDE,
            input_digests=digests,
        )

    def test_convention_serializes_every_frozen_default(self) -> None:
        """D3 — including the three the first draft omitted: seasons,
        min_wet_windows, min_candidates."""
        result = self._report(digests={})
        assert result.convention == FrozenConvention(
            seasons=DEFAULT_SEASONS,
            leads=CONTINUOUS_DAY_LEADS,
            lead_label=CONTINUOUS_DAY_LABEL,
            init_hour=DEFAULT_INIT_HOUR,
            search_windows_h=(12,),
            event_quantile=DEFAULT_EVENT_QUANTILE,
            decluster_h=DEFAULT_DECLUSTER_H,
            miss_fraction=DEFAULT_MISS_FRACTION,
            min_wet_windows=DEFAULT_MIN_WET_WINDOWS,
            min_candidates=DEFAULT_MIN_CANDIDATES,
            null_shift_days=(1,),
            min_amplitude=MIN_HARMONIC_AMPLITUDE,
        )

    def test_every_row_carries_observed_null_and_increment_together(self) -> None:
        """D1 — never the increment alone, never an absolute rate alone; both
        denominators (matched, searched) travel with every row."""
        result = self._report(digests={})
        assert {row.statistic for row in result.rows} == {
            "exact window",
            "within ±6 h",
            "missed",
        }
        for row in result.rows:
            assert row.window_h == 12
            assert isinstance(row.n_events, int)
            assert isinstance(row.n_matched, int)
            assert row.increment == pytest.approx(row.observed - row.null_mean)
            assert row.null_min <= row.null_mean <= row.null_max

    def test_every_row_carries_the_nulls_own_counts(self) -> None:
        """D1 — observed, null, increment AND THEIR COUNTS travel together: a
        null rate whose searched/matched denominators are not published is not
        comparable to the observed one. One entry per draw, never a mean that
        would hide a draw searching a different event set."""
        result = self._report(digests={})
        for row in result.rows:
            assert len(row.null_n_events) == 1  # one null draw in this fixture
            assert len(row.null_n_matched) == 1
            assert all(isinstance(count, int) for count in row.null_n_events)
            assert all(isinstance(count, int) for count in row.null_n_matched)
            assert all(
                matched <= searched
                for searched, matched in zip(
                    row.null_n_events, row.null_n_matched, strict=True
                )
            )

    def test_format_report_publishes_the_null_counts(self) -> None:
        result = self._report(digests={})
        text = format_report(result, n_stations=1, n_cells=1)
        assert "null counts per draw" in text
        assert "searched" in text
        assert "matched" in text

    def test_input_digests_pass_through_unchanged(self) -> None:
        """D7 — `build_report` carries whatever digests it is given; it does
        not compute them (that is `consumed_input_digests`, tested below
        against the real files)."""
        digests = {"a.parquet": "deadbeef", "workbook.xlsx": "cafef00d"}
        result = self._report(digests=digests)
        assert result.input_digests == digests

    def test_format_report_shows_the_convention_and_the_digests(self) -> None:
        """D2 — the printed form a human (and T2's binding test) reads."""
        digests = {"a.parquet": "deadbeef"}
        result = self._report(digests=digests)
        text = format_report(result, n_stations=1, n_cells=1)
        assert "frozen convention" in text
        assert "min_wet_windows=30" in text
        assert "a.parquet" in text
        assert "deadbeef" in text
        assert "n = 6 seasons" in text
        assert "no reliable inferential interval is available" in text

    def test_the_sample_size_is_derived_from_the_seasons_actually_used(self) -> None:
        """D4 — a hard-coded 6 prints a FALSE sample size for any valid
        non-default `--seasons` run."""
        result = build_report(
            [_synthetic_cell()],
            windows_h=(12,),
            base=EventTimingParams(search_window_h=12),
            leads=CONTINUOUS_DAY_LEADS,
            lead_label=CONTINUOUS_DAY_LABEL,
            init_hour=DEFAULT_INIT_HOUR,
            seasons=(2023, 2024),
            null_shift_days=(1,),
            min_amplitude=MIN_HARMONIC_AMPLITUDE,
            input_digests={},
        )
        text = format_report(result, n_stations=1, n_cells=1)
        assert "n = 2 seasons" in text
        assert "n = 6 seasons" not in text


class TestReportPayload:
    """🔴 D2 — the payload is what the document binding compares, so it must
    carry FULL precision: the printed report rounds results to `.3f` and the
    convention to `:g`, which is how a drifted default survived the first
    version of the binding."""

    def _report(self, *, miss_fraction: float) -> EventTimingReport:
        return build_report(
            [_synthetic_cell()],
            windows_h=(12,),
            base=EventTimingParams(search_window_h=12, miss_fraction=miss_fraction),
            leads=CONTINUOUS_DAY_LEADS,
            lead_label=CONTINUOUS_DAY_LABEL,
            init_hour=DEFAULT_INIT_HOUR,
            seasons=DEFAULT_SEASONS,
            null_shift_days=(1,),
            min_amplitude=MIN_HARMONIC_AMPLITUDE,
            input_digests={"a.parquet": "deadbeef"},
        )

    def test_a_drift_the_printed_report_rounds_away_survives_in_the_payload(
        self,
    ) -> None:
        published = self._report(miss_fraction=0.5)
        drifted = self._report(miss_fraction=0.5000001)
        assert format_report(drifted, n_stations=1, n_cells=1) == format_report(
            published, n_stations=1, n_cells=1
        ), "the printed report is expected to round this drift away"
        assert report_payload(drifted) != report_payload(published)
        convention = report_payload(drifted)["convention"]
        assert isinstance(convention, dict)
        assert convention["miss_fraction"] == 0.5000001

    def test_every_convention_and_row_field_reaches_the_payload(self) -> None:
        """`asdict` rather than a hand-written field list: a field ADDED to
        `FrozenConvention` or `StatisticRow` must enter the binding without
        anyone remembering to add it here."""
        payload = report_payload(self._report(miss_fraction=0.5))
        convention = payload["convention"]
        rows = payload["rows"]
        assert isinstance(convention, dict)
        assert isinstance(rows, list)
        assert set(cast("dict[str, object]", convention)) == {
            field.name for field in fields(FrozenConvention)
        }
        assert set(cast("dict[str, object]", rows[0])) == {
            field.name for field in fields(StatisticRow)
        } | {"increment"}
        assert payload["input_digests"] == {"a.parquet": "deadbeef"}

    def test_the_machine_block_round_trips_as_json(self) -> None:
        report = self._report(miss_fraction=0.5)
        assert json.loads(render_machine_block(report)) == json.loads(
            json.dumps(report_payload(report))
        )


def _production_inputs_available() -> bool:
    """Same gating pattern as `tests/integration/test_dhm_precip_ma6_pairs.py`
    — skipif, no other skip condition."""
    xlsx = os.environ.get("DHM_PRECIP_XLSX")
    if not xlsx or not Path(xlsx).exists():
        return False
    return all(
        path.exists()
        for path in tigge_series_paths(
            tigge_root=DEFAULT_TIGGE_ROOT, seasons=DEFAULT_SEASONS
        )
    )


requires_production_inputs = pytest.mark.skipif(
    not _production_inputs_available(),
    reason=(
        "DHM_PRECIP_XLSX and the six TIGGE season parquet files under "
        f"{DEFAULT_TIGGE_ROOT} must be present locally"
    ),
)


@requires_production_inputs
class TestConsumedInputDigestsOnProductionData:
    """D7 — the emitted digests are the files' REAL sha256, independently
    recomputed here with `shasum -a 256` rather than the module's own
    hashing utility, so a bug shared between production code and test would
    not hide itself. ⚠️ `station_coordinates.csv` is in the list because it
    IS consumed — `ma6_pairs` reads it to build the station population and
    `load_elevations` reads it again for the elevation banding — so the same
    convention on a different coordinates file must fail loudly."""

    def test_digests_match_shasum(self) -> None:
        digests = consumed_input_digests(
            tigge_root=DEFAULT_TIGGE_ROOT, seasons=DEFAULT_SEASONS
        )
        paths = (
            resolve_source_path(),
            resolve_coords_path(),
            *tigge_series_paths(tigge_root=DEFAULT_TIGGE_ROOT, seasons=DEFAULT_SEASONS),
        )
        assert len(digests) == len(paths) == 8
        for path in paths:
            shasum = subprocess.run(
                ["shasum", "-a", "256", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            expected = shasum.stdout.split()[0]
            assert digests[str(path)] == expected, path


@requires_production_inputs
class TestPublishedFiguresOnProductionData:
    """Plan 238 T1 acceptance — the ALREADY-MEASURED M-A11c figures, on
    checksum-pinned inputs: exact-window 0.179 / null 0.095, within ±6 h
    0.442 / null 0.309, missed 0.248 / null 0.400, increments +0.130 /
    +0.133 / +0.134 / +0.118 at ±12/24/36/48 h."""

    def test_matches_the_published_table(self) -> None:
        base = EventTimingParams(search_window_h=DEFAULT_SEARCH_WINDOWS_H[0])
        cells = build_cells(
            tigge_root=DEFAULT_TIGGE_ROOT,
            seasons=DEFAULT_SEASONS,
            leads=CONTINUOUS_DAY_LEADS,
            init_hour=DEFAULT_INIT_HOUR,
            params=base,
        )
        digests = consumed_input_digests(
            tigge_root=DEFAULT_TIGGE_ROOT, seasons=DEFAULT_SEASONS
        )
        result = build_report(
            cells,
            windows_h=DEFAULT_SEARCH_WINDOWS_H,
            base=base,
            leads=CONTINUOUS_DAY_LEADS,
            lead_label=CONTINUOUS_DAY_LABEL,
            init_hour=DEFAULT_INIT_HOUR,
            seasons=DEFAULT_SEASONS,
            null_shift_days=DEFAULT_NULL_SHIFT_DAYS,
            min_amplitude=MIN_HARMONIC_AMPLITUDE,
            input_digests=digests,
        )
        by_key = {(row.window_h, row.statistic): row for row in result.rows}

        exact24 = by_key[24, "exact window"]
        assert exact24.observed == pytest.approx(0.179, abs=5e-4)
        assert exact24.null_mean == pytest.approx(0.095, abs=5e-4)

        within24 = by_key[24, "within ±6 h"]
        assert within24.observed == pytest.approx(0.442, abs=5e-4)
        assert within24.null_mean == pytest.approx(0.309, abs=5e-4)

        missed24 = by_key[24, "missed"]
        assert missed24.observed == pytest.approx(0.248, abs=5e-4)
        assert missed24.null_mean == pytest.approx(0.400, abs=5e-4)

        expected_increments = {12: 0.130, 24: 0.133, 36: 0.134, 48: 0.118}
        for window_h, expected in expected_increments.items():
            row = by_key[window_h, "within ±6 h"]
            assert row.increment == pytest.approx(expected, abs=5e-4), window_h
