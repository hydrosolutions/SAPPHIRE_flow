"""Task 2b — time-axis diagnostics. `RAW`/`RAW_AXIS_DIAGNOSTIC` only (D6)."""

from __future__ import annotations

from datetime import datetime

import polars as pl

from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.stats_axis import (
    off_grid_minute_distribution,
    off_grid_observation_diagnostics,
    per_station_off_grid_attribution,
    row_count_diagnostics,
)


def _raw_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


class TestRowCountDiagnostics:
    def test_counts_rows_slots_and_off_grid(self) -> None:
        # Two stations x three source rows: two on-grid hours (0,1) and one
        # off-grid (minute 15), with a genuine gap between hour 1 and hour 3
        # (clean_hourly_slot_count should span min..max regardless).
        frame = _raw_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0, 0),
                    "value_mm": 0.0,
                },
                {
                    "source_row_index": 0,
                    "station": "B",
                    "timestamp": datetime(2024, 1, 1, 0, 0),
                    "value_mm": 0.0,
                },
                {
                    "source_row_index": 1,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 1, 0),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 1,
                    "station": "B",
                    "timestamp": datetime(2024, 1, 1, 1, 0),
                    "value_mm": None,
                },
                {
                    "source_row_index": 2,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 1, 15),
                    "value_mm": 0.5,
                },
                {
                    "source_row_index": 2,
                    "station": "B",
                    "timestamp": datetime(2024, 1, 1, 1, 15),
                    "value_mm": None,
                },
            ]
        )
        result = row_count_diagnostics(frame, DEFAULT_PARAMS)
        assert result["total_rows"][0] == 3
        assert result["off_grid_row_count"][0] == 1
        assert result["duplicate_timestamp_count"][0] == 0
        assert result["timestamp_monotonic"][0] is True
        # min=00:00, max=01:15 -> hourly slots 00:00, 01:00 = 2 (01:15 itself
        # is off-grid and not a clean hourly slot).
        assert result["clean_hourly_slot_count"][0] == 2

    def test_detects_duplicate_timestamps(self) -> None:
        frame = _raw_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0, 0),
                    "value_mm": 0.0,
                },
                {
                    "source_row_index": 1,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0, 0),
                    "value_mm": 0.0,
                },
            ]
        )
        result = row_count_diagnostics(frame, DEFAULT_PARAMS)
        assert result["duplicate_timestamp_count"][0] == 1

    def test_detects_a_non_monotonic_delivery_order(self) -> None:
        # source_row_index ascends (the file's own row order) but the
        # timestamps it carries go 02:00 -> 00:00 -> 01:00 — a genuinely
        # reordered workbook. A buggy implementation that sorts timestamps
        # before checking `.is_sorted()` would report this as monotonic;
        # the real answer, checked against delivery order, is False.
        frame = _raw_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 2, 0),
                    "value_mm": 0.0,
                },
                {
                    "source_row_index": 1,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0, 0),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 2,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 1, 0),
                    "value_mm": 2.0,
                },
            ]
        )
        result = row_count_diagnostics(frame, DEFAULT_PARAMS)
        assert result["timestamp_monotonic"][0] is False


class TestOffGridObservationDiagnostics:
    def test_fraction_uses_observation_grain_not_row_grain(self) -> None:
        # D6b: off-grid rows are sparser at observation grain than row grain.
        frame = _raw_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0, 0),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 0,
                    "station": "B",
                    "timestamp": datetime(2024, 1, 1, 0, 0),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 1,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0, 15),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 1,
                    "station": "B",
                    "timestamp": datetime(2024, 1, 1, 0, 15),
                    "value_mm": None,
                },
            ]
        )
        result = off_grid_observation_diagnostics(frame, DEFAULT_PARAMS)
        assert result["off_grid_observation_count"][0] == 1
        assert result["total_non_null_observations"][0] == 3


class TestOffGridMinuteDistribution:
    def test_lists_distinct_off_grid_minutes_with_counts(self) -> None:
        frame = _raw_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 0, 15),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 1,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 1, 15),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 2,
                    "station": "A",
                    "timestamp": datetime(2024, 1, 1, 2, 45),
                    "value_mm": 1.0,
                },
            ]
        )
        result = off_grid_minute_distribution(frame, DEFAULT_PARAMS).sort("minute")
        assert result["minute"].to_list() == [15, 45]
        assert result["row_count"].to_list() == [2, 1]


class TestPerStationOffGridAttribution:
    def test_ranks_stations_by_off_grid_observation_count(self) -> None:
        frame = _raw_frame(
            [
                {
                    "source_row_index": 0,
                    "station": "Lukla",
                    "timestamp": datetime(2024, 1, 1, 0, 15),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 0,
                    "station": "Lukla",
                    "timestamp": datetime(2024, 1, 1, 0, 15),
                    "value_mm": 1.0,
                },
                {
                    "source_row_index": 1,
                    "station": "Other",
                    "timestamp": datetime(2024, 1, 1, 1, 15),
                    "value_mm": 1.0,
                },
            ]
        )
        result = per_station_off_grid_attribution(frame, DEFAULT_PARAMS)
        assert result["station"][0] == "Lukla"
