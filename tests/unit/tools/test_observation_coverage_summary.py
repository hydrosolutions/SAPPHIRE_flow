"""Unit tests for observation_coverage_summary.compute_coverage_summary."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from sapphire_flow.tools.observation_coverage_summary import (
    CoverageSummary,
    compute_coverage_summary,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation
from tests.fakes.fake_stores import FakeObservationStore

_NOW = ensure_utc(datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC))

_SID_A = StationId(UUID("00000000-0000-0000-0000-000000000001"))
_SID_B = StationId(UUID("00000000-0000-0000-0000-000000000002"))
_SID_C = StationId(UUID("00000000-0000-0000-0000-000000000003"))


def _obs(
    station_id: StationId,
    timestamp: UtcDatetime,
    parameter: str = "discharge",
    value: float = 1.0,
) -> Observation:
    return Observation(
        id=ObservationId(uuid.uuid4()),
        station_id=station_id,
        timestamp=timestamp,
        parameter=parameter,
        value=value,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=QcStatus.QC_PASSED,
        qc_flags=[],
        qc_rule_version=None,
        created_at=timestamp,
    )


def _even_observations(
    station_id: StationId,
    now: UtcDatetime,
    window_hours: int,
    cadence_minutes: int,
    parameter: str = "discharge",
) -> list[Observation]:
    """Generate exactly expected_count observations evenly across the window."""
    start = UtcDatetime(now - timedelta(hours=window_hours))
    step = timedelta(minutes=cadence_minutes)
    timestamps = []
    t = start
    while t < now:
        timestamps.append(t)
        t = UtcDatetime(t + step)
    return [_obs(station_id, ts, parameter) for ts in timestamps]


class TestComputeCoverageSummaryHappyPath:
    def test_full_coverage_three_stations(self) -> None:
        store = FakeObservationStore()
        for sid in (_SID_A, _SID_B, _SID_C):
            store.store_observations(
                _even_observations(sid, _NOW, window_hours=24, cadence_minutes=10)
            )

        summary = compute_coverage_summary(
            store,
            station_ids=[_SID_A, _SID_B, _SID_C],
            now=_NOW,
            parameters=["discharge"],
            window_hours=24,
            expected_cadence_minutes=10,
        )

        assert isinstance(summary, CoverageSummary)
        # Each station should have exactly expected count (24h * 6/h = 144)
        expected = 144
        assert len(summary.rows) == 3
        for row in summary.rows:
            assert row.expected_count == expected
            assert row.actual_count == expected
            assert row.coverage_pct == pytest.approx(100.0, abs=0.1)
        assert summary.overall_coverage_pct == pytest.approx(100.0, abs=0.1)

    def test_now_is_window_end(self) -> None:
        store = FakeObservationStore()
        store.store_observations(
            _even_observations(_SID_A, _NOW, window_hours=24, cadence_minutes=10)
        )

        summary = compute_coverage_summary(
            store,
            station_ids=[_SID_A],
            now=_NOW,
            parameters=["discharge"],
            window_hours=24,
            expected_cadence_minutes=10,
        )

        assert summary.window_end == _NOW
        expected_start = UtcDatetime(_NOW - timedelta(hours=24))
        assert summary.window_start == expected_start


class TestComputeCoverageSummaryGapScenario:
    def test_half_coverage(self) -> None:
        """Only observations in the first 12 of 24 hours → ~50% coverage."""
        store = FakeObservationStore()
        half_window_start = UtcDatetime(_NOW - timedelta(hours=24))
        half_window_end = UtcDatetime(_NOW - timedelta(hours=12))

        step = timedelta(minutes=10)
        t = half_window_start
        obs_list = []
        while t < half_window_end:
            obs_list.append(_obs(_SID_A, t))
            t = UtcDatetime(t + step)
        store.store_observations(obs_list)

        summary = compute_coverage_summary(
            store,
            station_ids=[_SID_A],
            now=_NOW,
            parameters=["discharge"],
            window_hours=24,
            expected_cadence_minutes=10,
        )

        assert len(summary.rows) == 1
        row = summary.rows[0]
        assert row.expected_count == 144
        # 72 observations in first 12 h (12h * 6/h)
        assert row.actual_count == 72
        assert row.coverage_pct == pytest.approx(50.0, abs=0.5)

    def test_multiple_parameters_partial_coverage(self) -> None:
        """discharge has full coverage, water_level has half → overall ~75%."""
        store = FakeObservationStore()
        # Full discharge coverage
        store.store_observations(
            _even_observations(_SID_A, _NOW, 24, 10, parameter="discharge")
        )
        # Half water_level coverage
        half_start = UtcDatetime(_NOW - timedelta(hours=24))
        half_end = UtcDatetime(_NOW - timedelta(hours=12))
        step = timedelta(minutes=10)
        t = half_start
        wl_obs = []
        while t < half_end:
            wl_obs.append(_obs(_SID_A, t, parameter="water_level"))
            t = UtcDatetime(t + step)
        store.store_observations(wl_obs)

        summary = compute_coverage_summary(
            store,
            station_ids=[_SID_A],
            now=_NOW,
            parameters=["discharge", "water_level"],
            window_hours=24,
            expected_cadence_minutes=10,
        )

        assert len(summary.rows) == 2
        discharge_row = next(r for r in summary.rows if r.parameter == "discharge")
        wl_row = next(r for r in summary.rows if r.parameter == "water_level")
        assert discharge_row.coverage_pct == pytest.approx(100.0, abs=0.1)
        assert wl_row.coverage_pct == pytest.approx(50.0, abs=0.5)
        assert summary.overall_coverage_pct == pytest.approx(75.0, abs=0.5)


class TestComputeCoverageSummaryEmptyStore:
    def test_empty_store_no_rows(self) -> None:
        store = FakeObservationStore()

        summary = compute_coverage_summary(
            store,
            station_ids=[_SID_A, _SID_B],
            now=_NOW,
            parameters=["discharge"],
            window_hours=24,
            expected_cadence_minutes=10,
        )

        assert summary.rows == []
        assert summary.total_actual == 0
        assert summary.total_expected == 0
        assert summary.overall_coverage_pct == 0.0

    def test_empty_store_does_not_crash(self) -> None:
        store = FakeObservationStore()
        # Must not raise
        summary = compute_coverage_summary(
            store,
            station_ids=[],
            now=_NOW,
            parameters=["discharge", "water_level", "water_temperature"],
            window_hours=24,
            expected_cadence_minutes=10,
        )
        assert isinstance(summary, CoverageSummary)


class TestComputeCoverageSummaryNowInjection:
    def test_fixed_now_determines_window(self) -> None:
        """Passing a fixed now gives a deterministic window start."""
        fixed_now = ensure_utc(datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC))
        store = FakeObservationStore()

        summary = compute_coverage_summary(
            store,
            station_ids=[],
            now=fixed_now,
            parameters=["discharge"],
            window_hours=48,
            expected_cadence_minutes=10,
        )

        expected_start = ensure_utc(datetime(2026, 5, 30, 0, 0, 0, tzinfo=UTC))
        assert summary.window_start == expected_start
        assert summary.window_end == fixed_now
        assert summary.window_hours == 48
        assert summary.expected_cadence_minutes == 10


class TestComputeCoverageSummaryStationsWithNoObservationsOmitted:
    def test_station_with_no_obs_not_in_summary(self) -> None:
        """Stations with zero observations are silently omitted."""
        store = FakeObservationStore()
        store.store_observations(
            _even_observations(_SID_A, _NOW, 24, 10, parameter="discharge")
        )
        # _SID_B has no observations

        summary = compute_coverage_summary(
            store,
            station_ids=[_SID_A, _SID_B],
            now=_NOW,
            parameters=["discharge"],
            window_hours=24,
            expected_cadence_minutes=10,
        )

        station_ids_in_summary = {r.station_id for r in summary.rows}
        assert _SID_A in station_ids_in_summary
        assert _SID_B not in station_ids_in_summary
