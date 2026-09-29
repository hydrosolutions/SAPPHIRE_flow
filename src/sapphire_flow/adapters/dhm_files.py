"""Pure parsers for DHM's delivered daily-flow and rating-table text formats."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime


class DhmFileFormatError(ValueError):
    """A delivered DHM text file does not match its declared format."""


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyFlowValue:
    day: date
    discharge_m3s: float


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyFlowFile:
    station_code: str
    declared_years: tuple[int, ...]
    values: tuple[DailyFlowValue, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingPoint:
    stage_m: float
    discharge_m3s: float


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingTableBlock:
    rating_type_label: str
    from_date: date
    to_date: date
    from_stage_m: float
    points: tuple[RatingPoint, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingTableFile:
    station_code: str
    blocks: tuple[RatingTableBlock, ...]


_DAILY_TITLE = re.compile(r"Daily Flow of Station:\s*(.+?)\s+in m3/s\s*$")
_DAILY_VALUE = re.compile(r"(\d{2}/[A-Za-z]{3}/\d{4}),([^,]+)\s*$")
_YEAR = re.compile(r"\d{4}")
_RATING_TYPE = re.compile(r"Rating Type No\s*=\s*(\d+)\s*$")
_RATING_POINT = re.compile(r"([^,]+),([^,]+)\s*$")
_SEPARATOR = re.compile(r"-{3,}\s*$")


def _day(raw: str, fmt: str, *, line_number: int) -> date:
    try:
        return datetime.strptime(raw.strip(), fmt).date()
    except ValueError:
        raise DhmFileFormatError(f"line {line_number}: invalid date") from None


def _number(raw: str, *, line_number: int, label: str) -> float:
    try:
        value = float(raw.strip())
    except ValueError:
        raise DhmFileFormatError(f"line {line_number}: invalid {label}") from None
    if not math.isfinite(value):
        raise DhmFileFormatError(f"line {line_number}: non-finite {label}")
    return value


def parse_daily_flow(text: str) -> DailyFlowFile:
    lines = [(number, raw.strip()) for number, raw in enumerate(text.splitlines(), 1)]
    nonblank = [(number, raw) for number, raw in lines if raw]
    if not nonblank:
        raise DhmFileFormatError("daily-flow file is empty")
    first_number, title = nonblank[0]
    match = _DAILY_TITLE.fullmatch(title)
    if match is None:
        raise DhmFileFormatError(f"line {first_number}: invalid daily-flow title")

    years: list[int] = []
    values: list[DailyFlowValue] = []
    seen_days: set[date] = set()
    for line_number, raw in nonblank[1:]:
        if _YEAR.fullmatch(raw):
            year = int(raw)
            if year in years:
                raise DhmFileFormatError(f"line {line_number}: duplicate declared year")
            years.append(year)
            continue
        value_match = _DAILY_VALUE.fullmatch(raw)
        if value_match is None:
            raise DhmFileFormatError(f"line {line_number}: unknown daily-flow line")
        day = _day(value_match.group(1), "%d/%b/%Y", line_number=line_number)
        value = _number(
            value_match.group(2), line_number=line_number, label="discharge"
        )
        if value < 0:
            raise DhmFileFormatError(f"line {line_number}: negative discharge")
        if day in seen_days:
            raise DhmFileFormatError(f"line {line_number}: duplicate date")
        seen_days.add(day)
        values.append(DailyFlowValue(day=day, discharge_m3s=value))

    if not values:
        raise DhmFileFormatError("daily-flow file has no values")
    observed_years = {value.day.year for value in values}
    missing = set(years) - observed_years
    undeclared = observed_years - set(years)
    if missing or undeclared:
        raise DhmFileFormatError(
            f"declared years do not match data years: absent={sorted(missing)}, "
            f"undeclared={sorted(undeclared)}"
        )
    if [value.day for value in values] != sorted(seen_days):
        raise DhmFileFormatError("daily-flow dates are not ascending")
    return DailyFlowFile(
        station_code=match.group(1).strip(),
        declared_years=tuple(years),
        values=tuple(values),
    )


def parse_rating_tables(text: str) -> RatingTableFile:
    lines = [(number, raw.strip()) for number, raw in enumerate(text.splitlines(), 1)]
    nonblank = [(number, raw) for number, raw in lines if raw]
    if len(nonblank) < 5:
        raise DhmFileFormatError("rating-table file has an incomplete header")
    header = [raw for _, raw in nonblank[:4]]
    if not header[0].startswith("Station No:") or not header[0][11:].strip():
        raise DhmFileFormatError("rating-table station header is invalid")
    if header[1:] != [
        "Data Type: Rating Table",
        "Left Column= Stage in m",
        "Right Column= Discharge im m3/s",
    ]:
        raise DhmFileFormatError("rating-table column header is invalid")

    blocks: list[RatingTableBlock] = []
    index = 4
    while index < len(nonblank):
        line_number, raw = nonblank[index]
        if _SEPARATOR.fullmatch(raw):
            index += 1
            continue
        label_match = _RATING_TYPE.fullmatch(raw)
        if label_match is None:
            raise DhmFileFormatError(f"line {line_number}: expected rating block")
        if index + 3 >= len(nonblank):
            raise DhmFileFormatError(f"line {line_number}: truncated rating block")
        fields: list[str] = []
        for offset, prefix in enumerate(
            ("From Date =", "To Date =", "From Stage ="), 1
        ):
            field_line, field_raw = nonblank[index + offset]
            if not field_raw.startswith(prefix):
                raise DhmFileFormatError(f"line {field_line}: expected {prefix}")
            fields.append(field_raw.removeprefix(prefix).strip())
        start = _day(fields[0], "%d-%b-%Y", line_number=nonblank[index + 1][0])
        end = _day(fields[1], "%d-%b-%Y", line_number=nonblank[index + 2][0])
        if end < start:
            raise DhmFileFormatError(f"line {line_number}: rating window is reversed")
        from_stage = _number(
            fields[2], line_number=nonblank[index + 3][0], label="from stage"
        )
        index += 4
        points: list[RatingPoint] = []
        while index < len(nonblank):
            point_line, point_raw = nonblank[index]
            if _SEPARATOR.fullmatch(point_raw) or _RATING_TYPE.fullmatch(point_raw):
                break
            point_match = _RATING_POINT.fullmatch(point_raw)
            if point_match is None:
                raise DhmFileFormatError(
                    f"line {point_line}: unknown rating-block line"
                )
            points.append(
                RatingPoint(
                    stage_m=_number(
                        point_match.group(1), line_number=point_line, label="stage"
                    ),
                    discharge_m3s=_number(
                        point_match.group(2), line_number=point_line, label="discharge"
                    ),
                )
            )
            if points[-1].discharge_m3s < 0:
                raise DhmFileFormatError(
                    f"line {point_line}: negative rating discharge"
                )
            index += 1
        if len(points) < 2:
            raise DhmFileFormatError(
                f"line {line_number}: rating block has fewer than two points"
            )
        blocks.append(
            RatingTableBlock(
                rating_type_label=label_match.group(1),
                from_date=start,
                to_date=end,
                from_stage_m=from_stage,
                points=tuple(points),
            )
        )
    if not blocks:
        raise DhmFileFormatError("rating-table file has no blocks")
    return RatingTableFile(station_code=header[0][11:].strip(), blocks=tuple(blocks))
