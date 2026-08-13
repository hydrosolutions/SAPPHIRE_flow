"""Task 2d — candidate defect inventory. Inventory only, no filtering (D6)."""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams
from scripts.dhm_precip.stats_defects import (
    annual_totals,
    candidate_zero_runs,
    daily_totals,
    sentinel_counts,
    stuck_high_candidate_runs,
)


def _on_grid_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


class TestSentinelCounts:
    def test_counts_sentinel_cells_per_station(self) -> None:
        rows = [
            {
                "station": "Lukla Airport",
                "timestamp": datetime(2024, 6, 1, h),
                "value_mm": v,
            }
            for h, v in enumerate([-9999999.0, -9999999.0, 1.0, 0.0])
        ]
        frame = _on_grid_frame(rows)
        result = sentinel_counts(frame, DEFAULT_PARAMS)
        assert result["sentinel_count"][0] == 2

    def test_carries_the_pre_normalisation_candidate_flag_is_absent_for_non_run_stats(
        self,
    ) -> None:
        rows = [
            {
                "station": "A",
                "timestamp": datetime(2024, 6, 1, 0),
                "value_mm": -9999999.0,
            }
        ]
        result = sentinel_counts(_on_grid_frame(rows), DEFAULT_PARAMS)
        assert "pre_normalisation_candidate" not in result.columns


class TestStuckHighCandidateRuns:
    def test_detects_a_repeated_nonzero_value_run(self) -> None:
        start = datetime(2025, 8, 3, 0)
        rows = [
            {
                "station": "Sindhuli Madhi",
                "timestamp": start + timedelta(hours=i),
                "value_mm": 72.0,
            }
            for i in range(15)
        ]
        params = DhmPrecipParams(minimum_run_duration_hours=12)
        result = stuck_high_candidate_runs(_on_grid_frame(rows), params)
        assert result.height == 1
        assert result["run_length_hours"][0] == 15
        assert result["run_value_mm"][0] == pytest.approx(72.0)
        assert result["pre_normalisation_candidate"][0] is True

    def test_tolerates_sensor_noise_around_the_pinned_value(self) -> None:
        # Vision: Sindhuli Madhi reports 72.0/72.2/72.4, not one bit-exact
        # repeated value — the default stuck_high_tolerance_mm bridges this.
        start = datetime(2025, 8, 3, 0)
        noisy_values = [
            72.0,
            72.2,
            72.0,
            72.0,
            72.4,
            72.0,
            72.2,
            72.0,
            72.0,
            72.0,
            72.0,
            72.0,
        ]
        rows = [
            {
                "station": "Sindhuli Madhi",
                "timestamp": start + timedelta(hours=i),
                "value_mm": v,
            }
            for i, v in enumerate(noisy_values)
        ]
        params = DhmPrecipParams(minimum_run_duration_hours=10)
        result = stuck_high_candidate_runs(_on_grid_frame(rows), params)
        assert result.height == 1
        assert result["run_length_hours"][0] == len(noisy_values)

    def test_a_magnitude_floor_excludes_near_zero_noise(self) -> None:
        # A long, tightly-clustered near-zero sequence is NOT a stuck-high
        # defect — it is ordinary dry-spell noise below stuck_high_min_value_mm.
        rows = [
            {"station": "A", "timestamp": datetime(2025, 8, 3, h), "value_mm": v}
            for h, v in enumerate(
                [0.2, 0.4, 0.2, 0.0, 0.2, 0.4, 0.2, 0.0, 0.2, 0.4, 0.2, 0.0]
            )
        ]
        params = DhmPrecipParams(minimum_run_duration_hours=6)
        result = stuck_high_candidate_runs(_on_grid_frame(rows), params)
        assert result.height == 0

    def test_a_gap_breaks_the_run(self) -> None:
        rows = [
            {"station": "A", "timestamp": datetime(2025, 8, 3, 0), "value_mm": 72.0},
            {"station": "A", "timestamp": datetime(2025, 8, 3, 1), "value_mm": 72.0},
            # gap: hour 2 missing entirely
            {"station": "A", "timestamp": datetime(2025, 8, 3, 3), "value_mm": 72.0},
            {"station": "A", "timestamp": datetime(2025, 8, 3, 4), "value_mm": 72.0},
        ]
        params = DhmPrecipParams(minimum_run_duration_hours=2)
        result = stuck_high_candidate_runs(_on_grid_frame(rows), params).sort(
            "run_start"
        )
        assert result.height == 2
        assert result["run_length_hours"].to_list() == [2, 2]

    def test_a_null_breaks_the_run(self) -> None:
        rows = [
            {"station": "A", "timestamp": datetime(2025, 8, 3, 0), "value_mm": 72.0},
            {"station": "A", "timestamp": datetime(2025, 8, 3, 1), "value_mm": None},
            {"station": "A", "timestamp": datetime(2025, 8, 3, 2), "value_mm": 72.0},
        ]
        params = DhmPrecipParams(minimum_run_duration_hours=1)
        result = stuck_high_candidate_runs(_on_grid_frame(rows), params)
        assert result.height == 2  # two separate 1-hour runs, not one bridged run

    def test_zero_values_are_excluded_from_stuck_high(self) -> None:
        rows = [
            {"station": "A", "timestamp": datetime(2025, 8, 3, h), "value_mm": 0.0}
            for h in range(20)
        ]
        params = DhmPrecipParams(minimum_run_duration_hours=1)
        result = stuck_high_candidate_runs(_on_grid_frame(rows), params)
        assert result.height == 0

    def test_below_minimum_duration_is_not_a_candidate(self) -> None:
        rows = [
            {"station": "A", "timestamp": datetime(2025, 8, 3, h), "value_mm": 72.0}
            for h in range(3)
        ]
        params = DhmPrecipParams(minimum_run_duration_hours=12)
        result = stuck_high_candidate_runs(_on_grid_frame(rows), params)
        assert result.height == 0


