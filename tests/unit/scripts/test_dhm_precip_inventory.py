"""Task 2a — inventory and coverage. Synthetic frames only (D4: pure
functions of (view, params, stations), no I/O)."""

from __future__ import annotations

from datetime import datetime

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import (
    LongFrameInventory,
    Station,
    StationCoordinate,
    StationCoordinateTable,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.stats_inventory import (
    group_elevation_overlap,
    hourly_reporting_simultaneity,
    station_span_and_coverage,
    usable_station_inventory,
)


class TestUsableStationInventory:
    def test_flags_empty_columns_as_unusable(self) -> None:
        inventory = LongFrameInventory(
            all_columns=("A", "B", "C"), empty_columns=("B",), total_rows=10
        )
        result = usable_station_inventory(inventory)
        by_station = dict(
            zip(result["station"].to_list(), result["is_usable"].to_list(), strict=True)
        )
        assert by_station == {"A": True, "B": False, "C": True}

    def test_tags_axis_independent(self) -> None:
        inventory = LongFrameInventory(
            all_columns=("A",), empty_columns=(), total_rows=1
        )
        result = usable_station_inventory(inventory)
        assert result["axis_status"].unique().to_list() == ["AXIS_INDEPENDENT"]


def _on_grid_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


class TestHourlyReportingSimultaneity:
    def test_counts_reporting_stations_per_hour(self) -> None:
        frame = _on_grid_frame(
            [
                {"station": "A", "timestamp": datetime(2024, 6, 1, 0), "value_mm": 0.0},
                {
                    "station": "B",
                    "timestamp": datetime(2024, 6, 1, 0),
                    "value_mm": None,
                },
                {"station": "A", "timestamp": datetime(2024, 6, 1, 1), "value_mm": 1.0},
                {"station": "B", "timestamp": datetime(2024, 6, 1, 1), "value_mm": 2.0},
            ]
        )
        result = hourly_reporting_simultaneity(frame)
        counts = dict(
            zip(
                result["timestamp"].to_list(),
                result["reporting_station_count"].to_list(),
                strict=True,
            )
        )
        assert counts[datetime(2024, 6, 1, 0)] == 1
        assert counts[datetime(2024, 6, 1, 1)] == 2


class TestStationSpanAndCoverage:
    def test_computes_first_last_and_coverage_fraction(self) -> None:
        frame = _on_grid_frame(
            [
                {"station": "A", "timestamp": datetime(2024, 6, 1, 0), "value_mm": 0.0},
                {
                    "station": "A",
                    "timestamp": datetime(2024, 6, 1, 1),
                    "value_mm": None,
                },
                {"station": "A", "timestamp": datetime(2024, 6, 1, 2), "value_mm": 1.0},
                {"station": "B", "timestamp": datetime(2024, 6, 1, 0), "value_mm": 0.0},
                {"station": "B", "timestamp": datetime(2024, 6, 1, 1), "value_mm": 0.0},
                {"station": "B", "timestamp": datetime(2024, 6, 1, 2), "value_mm": 0.0},
            ]
        )
        result = station_span_and_coverage(frame).sort("station")
        row_a = result.filter(pl.col("station") == "A")
        assert row_a["non_null_count"][0] == 2
        assert row_a["coverage_fraction"][0] == pytest.approx(2 / 3)
        assert row_a["first_timestamp"][0] == datetime(2024, 6, 1, 0)
        assert row_a["last_timestamp"][0] == datetime(2024, 6, 1, 2)


class TestGroupElevationOverlap:
    def test_computes_group_a_min_minus_group_b_max(self) -> None:
        # Station X reports at 0.01 mm resolution (Group A); Y at 0.2 mm (Group B).
        rows = []
        for hour, val in enumerate([0.01, 0.03, 0.0, 0.07]):
            rows.append(
                {
                    "station": "X",
                    "timestamp": datetime(2024, 6, 1, hour),
                    "value_mm": val,
                }
            )
        for hour, val in enumerate([0.2, 0.4, 0.0, 0.6]):
            rows.append(
                {
                    "station": "Y",
                    "timestamp": datetime(2024, 6, 1, hour),
                    "value_mm": val,
                }
            )
        frame = _on_grid_frame(rows)
        stations = StationCoordinateTable(
            by_station={
                Station("X"): StationCoordinate(
                    station=Station("X"),
                    excel_col="X (mm)",
                    lat=27.0,
                    lon=85.0,
                    elev_m=3000.0,
                ),
                Station("Y"): StationCoordinate(
                    station=Station("Y"),
                    excel_col="Y (mm)",
                    lat=27.0,
                    lon=85.0,
                    elev_m=1000.0,
                ),
            }
        )
        result = group_elevation_overlap(frame, stations, DEFAULT_PARAMS)
        gap_row = result.filter(pl.col("group") == "A")
        assert gap_row["group_a_min_minus_group_b_max_m"][0] == pytest.approx(2000.0)
