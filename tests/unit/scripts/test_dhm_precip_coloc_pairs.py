"""Plan 182 (M-A10) — the co-located pair registry."""

from __future__ import annotations

import pytest

from scripts.dhm_precip.coloc_pairs import COLOCATED_PAIRS, ColocatedPair
from scripts.dhm_precip.domain_types import Station


class TestColocatedPairs:
    def test_exactly_two_pairs(self) -> None:
        assert len(COLOCATED_PAIRS) == 2

    def test_dhm_stations_are_lukla_and_syangboche(self) -> None:
        dhm_stations = {pair.dhm_station for pair in COLOCATED_PAIRS}
        assert dhm_stations == {
            Station("Lukla Airport"),
            Station("Syangboche Airport"),
        }

    def test_rejects_nonpositive_separation(self) -> None:
        with pytest.raises(ValueError, match="separation_km must be positive"):
            ColocatedPair(
                dhm_station=Station("X"),
                pyramid_station=Station("Y"),
                pyramid_csv_filename="x.csv",
                separation_km=0.0,
                elevation_delta_m=1.0,
            )

    def test_rejects_negative_elevation_delta(self) -> None:
        with pytest.raises(ValueError, match="elevation_delta_m must be >= 0"):
            ColocatedPair(
                dhm_station=Station("X"),
                pyramid_station=Station("Y"),
                pyramid_csv_filename="x.csv",
                separation_km=1.0,
                elevation_delta_m=-1.0,
            )

    def test_rejects_overlap_start_after_overlap_end(self) -> None:
        with pytest.raises(ValueError, match="overlap_start_year"):
            ColocatedPair(
                dhm_station=Station("X"),
                pyramid_station=Station("Y"),
                pyramid_csv_filename="x.csv",
                separation_km=1.0,
                elevation_delta_m=1.0,
                overlap_start_year=2023,
                overlap_end_year=2020,
            )

    def test_lukla_overlap_starts_2021_per_plan_d5(self) -> None:
        """D5a: 'Lukla's overlap is only 2021-2023.'"""
        lukla = next(
            p for p in COLOCATED_PAIRS if p.dhm_station == Station("Lukla Airport")
        )
        assert lukla.overlap_start_year == 2021
        assert lukla.overlap_end_year == 2023

    def test_syangboche_overlap_is_the_default_2020_2023(self) -> None:
        syangboche = next(
            p for p in COLOCATED_PAIRS if p.dhm_station == Station("Syangboche Airport")
        )
        assert syangboche.overlap_start_year == 2020
        assert syangboche.overlap_end_year == 2023
