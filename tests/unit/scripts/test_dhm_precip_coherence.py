"""Task 2f — coherence, diurnal and geometry. Single owner of all coherence
computation (D12)."""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import (
    Station,
    StationCoordinate,
    StationCoordinateTable,
)
from scripts.dhm_precip.params import DhmPrecipParams
from scripts.dhm_precip.stats_coherence import (
    diurnal_profile_correlations,
    diurnal_profiles,
    frequency_correlations,
    interannual_diurnal_stability,
    nearest_neighbour_distances,
    pair_count_within,
    pairwise_distances,
    undistanced_median_r,
)


def _stations(*rows: tuple[str, float, float]) -> StationCoordinateTable:
    return StationCoordinateTable(
        by_station={
            Station(name): StationCoordinate(
                station=Station(name),
                excel_col=f"{name} (mm)",
                lat=lat,
                lon=lon,
                elev_m=1000.0,
            )
            for name, lat, lon in rows
        }
    )


class TestPairwiseDistances:
    def test_zero_distance_for_identical_coordinates(self) -> None:
        stations = _stations(("A", 27.0, 85.0), ("B", 27.0, 85.0))
        result = pairwise_distances(stations)
        assert result["distance_km"][0] == pytest.approx(0.0, abs=1e-6)

    def test_tags_axis_independent(self) -> None:
        stations = _stations(("A", 27.0, 85.0), ("B", 28.0, 86.0))
        result = pairwise_distances(stations)
        assert result["axis_status"][0] == "AXIS_INDEPENDENT"

    def test_known_distance_kathmandu_to_pokhara_ballpark(self) -> None:
        # Kathmandu (27.7172, 85.3240) to Pokhara (28.2096, 83.9856) ~ 145 km.
        stations = _stations(
            ("Kathmandu", 27.7172, 85.3240), ("Pokhara", 28.2096, 83.9856)
        )
        result = pairwise_distances(stations)
        assert result["distance_km"][0] == pytest.approx(145, rel=0.05)


class TestNearestNeighbourDistances:
    def test_each_station_gets_its_minimum_distance(self) -> None:
        stations = _stations(("A", 27.0, 85.0), ("B", 27.01, 85.0), ("C", 30.0, 85.0))
        pairwise = pairwise_distances(stations)
        nn = nearest_neighbour_distances(pairwise)
        by_station = dict(
            zip(
                nn["station"].to_list(),
                nn["nearest_neighbour_km"].to_list(),
                strict=True,
            )
        )
        # A's neighbour B is much closer than C's nearest.
        assert by_station["A"] < by_station["C"]


class TestPairCountWithin:
    def test_counts_pairs_under_the_threshold(self) -> None:
        stations = _stations(("A", 27.0, 85.0), ("B", 27.01, 85.0), ("C", 30.0, 85.0))
        pairwise = pairwise_distances(stations)
        assert pair_count_within(pairwise, 25.0) == 1  # only A-B is under 25km


def _on_grid_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).with_columns(pl.col("timestamp").cast(pl.Datetime("ms")))


class TestFrequencyCorrelations:
    def test_perfectly_correlated_stations_give_r_near_one(self) -> None:
        start = datetime(2024, 6, 1)
        rows = []
        for i in range(150):
            ts = start + timedelta(hours=i)
            value = float(i % 5)
            rows.append({"station": "A", "timestamp": ts, "value_mm": value})
            rows.append({"station": "B", "timestamp": ts, "value_mm": value})
        frame = _on_grid_frame(rows)
        stations = _stations(("A", 27.0, 85.0), ("B", 27.0, 85.0))
        params = DhmPrecipParams(min_paired_samples=10)
        correlations = frequency_correlations(frame, stations, params)
        assert correlations["hourly"][("A", "B")] == pytest.approx(1.0, abs=1e-6)

    def test_pairs_below_min_paired_samples_are_excluded(self) -> None:
        start = datetime(2024, 6, 1)
        rows = [
            {
                "station": "A",
                "timestamp": start + timedelta(hours=i),
                "value_mm": float(i),
            }
            for i in range(5)
        ]
        rows += [
            {
                "station": "B",
                "timestamp": start + timedelta(hours=i),
                "value_mm": float(i),
            }
            for i in range(5)
        ]
        frame = _on_grid_frame(rows)
        stations = _stations(("A", 27.0, 85.0), ("B", 27.0, 85.0))
        params = DhmPrecipParams(min_paired_samples=100)
        correlations = frequency_correlations(frame, stations, params)
        assert ("A", "B") not in correlations["hourly"]


