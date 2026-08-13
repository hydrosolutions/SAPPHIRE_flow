"""M-A4 (Plan 171) task 1a — the CDS request spec: `AcquisitionWindow` (D2)
and the window -> payload builder.

The payload shape below is not guessed — it is the OPERATOR-CAPTURED literal
from the live CDS download form (plan `## Observed CDS payload`), reproduced
here field-for-field. Every CDS-shaped statement elsewhere in this plan is
superseded by that observation; this module is where it becomes code.

Credentials never appear here: this module only ever builds a JSON-safe
request payload from an `AcquisitionWindow` + `Era5RequestSpec`. Neither type
carries a credential field, and `cdsapi.Client()` (constructed elsewhere, in
`era5_acquire.py`) reads credentials from the environment/`~/.cdsapirc`
itself — they are never threaded through this module's objects.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import cast

from scripts.dhm_precip.era5_errors import NonExpressibleWindowError

DATASET_ID = "reanalysis-era5-land"
DEFAULT_VARIABLE = "total_precipitation"

# D2: CDS `area` is north/west/south/east. Study box: 26-31 N, 80-89 E.
StudyArea = tuple[int, int, int, int]
STUDY_AREA: StudyArea = (31, 80, 26, 89)

# D4: the six product years plus the two edge-context windows.
STUDY_YEARS: tuple[int, ...] = (2020, 2021, 2022, 2023, 2024, 2025)

_HOURS_PER_DAY = 24


@dataclass(frozen=True, kw_only=True, slots=True)
class AcquisitionWindow:
    """D2 — one window, one CDS payload, one raw artifact.

    Restricted by construction to a whole calendar year, a whole calendar
    month, a single day, or a single hour — the only shapes CDS's
    independent year/month/day/time lists can express without a Cartesian
    spill (D2's "no over-selection" rule). `month=None` means "whole year";
    `day=None` (with `month` set) means "whole month"; `hour=None` (with
    `day` set) means "whole day".
    """

    year: int
    month: int | None = None
    day: int | None = None
    hour: int | None = None

    def __post_init__(self) -> None:
        if not (1 <= self.year <= 9999):
            raise ValueError(f"year out of range: {self.year}")
        if self.day is not None and self.month is None:
            raise ValueError("day requires month to be set")
        if self.hour is not None and self.day is None:
            raise ValueError("hour requires day to be set")
        if self.month is not None and not (1 <= self.month <= 12):
            raise ValueError(f"month out of range: {self.month}")
        if self.hour is not None and not (0 <= self.hour <= 23):
            raise ValueError(f"hour out of range: {self.hour}")
        if self.day is not None:
            assert self.month is not None  # narrowed by the check above
            last_day = calendar.monthrange(self.year, self.month)[1]
            if not (1 <= self.day <= last_day):
                raise ValueError(
                    f"day {self.day} invalid for {self.year:04d}-{self.month:02d}"
                )

    @property
    def window_id(self) -> str:
        if self.month is None:
            return f"{self.year:04d}"
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        if self.hour is None:
            return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}T{self.hour:02d}"

    def valid_time_stamps(self) -> frozenset[tuple[int, int, int, int]]:
        """The exact set of (year, month, day, hour) stamps this window
        denotes — always calendar-valid by construction (no spill)."""
        months = [self.month] if self.month is not None else list(range(1, 13))
        stamps: set[tuple[int, int, int, int]] = set()
        for month in months:
            if self.day is not None:
                days = [self.day]
            else:
                days = list(range(1, calendar.monthrange(self.year, month)[1] + 1))
            hours = (
                [self.hour] if self.hour is not None else list(range(_HOURS_PER_DAY))
            )
            stamps.update(
                (self.year, month, day, hour) for day in days for hour in hours
            )
        return frozenset(stamps)

    @staticmethod
    def from_date_range(start: date, end: date) -> AcquisitionWindow:
        """D2 — reduce an explicit date span to a canonical window, or raise
        `NonExpressibleWindowError` when the span is not a clean Cartesian
        unit (e.g. "30 Sep through 1 Nov")."""
        if start > end:
            raise NonExpressibleWindowError(f"start {start} is after end {end}")
        if start.year != end.year:
            raise NonExpressibleWindowError(
                f"span {start}..{end} crosses a year boundary; not expressible "
                "as a single CDS payload"
            )
        year = start.year
        if start == date(year, 1, 1) and end == date(year, 12, 31):
            return AcquisitionWindow(year=year)
        if start.day == 1 and start.month == end.month:
            last_day = calendar.monthrange(year, start.month)[1]
            if end.day == last_day:
                return AcquisitionWindow(year=year, month=start.month)
        if start == end:
            return AcquisitionWindow(year=year, month=start.month, day=start.day)
        raise NonExpressibleWindowError(
            f"span {start}..{end} is not a whole year, whole month, or single "
            "day — no combination of CDS's independent year/month/day/time "
            "lists selects it exactly"
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class Era5RequestSpec:
    """D2/D5 — everything about a request except which window it targets.
    Retry knobs live here (owner: the 2a acquisition driver) so a single
    object is both the payload's parameter source and the retry contract."""

    dataset: str = DATASET_ID
    variable: str = DEFAULT_VARIABLE
    area: StudyArea = STUDY_AREA
    data_format: str = "netcdf"
    download_format: str = "unarchived"
    max_retry_attempts: int = 5
    retry_backoff_base_seconds: float = 2.0

    def __post_init__(self) -> None:
        north, west, south, east = self.area
        if not (south < north):
            raise ValueError(f"area north/south out of order: {self.area}")
        if not (west < east):
            raise ValueError(f"area west/east out of order: {self.area}")
        if self.max_retry_attempts < 1:
            raise ValueError("max_retry_attempts must be >= 1")
        if self.retry_backoff_base_seconds <= 0:
            raise ValueError("retry_backoff_base_seconds must be > 0")


DEFAULT_REQUEST_SPEC = Era5RequestSpec()


def build_request_payload(
    window: AcquisitionWindow, spec: Era5RequestSpec = DEFAULT_REQUEST_SPEC
) -> dict[str, object]:
    """D2/D4 — the window -> CDS payload builder. Field set, key order and
    `area` ordering match the operator-captured literal exactly. A whole-year
    window's `day` list is the full 31-entry list (the accepted Cartesian
    spill onto non-existent dates, D2's refinement); a whole-month window's
    `day` list is exactly that month's days (no spill)."""
    year_field = f"{window.year:04d}"
    if window.month is None:
        month_field: str | list[str] = [f"{m:02d}" for m in range(1, 13)]
        day_field: list[str] = [f"{d:02d}" for d in range(1, 32)]
    else:
        month_field = f"{window.month:02d}"
        if window.day is None:
            last_day = calendar.monthrange(window.year, window.month)[1]
            day_field = [f"{d:02d}" for d in range(1, last_day + 1)]
        else:
            day_field = [f"{window.day:02d}"]

    if window.hour is not None:
        time_field = [f"{window.hour:02d}:00"]
    else:
        time_field = [f"{h:02d}:00" for h in range(_HOURS_PER_DAY)]

    return {
        "variable": [spec.variable],
        "year": year_field,
        "month": month_field,
        "day": day_field,
        "time": time_field,
        "data_format": spec.data_format,
        "download_format": spec.download_format,
        "area": list(spec.area),
    }


def payload_implied_valid_time_stamps(
    payload: dict[str, object],
) -> frozenset[tuple[int, int, int, int]]:
    """The Cartesian product of `payload`'s year/month/day/time lists,
    filtered to calendar-valid stamps only (D2's refinement: non-existent
    dates from a whole-year spill are neither spill nor omission)."""
    years = _as_list(payload["year"])
    months = _as_list(payload["month"])
    days = _as_list(payload["day"])
    times = _as_list(payload["time"])
    stamps: set[tuple[int, int, int, int]] = set()
    for year in years:
        for month in months:
            if not (1 <= int(month) <= 12):
                continue
            last_day = calendar.monthrange(int(year), int(month))[1]
            for day in days:
                if not (1 <= int(day) <= last_day):
                    continue
                for time in times:
                    hour = int(str(time).split(":")[0])
                    stamps.add((int(year), int(month), int(day), hour))
    return frozenset(stamps)


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in cast("list[object]", value)]
    return [str(value)]


