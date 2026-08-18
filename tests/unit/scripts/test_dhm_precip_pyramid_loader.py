"""Plan 182 (M-A10) — the Pyramid Lvl1 CSV loader.

Residual risk (unchanged from the module docstring): the real Zenodo Lvl1
files are not present in this workspace, so the exact column names cannot
be verified against a real file here. The header literals below are
INTENTIONALLY NOT imported from `pyramid_loader`'s own `TIMESTAMP_COLUMN`/
`PRECIP_COLUMN` constants — a fixture built from the implementation's own
constants can never catch a wrong constant, only a genuinely independent
literal can; `TestExpectedColumnNames` is the tripwire that keeps the two
in sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.pyramid_loader import (
    PRECIP_COLUMN,
    TIMESTAMP_COLUMN,
    PyramidDuplicateTimestampError,
    PyramidInvalidTimestampError,
    PyramidParseFailureError,
    PyramidSchemaMismatchError,
    PyramidSourceUnreadableError,
    load_pyramid_lvl1_csv,
)

if TYPE_CHECKING:
    from pathlib import Path

_STATION = Station("AWS3 Lukla")


def _write_csv(path: Path, rows: str) -> None:
    # Literal header, independent of the module's own constants (see
    # module docstring above).
    path.write_text(f"TIMESTAMP,RR\n{rows}")


class TestExpectedColumnNames:
    def test_constants_match_the_literal_schema_this_module_assumes(self) -> None:
        assert TIMESTAMP_COLUMN == "TIMESTAMP"
        assert PRECIP_COLUMN == "RR"


class TestLoadPyramidLvl1Csv:
    def test_loads_station_timestamp_and_value(self, tmp_path: Path) -> None:
        path = tmp_path / "AWS3_Z2660_Lvl1.csv"
        _write_csv(path, "2021-07-01 00:00:00,0.2\n2021-07-01 01:00:00,0.0\n")
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.retained.height == 2
        assert result.n_raw == 2
        assert result.n_retained == 2
        assert result.retained["station"].to_list() == [str(_STATION), str(_STATION)]
        assert result.retained["value_mm"].to_list() == [0.2, 0.0]

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

    def test_unparseable_timestamp_raises_invalid_timestamp(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bad_ts.csv"
        _write_csv(path, "not-a-date,0.2\n")
        with pytest.raises(PyramidInvalidTimestampError):
            load_pyramid_lvl1_csv(path, station=_STATION)

    def test_duplicate_timestamp_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "dup.csv"
        _write_csv(path, "2021-07-01 00:00:00,0.2\n2021-07-01 00:00:00,0.4\n")
        with pytest.raises(PyramidDuplicateTimestampError):
            load_pyramid_lvl1_csv(path, station=_STATION)


class TestPhysicalRangeBoundary:
    """D4 — the physical-range boundary Pyramid never had: finite values in
    `[qc_mask_range_check_value_min_mm, qc_mask_range_check_value_max_mm]`
    only, the SAME bounds DHM's own mask enforces (0.0-200.0mm by default).
    NaN/infinite/out-of-range values are excluded from `retained` — never
    zeroed, never left as a null a naive denominator would silently count
    as a dry hour."""

    def test_nan_value_is_excluded_and_counted(self, tmp_path: Path) -> None:
        path = tmp_path / "nan.csv"
        _write_csv(path, "2021-07-01 00:00:00,nan\n2021-07-01 01:00:00,0.2\n")
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.n_raw == 2
        assert result.n_nonfinite == 1
        assert result.n_out_of_range == 0
        assert result.retained.height == 1
        assert result.retained["value_mm"].to_list() == [0.2]

    def test_out_of_range_value_is_excluded_and_counted(self, tmp_path: Path) -> None:
        path = tmp_path / "range.csv"
        _write_csv(
            path,
            "2021-07-01 00:00:00,-5.0\n"
            "2021-07-01 01:00:00,9999999.0\n"
            "2021-07-01 02:00:00,0.4\n",
        )
        result = load_pyramid_lvl1_csv(path, station=_STATION, params=DEFAULT_PARAMS)
        assert result.n_raw == 3
        assert result.n_nonfinite == 0
        assert result.n_out_of_range == 2
        assert result.retained.height == 1
        assert result.retained["value_mm"].to_list() == [0.4]

    def test_boundary_values_are_retained_inclusive(self, tmp_path: Path) -> None:
        path = tmp_path / "boundary.csv"
        _write_csv(
            path,
            f"2021-07-01 00:00:00,{DEFAULT_PARAMS.qc_mask_range_check_value_min_mm}\n"
            f"2021-07-01 01:00:00,{DEFAULT_PARAMS.qc_mask_range_check_value_max_mm}\n",
        )
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.n_out_of_range == 0
        assert result.retained.height == 2
