"""Task 1b — types and parameters (D2, D6, D6b, D7, D9)."""

from __future__ import annotations

from datetime import UTC

import pytest

from scripts.dhm_precip.domain_types import (
    AxisStatus,
    Grain,
    RunManifest,
    Station,
    StationCoordinate,
    StationCoordinateTable,
    View,
)
from scripts.dhm_precip.params import DEFAULT_PARAMS, DhmPrecipParams


class TestView:
    def test_has_exactly_raw_and_on_grid(self) -> None:
        assert {member.value for member in View} == {"RAW", "ON_GRID"}


class TestAxisStatus:
    def test_has_exactly_the_four_documented_values(self) -> None:
        # NORMALIZED added by Plan 172 (M-A2) — the canonical hourly axis
        # is neither a raw diagnostic nor a provisional statistic.
        assert {member.value for member in AxisStatus} == {
            "AXIS_INDEPENDENT",
            "RAW_AXIS_DIAGNOSTIC",
            "RAW_PROVISIONAL",
            "NORMALIZED",
        }


class TestGrain:
    def test_has_exactly_the_three_d6b_grains(self) -> None:
        assert {member.value for member in Grain} == {
            "source_timestamp_rows",
            "station_timestamp_cells",
            "non_null_observations",
        }


class TestStationCoordinate:
    def test_rejects_out_of_range_latitude(self) -> None:
        with pytest.raises(ValueError, match="latitude"):
            StationCoordinate(
                station=Station("X"),
                excel_col="X (mm)",
                lat=91.0,
                lon=85.0,
                elev_m=100.0,
            )

    def test_rejects_out_of_range_longitude(self) -> None:
        with pytest.raises(ValueError, match="longitude"):
            StationCoordinate(
                station=Station("X"),
                excel_col="X (mm)",
                lat=27.0,
                lon=-200.0,
                elev_m=100.0,
            )

    def test_accepts_a_valid_nepal_coordinate(self) -> None:
        coord = StationCoordinate(
            station=Station("Khumaltar"),
            excel_col="Khumaltar (mm)",
            lat=27.65175,
            lon=85.32577,
            elev_m=1334.0,
        )
        assert coord.station == "Khumaltar"


class TestStationCoordinateTable:
    def test_rejects_empty_table(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            StationCoordinateTable(by_station={})

    def test_exposes_stations_property(self) -> None:
        coord = StationCoordinate(
            station=Station("Khumaltar"),
            excel_col="Khumaltar (mm)",
            lat=27.65175,
            lon=85.32577,
            elev_m=1334.0,
        )
        table = StationCoordinateTable(by_station={coord.station: coord})
        assert table.stations == ("Khumaltar",)


class TestDhmPrecipParams:
    def test_default_wet_threshold_matches_vision(self) -> None:
        assert DEFAULT_PARAMS.wet_threshold_mm_per_h == 0.2

    def test_rejects_non_positive_wet_threshold(self) -> None:
        with pytest.raises(ValueError, match="wet_threshold_mm_per_h"):
            DhmPrecipParams(wet_threshold_mm_per_h=0.0)

    def test_rejects_invalid_on_grid_minute(self) -> None:
        with pytest.raises(ValueError, match="on_grid_minute"):
            DhmPrecipParams(on_grid_minute=60)

    def test_rejects_quantile_grid_entry_out_of_open_unit_interval(self) -> None:
        with pytest.raises(ValueError, match="quantile_grid"):
            DhmPrecipParams(quantile_grid=(0.5, 1.0))

    def test_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            DEFAULT_PARAMS.wet_threshold_mm_per_h = 0.5  # type: ignore[misc]


class TestRunManifest:
    def test_constructs_with_empty_defaults(self) -> None:
        from datetime import datetime

        manifest = RunManifest(
            run_id="test-run",
            source_path="data/dhm_precip/combined_precipitation_37_stations.xlsx",
            source_sha256="8dc57e43" + "0" * 56,
            generated_at=datetime(2026, 8, 13, tzinfo=UTC),
            parameters={},
        )
        assert manifest.tables == ()
        assert manifest.values == {}