class TestDailyBucketExcludesIncompleteDays:
    def test_an_all_null_station_day_is_dropped_not_summed_to_zero(self) -> None:
        # Six JJAS days where A and B move in perfect lockstep (daily totals
        # 1..6 mm, spread evenly over each day's 24 ON_GRID hours), plus a
        # seventh day where A has NO data at all (every hour null) while B
        # reports 7 mm. Polars' `.sum()` over an all-null group returns 0.0,
        # not null — so a buggy implementation that never filters on
        # `hours_present` would silently record A's day 7 total as 0.0 mm
        # and pair it against B's 7 mm, corrupting an otherwise perfect
        # correlation. The correct behaviour drops day 7 for A entirely (0
        # of 24 hours present, far below `daily_completeness_min_hours`),
        # so the wide pivot's `drop_nulls()` excludes the pair and the
        # remaining six perfectly-matched days give r == 1.0 exactly.
        start = datetime(2024, 6, 1)
        rows: list[dict[str, object]] = []
        for day in range(6):
            daily_total = float(day + 1)
            for hour in range(24):
                ts = start + timedelta(days=day, hours=hour)
                rows.append(
                    {"station": "A", "timestamp": ts, "value_mm": daily_total / 24}
                )
                rows.append(
                    {"station": "B", "timestamp": ts, "value_mm": daily_total / 24}
                )
        # Day 7 (index 6): A entirely null, B fully present at 7 mm/day.
        for hour in range(24):
            ts = start + timedelta(days=6, hours=hour)
            rows.append({"station": "A", "timestamp": ts, "value_mm": None})
            rows.append({"station": "B", "timestamp": ts, "value_mm": 7.0 / 24})
        frame = _on_grid_frame(rows)
        stations = _stations(("A", 27.0, 85.0), ("B", 27.0, 85.0))
        params = DhmPrecipParams(min_paired_samples=3)
        correlations = frequency_correlations(frame, stations, params)
        assert correlations["daily"][("A", "B")] == pytest.approx(1.0, abs=1e-9)


class TestUndistancedMedianR:
    def test_median_of_the_correlation_values(self) -> None:
        correlations = {("A", "B"): 0.2, ("A", "C"): 0.4, ("B", "C"): 0.6}
        assert undistanced_median_r(correlations) == pytest.approx(0.4)


class TestDiurnalProfiles:
    def test_mean_value_by_hour_of_day(self) -> None:
        rows = [
            {"station": "A", "timestamp": datetime(2024, 6, 1, 0), "value_mm": 1.0},
            {"station": "A", "timestamp": datetime(2024, 6, 2, 0), "value_mm": 3.0},
        ]
        result = diurnal_profiles(_on_grid_frame(rows), DhmPrecipParams())
        assert result.filter(pl.col("hour") == 0)["mean_value_mm"][0] == pytest.approx(
            2.0
        )


class TestInterannualDiurnalStability:
    def test_identical_year_over_year_profiles_give_r_near_one(self) -> None:
        rows = []
        for year in (2021, 2022, 2023):
            for hour, value in enumerate([1.0, 2.0, 3.0, 4.0]):
                rows.append(
                    {
                        "station": "A",
                        "timestamp": datetime(year, 6, 1, hour),
                        "value_mm": value,
                    }
                )
        frame = _on_grid_frame(rows)
        result = interannual_diurnal_stability(
            frame, DhmPrecipParams(min_diurnal_paired_hours=2)
        )
        row_a = result.filter(pl.col("station") == "A")
        assert row_a["interannual_diurnal_stability_median_r"][0] == pytest.approx(
            1.0, abs=1e-6
        )
        assert row_a["n_year_pairs"][0] == 3  # 2021-22, 2021-23, 2022-23

    def test_a_single_year_of_data_yields_no_pairs(self) -> None:
        rows = [
            {"station": "A", "timestamp": datetime(2024, 6, 1, h), "value_mm": 1.0}
            for h in range(4)
        ]
        frame = _on_grid_frame(rows)
        result = interannual_diurnal_stability(
            frame, DhmPrecipParams(min_diurnal_paired_hours=2)
        )
        row_a = result.filter(pl.col("station") == "A")
        assert row_a["n_year_pairs"][0] == 0
        assert row_a["interannual_diurnal_stability_median_r"][0] is None


class TestDiurnalProfileCorrelations:
    def test_identical_profiles_correlate_perfectly(self) -> None:
        profiles = pl.DataFrame(
            {
                "station": ["A"] * 4 + ["B"] * 4,
                "hour": [0, 1, 2, 3] * 2,
                "mean_value_mm": [1.0, 2.0, 3.0, 4.0] * 2,
            }
        )
        result = diurnal_profile_correlations(
            profiles,
            (Station("A"), Station("B")),
            DhmPrecipParams(min_diurnal_paired_hours=2),
        )
        assert result[("A", "B")] == pytest.approx(1.0, abs=1e-6)
