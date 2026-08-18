"""Plan 182 (M-A10) — the Pyramid Lvl1 CSV loader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.pyramid_loader import (
    PRECIP_COLUMN,
    TIMESTAMP_COLUMN,
    PyramidParseFailureError,
    PyramidSchemaMismatchError,
    PyramidSourceUnreadableError,
    load_pyramid_lvl1_csv,
)

if TYPE_CHECKING:
    from pathlib import Path

_STATION = Station("AWS3 Lukla")


def _write_csv(path: Path, rows: str) -> None:
    path.write_text(f"{TIMESTAMP_COLUMN},{PRECIP_COLUMN}\n{rows}")


class TestLoadPyramidLvl1Csv:
    def test_loads_station_timestamp_and_value(self, tmp_path: Path) -> None:
        path = tmp_path / "AWS3_Z2660_Lvl1.csv"
        _write_csv(path, "2021-07-01 00:00:00,0.2\n2021-07-01 01:00:00,0.0\n")
        frame = load_pyramid_lvl1_csv(path, station=_STATION)
        assert frame.height == 2
        assert frame["station"].to_list() == [str(_STATION), str(_STATION)]
        assert frame["value_mm"].to_list() == [0.2, 0.0]

    def test_missing_file_raises_unreadable(self, tmp_path: Path) -> None:
        with pytest.raises(PyramidSourceUnreadableError):
            load_pyramid_lvl1_csv(tmp_path / "missing.csv", station=_STATION)

    def test_missing_precip_column_raises_schema_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.csv"
        path.write_text(f"{TIMESTAMP_COLUMN},OTHER\n2021-07-01 00:00:00,1\n")
        with pytest.raises(PyramidSchemaMismatchError):
            load_pyramid_lvl1_csv(path, station=_STATION)

    def test_non_numeric_precip_value_raises_parse_failure(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bad_value.csv"
        _write_csv(path, "2021-07-01 00:00:00,not_a_number\n")
        with pytest.raises(PyramidParseFailureError):
            load_pyramid_lvl1_csv(path, station=_STATION)
