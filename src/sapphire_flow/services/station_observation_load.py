"""Plan 540 M3a: parse a CSV of readings, convert to stored units, build raw rows."""

from __future__ import annotations

import csv
import io
import math
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from enum import Enum
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import ObservationSource, WaterLevelGaugeType
from sapphire_flow.types.observation import RawObservation

if TYPE_CHECKING:
    from collections.abc import Callable
    from zoneinfo import ZoneInfo

    from sapphire_flow.types.station import StationConfig


class ObservationValueKind(Enum):
    GAUGE_READING = "gauge_reading"
    LEVEL_MASL = "level_masl"
    DISCHARGE_M3S = "discharge_m3s"


class ObservationCsvError(ConfigurationError):
    """The file cannot be read as observations (usage or validation error)."""


class DatumRequiredError(ConfigurationError):
    """A gauge reading cannot be converted without the station's datum and type."""


def _zone(utc_offset: timedelta | None, zone: ZoneInfo | None) -> tzinfo | None:
    if utc_offset is not None and zone is not None:
        raise ObservationCsvError("give a UTC offset or a zone, not both")
    return timezone(utc_offset) if utc_offset is not None else zone


def _parse_time(raw: str, tz: tzinfo | None, line: int) -> UtcDatetime:
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ObservationCsvError(
            f"line {line}: {raw!r} is not an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        if tz is None:
            raise ObservationCsvError(
                f"line {line}: timestamp {raw!r} has no time zone; "
                "give a UTC offset or a zone"
            )
        parsed = parsed.replace(tzinfo=tz)
    return ensure_utc(parsed.astimezone(UTC))


def _parse_value(raw: str, line: int) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ObservationCsvError(f"line {line}: {raw!r} is not a number") from exc
    if not math.isfinite(value):
        raise ObservationCsvError(f"line {line}: value {raw!r} is not finite")
    return value


def parse_observation_csv(
    text: str,
    *,
    time_column: str,
    value_column: str,
    utc_offset: timedelta | None,
    timezone: ZoneInfo | None,
    now: UtcDatetime,
) -> list[tuple[UtcDatetime, float]]:
    tz = _zone(utc_offset, timezone)
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ObservationCsvError("the file is empty")
    missing = [c for c in (time_column, value_column) if c not in reader.fieldnames]
    if missing:
        raise ObservationCsvError(
            f"column(s) {missing} not in the file; found {list(reader.fieldnames)}"
        )
    rows: list[tuple[UtcDatetime, float]] = []
    seen: set[UtcDatetime] = set()
    for line, record in enumerate(reader, start=2):
        ts = _parse_time(record[time_column] or "", tz, line)
        if ts > now:
            raise ObservationCsvError(
                f"line {line}: timestamp {ts.isoformat()} is in the future"
            )
        if ts in seen:
            raise ObservationCsvError(
                f"line {line}: duplicate timestamp {ts.isoformat()}"
            )
        seen.add(ts)
        rows.append((ts, _parse_value(record[value_column] or "", line)))
    if not rows:
        raise ObservationCsvError("the file has no data rows")
    return rows


def convert_for_kind(
    station: StationConfig, kind: ObservationValueKind
) -> Callable[[float], float]:
    match kind:
        case ObservationValueKind.DISCHARGE_M3S | ObservationValueKind.LEVEL_MASL:
            return lambda value: value
        case ObservationValueKind.GAUGE_READING:
            datum = station.water_level_datum_masl
            gauge = station.water_level_gauge_type
            if datum is None or gauge is None:
                raise DatumRequiredError(
                    f"station {station.code!r} has no gauge type or datum; set them "
                    "first (create_station), or load absolute levels with "
                    "--value-kind level-masl"
                )
            if gauge is WaterLevelGaugeType.STAFF_OR_PRESSURE:
                return lambda value: datum + value
            sensor = station.water_level_sensor_masl
            if sensor is None:
                raise DatumRequiredError(
                    f"station {station.code!r} is a bridge sensor without a sensor "
                    "elevation"
                )
            return lambda value: sensor - value


def to_raw_observations(
    rows: list[tuple[UtcDatetime, float]],
    station: StationConfig,
    parameter: str,
    kind: ObservationValueKind,
    source: ObservationSource = ObservationSource.MEASURED,
) -> list[RawObservation]:
    convert = convert_for_kind(station, kind)
    return [
        RawObservation(
            station_id=station.id,
            timestamp=ts,
            parameter=parameter,
            value=convert(value),
            source=source,
        )
        for ts, value in rows
    ]


def days_with_value(
    timestamps: list[UtcDatetime], zone: tzinfo, *, end: date, lookback_days: int
) -> int:
    start = end - timedelta(days=lookback_days - 1)
    local = {ts.astimezone(zone).date() for ts in timestamps}
    return sum(1 for day in local if start <= day <= end)
