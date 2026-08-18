"""Plan 182 (M-A10) — the Pyramid Lvl1 CSV loader.

The fixtures here are byte-identical IN SHAPE to the real Zenodo Lvl1 files
(verified 2026-08-18): semicolon-delimited, CR-only line endings with a
final CRLF, the header `year;month;day;hour;AT;RR;AP;RH;WS;WD`, and an empty
field for every missing reading. The header literals below are INTENTIONALLY
NOT built from `pyramid_loader`'s own constants — a fixture built from the
implementation's own constants can never catch a wrong constant, only a
genuinely independent literal can; `TestExpectedColumnNames` is the tripwire
that keeps the two in sync.

`TestRealPyramidFiles` reads the actual Zenodo CSVs when
`DHM_PRECIP_PYRAMID_DIR` points at them, and skips cleanly when it is unset
(the same pattern `DHM_PRECIP_XLSX` uses for the DHM workbook).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import Station
from scripts.dhm_precip.params import DEFAULT_PARAMS
from scripts.dhm_precip.pyramid_loader import (
    PRECIP_COLUMN,
    SEPARATOR,
    TIME_COLUMNS,
    PyramidDuplicateTimestampError,
    PyramidInvalidTimestampError,
    PyramidParseFailureError,
    PyramidSchemaMismatchError,
    PyramidSourceUnreadableError,
    load_pyramid_lvl1_csv,
)

_STATION = Station("AWS3 Lukla")

_HEADER = "year;month;day;hour;AT;RR;AP;RH;WS;WD"


def _row(year: int, month: int, day: int, hour: int | str, rr: str) -> str:
    """One Lvl1 data row: the four time columns, then AT, RR, AP, RH, WS, WD
    with everything but RR left empty (as most real rows are)."""
    return f"{year};{month};{day};{hour};;{rr};;;;"


def _write_pyramid_csv(path: Path, rows: list[str], *, header: str = _HEADER) -> None:
    path.write_bytes(("\r".join([header, *rows]) + "\r\n").encode("utf-8"))


class TestExpectedColumnNames:
    def test_constants_match_the_literal_schema_this_module_assumes(self) -> None:
        assert SEPARATOR == ";"
        assert TIME_COLUMNS == ("year", "month", "day", "hour")
        assert PRECIP_COLUMN == "RR"


class TestLoadPyramidLvl1Csv:
    def test_loads_station_timestamp_and_value(self, tmp_path: Path) -> None:
        path = tmp_path / "AWS3_Z2660_Lvl1.csv"
        _write_pyramid_csv(path, [_row(2021, 7, 1, 0, "0.2"), _row(2021, 7, 1, 1, "0")])
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.retained.height == 2
        assert result.n_raw == 2
        assert result.n_retained == 2
        assert result.retained["station"].to_list() == [str(_STATION), str(_STATION)]
        assert result.retained["value_mm"].to_list() == [0.2, 0.0]

    def test_timestamp_is_built_from_the_four_integer_time_columns(
        self, tmp_path: Path
    ) -> None:
        """There is no `TIMESTAMP` column in a real Lvl1 file — and the hour
        is a bare 0-23 integer, kept as UNCONVERTED NPT wall-clock (D2)."""
        path = tmp_path / "AWS3_Z2660_Lvl1.csv"
        _write_pyramid_csv(
            path, [_row(2021, 7, 1, 23, "0.4"), _row(2021, 7, 2, 0, "0.6")]
        )
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.retained["timestamp"].to_list() == [
            datetime(2021, 7, 1, 23),
            datetime(2021, 7, 2, 0),
        ]
        assert result.retained.schema["timestamp"] == pl.Datetime("us")

    def test_cr_only_line_endings_are_parsed_as_separate_rows(
        self, tmp_path: Path
    ) -> None:
        """The real files are classic-Mac CR-terminated: a reader that does
        not normalise them sees ONE enormous line."""
        path = tmp_path / "AWS3_Z2660_Lvl1.csv"
        rows = [_row(2021, 7, 1, hour, "0.2") for hour in range(24)]
        path.write_bytes(("\r".join([_HEADER, *rows]) + "\r").encode("utf-8"))
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.n_raw == 24
        assert result.n_retained == 24

    def test_empty_precip_field_is_missing_not_a_parse_failure(
        self, tmp_path: Path
    ) -> None:
        """Lvl1 is published ungapfilled: an empty `RR` is a genuine missing
        reading, dropped from `retained` — never a zero, never a crash."""
        path = tmp_path / "AWS3_Z2660_Lvl1.csv"
        _write_pyramid_csv(
            path,
            [
                _row(2021, 7, 1, 0, ""),
                _row(2021, 7, 1, 1, "0.2"),
                _row(2021, 7, 1, 2, ""),
            ],
        )
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.n_raw == 3
        assert result.n_nonfinite == 0
        assert result.n_out_of_range == 0
        assert result.n_retained == 1
        assert result.retained["value_mm"].to_list() == [0.2]

    def test_a_long_leading_run_of_empty_precip_still_reads_later_values(
        self, tmp_path: Path
    ) -> None:
        """The real files open with thousands of empty `RR` fields — a
        schema inferred from the head alone would discard the column."""
        path = tmp_path / "AWS3_Z2660_Lvl1.csv"
        rows = [_row(2021, 1, 1 + day // 24, day % 24, "") for day in range(200)]
        rows.append(_row(2021, 7, 1, 0, "1.2"))
        _write_pyramid_csv(path, rows)
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.n_raw == 201
        assert result.retained["value_mm"].to_list() == [1.2]

    def test_missing_file_raises_unreadable(self, tmp_path: Path) -> None:
        with pytest.raises(PyramidSourceUnreadableError):
            load_pyramid_lvl1_csv(tmp_path / "missing.csv", station=_STATION)

    def test_missing_precip_column_raises_schema_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.csv"
        _write_pyramid_csv(
            path,
            ["2021;7;1;0;;;;;;"],
            header="year;month;day;hour;AT;OTHER;AP;RH;WS;WD",
        )
        with pytest.raises(PyramidSchemaMismatchError, match="RR"):
            load_pyramid_lvl1_csv(path, station=_STATION)

    def test_missing_time_column_raises_schema_mismatch(self, tmp_path: Path) -> None:
        path = tmp_path / "bad_time.csv"
        _write_pyramid_csv(
            path, ["2021;7;1;;0.2;;;;"], header="year;month;day;AT;RR;AP;RH;WS;WD"
        )
        with pytest.raises(PyramidSchemaMismatchError, match="hour"):
            load_pyramid_lvl1_csv(path, station=_STATION)

    def test_comma_delimited_file_fails_loudly_rather_than_silently(
        self, tmp_path: Path
    ) -> None:
        """A file in some other dialect must never parse to an empty or
        one-column frame that downstream code reads as "no data"."""
        path = tmp_path / "comma.csv"
        path.write_text("year,month,day,hour,AT,RR,AP,RH,WS,WD\n2021,7,1,0,,0.2,,,,\n")
        with pytest.raises(PyramidSchemaMismatchError):
            load_pyramid_lvl1_csv(path, station=_STATION)

    def test_non_numeric_precip_value_raises_parse_failure(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bad_value.csv"
        _write_pyramid_csv(path, [_row(2021, 7, 1, 0, "not_a_number")])
        with pytest.raises(PyramidParseFailureError):
            load_pyramid_lvl1_csv(path, station=_STATION)

    def test_non_integer_hour_raises_invalid_timestamp(self, tmp_path: Path) -> None:
        path = tmp_path / "bad_ts.csv"
        _write_pyramid_csv(path, [_row(2021, 7, 1, "not-an-hour", "0.2")])
        with pytest.raises(PyramidInvalidTimestampError):
            load_pyramid_lvl1_csv(path, station=_STATION)

    def test_empty_time_field_raises_invalid_timestamp(self, tmp_path: Path) -> None:
        path = tmp_path / "empty_ts.csv"
        _write_pyramid_csv(path, [";;;;;0.2;;;;"])
        with pytest.raises(PyramidInvalidTimestampError):
            load_pyramid_lvl1_csv(path, station=_STATION)

    def test_out_of_range_month_raises_invalid_timestamp(self, tmp_path: Path) -> None:
        path = tmp_path / "bad_month.csv"
        _write_pyramid_csv(path, [_row(2021, 13, 1, 0, "0.2")])
        with pytest.raises(PyramidInvalidTimestampError):
            load_pyramid_lvl1_csv(path, station=_STATION)

    def test_duplicate_timestamp_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "dup.csv"
        _write_pyramid_csv(
            path, [_row(2021, 7, 1, 0, "0.2"), _row(2021, 7, 1, 0, "0.4")]
        )
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
        _write_pyramid_csv(
            path, [_row(2021, 7, 1, 0, "nan"), _row(2021, 7, 1, 1, "0.2")]
        )
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.n_raw == 2
        assert result.n_nonfinite == 1
        assert result.n_out_of_range == 0
        assert result.retained.height == 1
        assert result.retained["value_mm"].to_list() == [0.2]

    def test_out_of_range_value_is_excluded_and_counted(self, tmp_path: Path) -> None:
        path = tmp_path / "range.csv"
        _write_pyramid_csv(
            path,
            [
                _row(2021, 7, 1, 0, "-5.0"),
                _row(2021, 7, 1, 1, "9999999.0"),
                _row(2021, 7, 1, 2, "0.4"),
            ],
        )
        result = load_pyramid_lvl1_csv(path, station=_STATION, params=DEFAULT_PARAMS)
        assert result.n_raw == 3
        assert result.n_nonfinite == 0
        assert result.n_out_of_range == 2
        assert result.retained.height == 1
        assert result.retained["value_mm"].to_list() == [0.4]

    def test_boundary_values_are_retained_inclusive(self, tmp_path: Path) -> None:
        path = tmp_path / "boundary.csv"
        _write_pyramid_csv(
            path,
            [
                _row(
                    2021, 7, 1, 0, str(DEFAULT_PARAMS.qc_mask_range_check_value_min_mm)
                ),
                _row(
                    2021, 7, 1, 1, str(DEFAULT_PARAMS.qc_mask_range_check_value_max_mm)
                ),
            ],
        )
        result = load_pyramid_lvl1_csv(path, station=_STATION)
        assert result.n_out_of_range == 0
        assert result.retained.height == 2


_PYRAMID_DIR_ENV = "DHM_PRECIP_PYRAMID_DIR"


@pytest.mark.skipif(
    not os.environ.get(_PYRAMID_DIR_ENV),
    reason=f"{_PYRAMID_DIR_ENV} unset — the real Zenodo Lvl1 CSVs are not "
    "in this workspace (the directory is gitignored)",
)
class TestRealPyramidFiles:
    """Read the ACTUAL Zenodo Lvl1 files. The counts below were measured on
    them (2026-08-18) and are the ground truth D7's matched-resolution
    design rests on: the JJAS retained population, and a 0.2 mm smallest
    positive reading with NOTHING beneath it."""

    @staticmethod
    def _jjas_values(filename: str, station: str) -> list[float]:
        directory = Path(os.environ[_PYRAMID_DIR_ENV])
        result = load_pyramid_lvl1_csv(directory / filename, station=Station(station))
        jjas = result.retained.filter(
            pl.col("timestamp").dt.month().is_in([6, 7, 8, 9])
        )
        return jjas["value_mm"].to_list()

    @pytest.mark.parametrize(
        ("filename", "station", "n_jjas", "n_positive"),
        [
            ("AWS3_Z2660_Lvl1.csv", "AWS3 Lukla", 21_567, 7_133),
            ("AWS5_Z3570_Lvl1.csv", "AWS5 Namche", 33_180, 9_280),
        ],
    )
    def test_jjas_retained_population(
        self, filename: str, station: str, n_jjas: int, n_positive: int
    ) -> None:
        values = self._jjas_values(filename, station)
        assert len(values) == n_jjas
        assert sum(1 for v in values if v > 0) == n_positive

    @pytest.mark.parametrize(
        ("filename", "station"),
        [
            ("AWS3_Z2660_Lvl1.csv", "AWS3 Lukla"),
            ("AWS5_Z3570_Lvl1.csv", "AWS5 Namche"),
        ],
    )
    def test_smallest_positive_jjas_reading_is_exactly_0_2_mm(
        self, filename: str, station: str
    ) -> None:
        """The tipping-bucket resolution D7's matched-resolution design
        assumes. NOTE: 0.2 mm is the floor and the dominant quantum, but
        NOT every positive value is an integer multiple of it — roughly 4%
        of positive JJAS hours belong to a 0.24 mm-quantised sub-population
        (a different bucket/logger era), so only the floor is asserted."""
        positive = [v for v in self._jjas_values(filename, station) if v > 0]
        assert min(positive) == pytest.approx(0.2)
        assert [v for v in positive if v < 0.2] == []
