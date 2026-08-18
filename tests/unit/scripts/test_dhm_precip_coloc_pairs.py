"""Plan 182 (M-A10) — the co-located pair registry."""

from __future__ import annotations

import pytest

from scripts.dhm_precip.coloc_pairs import COLOCATED_PAIRS, ColocatedPair
from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS


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


class TestFullRecordBoundsAreRegistered:
    """D11 — 'The verdict is adjudicated on the FULL RECORD: DHM's full
    JJAS (2020-2025, 6 season-years) against Pyramid's full JJAS (Lukla
    2005-2023, Namche 2002-2023)'. The registry is where those bounds
    live, so a test can drive the pipeline through the REAL bounds instead
    of a synthetic window that bypasses them."""

    def test_dhm_full_record_is_the_real_2020_2025_span(self) -> None:
        for pair in COLOCATED_PAIRS:
            assert pair.dhm_start_year == 2020
            assert pair.dhm_end_year == 2025

    def test_pyramid_full_records_are_the_real_per_station_spans(self) -> None:
        by_station = {p.dhm_station: p for p in COLOCATED_PAIRS}
        lukla = by_station[Station("Lukla Airport")]
        namche = by_station[Station("Syangboche Airport")]
        assert (lukla.pyramid_start_year, lukla.pyramid_end_year) == (2005, 2023)
        assert (namche.pyramid_start_year, namche.pyramid_end_year) == (2002, 2023)

    def test_both_full_records_clear_the_five_season_adequacy_threshold(self) -> None:
        """D11 — 'Both sides clear the 5-season threshold comfortably, so a
        decisive verdict is reachable in production.'"""
        minimum = DEFAULT_PARAMS.coloc_min_season_years_for_adequacy
        for pair in COLOCATED_PAIRS:
            assert pair.dhm_end_year - pair.dhm_start_year + 1 >= minimum
            assert pair.pyramid_end_year - pair.pyramid_start_year + 1 >= minimum

    def test_rejects_an_overlap_window_outside_the_full_records(self) -> None:
        with pytest.raises(ValueError, match="overlap"):
            ColocatedPair(
                dhm_station=Station("X"),
                pyramid_station=Station("Y"),
                pyramid_csv_filename="x.csv",
                separation_km=1.0,
                elevation_delta_m=1.0,
                overlap_start_year=2019,
                overlap_end_year=2023,
                pyramid_start_year=2005,
                pyramid_end_year=2023,
            )

    def test_rejects_pyramid_start_after_pyramid_end(self) -> None:
        with pytest.raises(ValueError, match="pyramid_start_year"):
            ColocatedPair(
                dhm_station=Station("X"),
                pyramid_station=Station("Y"),
                pyramid_csv_filename="x.csv",
                separation_km=1.0,
                elevation_delta_m=1.0,
                pyramid_start_year=2024,
                pyramid_end_year=2023,
            )


class TestPyramidStationaritySplitIsRegistered:
    """D12 — 'The pre-2020 vs 2020+ split is PYRAMID's (1994/2002/2005-2023)',
    never DHM's (which has no pre-2020 data at all)."""

    def test_pyramid_split_year_is_2020_and_straddles_every_pyramid_record(
        self,
    ) -> None:
        split = DEFAULT_PARAMS.coloc_pyramid_stationarity_split_year
        assert split == 2020
        for pair in COLOCATED_PAIRS:
            assert pair.pyramid_start_year < split <= pair.pyramid_end_year