def parse_window_arg(text: str) -> AcquisitionWindow:
    """CLI `--window` grammar (D2): `YYYY`, `YYYY-MM`, `YYYY-MM-DD`, or
    `YYYY-MM-DDTHH` — the same four canonical shapes `AcquisitionWindow`
    represents, so 2b's October sample and a full year share one code path.
    """
    parts = text.split("T")
    if len(parts) == 2:
        date_part, hour_part = parts
    else:
        date_part, hour_part = parts[0], None
    date_fields = date_part.split("-")
    try:
        if len(date_fields) == 1:
            return AcquisitionWindow(year=int(date_fields[0]))
        if len(date_fields) == 2:
            return AcquisitionWindow(
                year=int(date_fields[0]), month=int(date_fields[1])
            )
        if len(date_fields) == 3:
            year, month, day = (int(f) for f in date_fields)
            hour = int(hour_part) if hour_part is not None else None
            return AcquisitionWindow(year=year, month=month, day=day, hour=hour)
    except ValueError as exc:
        raise NonExpressibleWindowError(
            f"unparsable --window value {text!r}: {exc}"
        ) from exc
    raise NonExpressibleWindowError(f"unparsable --window value {text!r}")


ALL_ACQUISITION_WINDOWS: tuple[AcquisitionWindow, ...] = (
    *(AcquisitionWindow(year=year) for year in STUDY_YEARS),
    AcquisitionWindow(year=STUDY_YEARS[0] - 1, month=12, day=31),
    AcquisitionWindow(year=STUDY_YEARS[-1] + 1, month=1, day=1, hour=0),
)