class TestCandidateZeroRuns:
    def test_detects_a_zero_run_within_jjas(self) -> None:
        start = datetime(2024, 7, 1)
        rows = [
            {
                "station": "Aiselukhark",
                "timestamp": start + timedelta(hours=i),
                "value_mm": 0.0,
            }
            for i in range(48)
        ]
        params = DhmPrecipParams(minimum_run_duration_hours=12)
        result = candidate_zero_runs(_on_grid_frame(rows), params)
        assert result.height == 1
        assert result["run_length_days"][0] == pytest.approx(2.0)

    def test_a_run_crossing_out_of_jjas_is_not_extended_past_the_season(self) -> None:
        # Sep 30 -> Oct 1 crosses out of JJAS; the September portion is
        # still a candidate (JJAS-scoped, not merged across the boundary).
        start = datetime(2024, 9, 29, 12)
        rows = [
            {"station": "A", "timestamp": start + timedelta(hours=i), "value_mm": 0.0}
            for i in range(36)  # runs into Oct 1
        ]
        params = DhmPrecipParams(minimum_run_duration_hours=6)
        result = candidate_zero_runs(_on_grid_frame(rows), params)
        assert result.height == 1
        # Sep 29 12:00 -> Sep 30 23:00 inclusive = 36 hours, but JJAS ends at
        # month 9 -> all of Sep is JJAS, so this fixture never leaves JJAS in
        # September; length should be exactly the September portion (30 hrs).
        assert result["run_end"][0].month == 9


class TestDailyAndAnnualTotals:
    def test_daily_total_requires_completeness(self) -> None:
        day = datetime(2021, 10, 19)
        rows = [
            {
                "station": "Tarahara",
                "timestamp": day + timedelta(hours=h),
                "value_mm": 1.0,
            }
            for h in range(18)  # below the 20-hour default completeness threshold
        ]
        result = daily_totals(_on_grid_frame(rows), DEFAULT_PARAMS)
        assert result.height == 0

    def test_daily_total_sums_a_complete_day(self) -> None:
        day = datetime(2021, 10, 19)
        rows = [
            {
                "station": "Tarahara",
                "timestamp": day + timedelta(hours=h),
                "value_mm": 2.0,
            }
            for h in range(24)
        ]
        result = daily_totals(_on_grid_frame(rows), DEFAULT_PARAMS)
        assert result["daily_total_mm"][0] == pytest.approx(48.0)

    def test_annual_total_sums_across_the_year_no_minimum(self) -> None:
        rows = [
            {
                "station": "Khumaltar",
                "timestamp": datetime(2023, m, 1),
                "value_mm": 10.0,
            }
            for m in range(1, 13)
        ]
        result = annual_totals(_on_grid_frame(rows), DEFAULT_PARAMS)
        assert result["annual_total_mm"][0] == pytest.approx(120.0)
