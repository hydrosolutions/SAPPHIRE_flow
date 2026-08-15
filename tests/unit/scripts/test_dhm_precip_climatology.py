"""Task 2e — climatology and missingness."""

from __future__ import annotations

from datetime import datetime

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import (
    Station,
    StationCoordinate,
    StationCoordinateTable,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.stats_climatology import (
    djf_share_of_total,
    monthly_climatology,
    per_year_totals_with_completeness,
    wet_biased_missingness_ratio,
)


def _on_grid_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


class TestMonthlyClimatology:
    def test_mean_intensity_is_coverage_normalised(self) -> None:
        rows = [
            {"station": "A", "timestamp": datetime(2020, 6, 1), "value_mm": 2.0},
            {"station": "A", "timestamp": datetime(2021, 6, 1), "value_mm": 4.0},
        ]
        result = monthly_climatology(_on_grid_frame(rows))
        row = result.filter(pl.col("month") == 6)
        assert row["mean_hourly_intensity_mm"][0] == pytest.approx(3.0)
        assert row["non_null_count"][0] == 2


class TestDjfShareOfTotal:
    def test_share_uses_djf_months_only(self) -> None:
        rows = [
            {
                "station": "Humde Airport",
                "timestamp": datetime(2024, 1, 1),
                "value_mm": 20.0,
            },
            {
                "station": "Humde Airport",
                "timestamp": datetime(2024, 12, 1),
                "value_mm": 20.0,
            },
            {
                "station": "Humde Airport",
                "timestamp": datetime(2024, 6, 1),
                "value_mm": 160.0,
            },
        ]
        result = djf_share_of_total(_on_grid_frame(rows), DEFAULT_PARAMS)
        assert result["djf_share_of_annual_total"][0] == pytest.approx(40.0 / 200.0)


class TestPerYearTotalsWithCompleteness:
    def test_flags_a_year_below_period_completeness(self) -> None:
        from datetime import timedelta

        # "Filler" defines the year's delivered hourly-slot denominator (200
        # distinct hours); "Sparse" only reports 20 of them (10% retained).
        possible_hours = [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(200)]
        rows = [
            {"station": "Filler", "timestamp": ts, "value_mm": 1.0}
            for ts in possible_hours
        ]
        rows += [
            {"station": "Sparse", "timestamp": ts, "value_mm": 1.0}
            for ts in possible_hours[:20]
        ]
        rows += [
            {"station": "Sparse", "timestamp": ts, "value_mm": None}
            for ts in possible_hours[20:]
        ]
        result = per_year_totals_with_completeness(_on_grid_frame(rows), DEFAULT_PARAMS)
        sparse_row = result.filter(pl.col("station") == "Sparse")
        assert sparse_row["retained_fraction"][0] == pytest.approx(20 / 200)
        assert sparse_row["period_complete"][0] is False
        filler_row = result.filter(pl.col("station") == "Filler")
        assert filler_row["period_complete"][0] is True

    def test_an_all_null_station_year_yields_a_null_total_not_zero(self) -> None:
        # D2b — Polars' .sum() over an all-null group returns 0.0, not
        # null; a station-year with zero retained hours must report a
        # NULL annual_total_mm, never a fabricated 0.0 mm (D2's
        # null-vs-zero guarantee surviving aggregation). A second,
        # genuinely-reporting station forces `value_mm`'s dtype to
        # Float64 — an all-None-list column would infer dtype Null and
        # trivially sum to null regardless of the fix, masking the trap.
        rows = [
            {
                "station": "AllMissing",
                "timestamp": datetime(2024, 1, 1, h),
                "value_mm": None,
            }
            for h in range(5)
        ] + [
            {"station": "Other", "timestamp": datetime(2024, 1, 1, 0), "value_mm": 5.0},
        ]
        result = per_year_totals_with_completeness(_on_grid_frame(rows), DEFAULT_PARAMS)
        row = result.filter(pl.col("station") == "AllMissing")
        assert row["retained_hours"][0] == 0
        assert row["annual_total_mm"][0] is None


def _coords(*names: str) -> StationCoordinateTable:
    return StationCoordinateTable(
        by_station={
            Station(name): StationCoordinate(
                station=Station(name),
                excel_col=f"{name} (mm)",
                lat=27.0,
                lon=85.0,
                elev_m=1000.0,
            )
            for name in names
        }
    )


class TestWetBiasedMissingnessRatio:
    def test_station_excluded_from_its_own_regional_wet_indicator(self) -> None:
        # Station "Self" is missing at every hour; "Other" carries the
        # regional wet/dry signal. Self's own (null) value must never count
        # toward its own regional indicator.
        rows = []
        for h in range(20):
            wet = h % 2 == 0
            rows.append(
                {
                    "station": "Other",
                    "timestamp": datetime(2024, 6, 1, h),
                    "value_mm": 5.0 if wet else 0.0,
                }
            )
            rows.append(
                {
                    "station": "Self",
                    "timestamp": datetime(2024, 6, 1, h),
                    "value_mm": None,
                }
            )
        result = wet_biased_missingness_ratio(
            _on_grid_frame(rows), _coords("Self", "Other"), DEFAULT_PARAMS
        )
        self_row = result.filter(pl.col("station") == "Self")
        # Self is missing 100% of the time regardless of region wet/dry ->
        # ratio should be well-defined and equal to 1 (no bias either way).
        assert self_row["wet_biased_missingness_ratio"][0] == pytest.approx(1.0)

    def test_stations_outside_the_coordinate_table_are_excluded_from_output(
        self,
    ) -> None:
        # A permanently-empty (never-usable) column must not appear in the
        # output at all — it would otherwise pollute the population's
        # median/max with a trivial degenerate ratio.
        rows = []
        for h in range(20):
            wet = h % 2 == 0
            rows.append(
                {
                    "station": "Usable",
                    "timestamp": datetime(2024, 6, 1, h),
                    "value_mm": 5.0 if wet else 0.0,
                }
            )
            rows.append(
                {
                    "station": "Other",
                    "timestamp": datetime(2024, 6, 1, h),
                    "value_mm": 5.0 if wet else 0.0,
                }
            )
            rows.append(
                {
                    "station": "AlwaysEmpty",
                    "timestamp": datetime(2024, 6, 1, h),
                    "value_mm": None,
                }
            )
        result = wet_biased_missingness_ratio(
            _on_grid_frame(rows), _coords("Usable", "Other"), DEFAULT_PARAMS
        )
        assert set(result["station"].to_list()) == {"Usable", "Other"}
        assert "AlwaysEmpty" not in result["station"].to_list()
