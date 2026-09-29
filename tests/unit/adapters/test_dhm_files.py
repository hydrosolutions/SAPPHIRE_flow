from pathlib import Path

import pytest

from sapphire_flow.adapters.dhm_files import (
    DhmFileFormatError,
    parse_daily_flow,
    parse_rating_tables,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "dhm"


def test_daily_flow_keeps_declared_years_and_values() -> None:
    parsed = parse_daily_flow((FIXTURES / "synthetic_daily_flow.txt").read_text())
    assert parsed.station_code == "999"
    assert parsed.declared_years == (1985, 1987)
    assert [row.discharge_m3s for row in parsed.values] == [1.5, 2.0, 3.25]


def test_daily_flow_rejects_declared_year_absent_from_data() -> None:
    text = "Daily Flow of Station: 999 in m3/s\n 1985\n 1986\n01/Jan/1985,1\n"
    with pytest.raises(DhmFileFormatError, match="declared years do not match"):
        parse_daily_flow(text)


def test_daily_flow_rejects_non_numeric_value() -> None:
    text = "Daily Flow of Station: 999 in m3/s\n 1985\n01/Jan/1985,bad\n"
    with pytest.raises(DhmFileFormatError, match="invalid discharge") as error:
        parse_daily_flow(text)
    assert error.value.__suppress_context__ is True
    assert "bad" not in str(error.value)


def test_rating_tables_keep_repeated_labels_and_negative_stage() -> None:
    parsed = parse_rating_tables((FIXTURES / "synthetic_rating_tables.txt").read_text())
    assert parsed.station_code == "999"
    assert [block.rating_type_label for block in parsed.blocks] == ["3", "3"]
    assert parsed.blocks[0].points[0].stage_m == -0.1


def test_rating_tables_reject_truncated_block() -> None:
    text = (FIXTURES / "synthetic_rating_tables.txt").read_text()
    with pytest.raises(DhmFileFormatError, match="truncated rating block"):
        parse_rating_tables(text + "\nRating Type No =  4\n")


def test_rating_tables_reject_unknown_line_inside_block() -> None:
    text = (FIXTURES / "synthetic_rating_tables.txt").read_text()
    with pytest.raises(DhmFileFormatError, match="unknown rating-block line"):
        parse_rating_tables(
            text.replace("0,2\n--------------------", "unknown\n--------------------")
        )
