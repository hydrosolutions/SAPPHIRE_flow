"""Aggregate the restricted DHM delivery without emitting measurements."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import structlog

from sapphire_flow.adapters.dhm_files import parse_daily_flow, parse_rating_tables
from sapphire_flow.adapters.nepal_local_day import nepal_day_start
from sapphire_flow.logging import configure_cli_logging

log = structlog.get_logger(__name__)
_CODES = ("447", "450", "604.5", "647", "670", "684")
_EXPECTED_MISSING = {
    "447": 0,
    "450": 155,
    "604.5": 288,
    "647": 1173,
    "670": 433,
    "684": 32,
}
_EXPECTED_LONGEST_GAP = {
    "447": 0,
    "450": 122,
    "604.5": 106,
    "647": 471,
    "670": 245,
    "684": 32,
}
_EXPECTED_CURVE_LESS = {
    "447": 0,
    "450": 0,
    "604.5": 2976,
    "647": 0,
    "670": 1653,
    "684": 0,
}


@dataclass(frozen=True, kw_only=True, slots=True)
class StationAggregate:
    code: str
    observations: int
    curves: int
    missing_days: int
    longest_gap_days: int
    gap_runs: tuple[tuple[date, int], ...]
    curve_less_days: int
    outside_curve_range_days: int
    curve_windows: tuple[tuple[date, date, int], ...]
    integer_percent: float
    one_decimal_percent: float
    finer_percent: float
    first_day: date
    last_day: date


def measure_station(input_dir: Path, code: str) -> StationAggregate:
    daily_text = (input_dir / f"DFL_{code}.txt").read_text()
    daily = parse_daily_flow(daily_text)
    rating = parse_rating_tables((input_dir / f"RT_{code}.txt").read_text())
    if daily.station_code != code or rating.station_code != code:
        raise ValueError(f"station {code} delivery header mismatch")
    days = [item.day for item in daily.values]
    gaps = [
        (earlier + timedelta(days=1), (later - earlier).days - 1)
        for earlier, later in zip(days, days[1:], strict=False)
        if (later - earlier).days > 1
    ]
    intervals = [
        (
            nepal_day_start(block.from_date),
            nepal_day_start(block.to_date + timedelta(days=1)),
            block,
            ordinal,
        )
        for ordinal, block in enumerate(rating.blocks)
    ]
    curve_less = 0
    outside = 0
    for item in daily.values:
        at = nepal_day_start(item.day)
        matching = [
            interval for interval in intervals if interval[0] <= at < interval[1]
        ]
        if not matching:
            curve_less += 1
            continue
        _, _, block, _ = max(matching, key=lambda interval: (interval[0], interval[3]))
        discharges = [point.discharge_m3s for point in block.points]
        if item.discharge_m3s < min(discharges) or item.discharge_m3s > max(discharges):
            outside += 1
    precision = [
        len(match.group(1).strip().partition(".")[2])
        for line in daily_text.splitlines()
        if (match := re.fullmatch(r"\s*\d{2}/[A-Za-z]{3}/\d{4},([^,]+)\s*", line))
    ]
    if len(precision) != len(days):
        raise ValueError(f"station {code} precision audit did not match row count")
    count = len(precision)
    return StationAggregate(
        code=code,
        observations=len(days),
        curves=len(rating.blocks),
        missing_days=sum(length for _, length in gaps),
        longest_gap_days=max((length for _, length in gaps), default=0),
        gap_runs=tuple(gaps),
        curve_less_days=curve_less,
        outside_curve_range_days=outside,
        curve_windows=tuple(
            (block.from_date, block.to_date, len(block.points))
            for block in rating.blocks
        ),
        integer_percent=100 * precision.count(0) / count,
        one_decimal_percent=100 * precision.count(1) / count,
        finer_percent=100 * sum(digits > 1 for digits in precision) / count,
        first_day=days[0],
        last_day=days[-1],
    )


def check_aggregates(aggregates: list[StationAggregate]) -> list[str]:
    failures: list[str] = []
    if sum(item.observations for item in aggregates) != 99246:
        failures.append("daily observation count")
    if sum(item.curves for item in aggregates) != 112:
        failures.append("rating curve count")
    if sum(item.curve_less_days for item in aggregates) != 4629:
        failures.append("curve-less count")
    if sum(item.outside_curve_range_days for item in aggregates) != 1:
        failures.append("out-of-range count")
    for item in aggregates:
        if item.missing_days != _EXPECTED_MISSING[item.code]:
            failures.append(f"station {item.code} missing-day count")
        if item.longest_gap_days != _EXPECTED_LONGEST_GAP[item.code]:
            failures.append(f"station {item.code} longest gap")
        if item.curve_less_days != _EXPECTED_CURVE_LESS[item.code]:
            failures.append(f"station {item.code} curve-less count")
        expected_outside = 1 if item.code == "670" else 0
        if item.outside_curve_range_days != expected_outside:
            failures.append(f"station {item.code} out-of-range count")
    return failures


def main() -> int:
    configure_cli_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    aggregates = [measure_station(args.input_dir, code) for code in _CODES]
    for item in aggregates:
        log.info(
            "dhm_delivery.station_aggregate",
            code=item.code,
            observations=item.observations,
            curves=item.curves,
            missing_days=item.missing_days,
            longest_gap_days=item.longest_gap_days,
            gap_runs=[
                {"start": start.isoformat(), "days": length}
                for start, length in item.gap_runs
            ],
            curve_less_days=item.curve_less_days,
            outside_curve_range_days=item.outside_curve_range_days,
            curve_windows=[
                {"start": start.isoformat(), "end": end.isoformat(), "points": points}
                for start, end, points in item.curve_windows
            ],
            precision_percentages={
                "integer": round(item.integer_percent, 1),
                "one_decimal": round(item.one_decimal_percent, 1),
                "finer": round(item.finer_percent, 1),
            },
            first_day=item.first_day.isoformat(),
            last_day=item.last_day.isoformat(),
        )
    if args.check:
        failures = check_aggregates(aggregates)
        if failures:
            log.error("dhm_delivery.aggregate_mismatch", checks=failures)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
