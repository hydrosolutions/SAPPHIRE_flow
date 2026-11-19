"""Plan 540 M3c: parse a rating-table CSV and derive discharge from stored levels."""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.rating_conversion import (
    RatingConversionError,
    RatingConverter,
    RatingRange,
)
from sapphire_flow.services.station_observation_load import (
    ObservationValueKind,
    convert_for_kind,
)
from sapphire_flow.types.enums import ObservationSource, WaterLevelGaugeType
from sapphire_flow.types.observation import RawObservation
from sapphire_flow.types.rating_curve import RatingCurve

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import InterpolationMethod
    from sapphire_flow.types.ids import RatingCurveId
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.station import StationConfig


class RatingCsvError(ConfigurationError):
    """The file cannot be read as a rating table (usage or validation error)."""


def _number(raw: str | None, line: int, column: str) -> float:
    try:
        value = float(raw or "")
    except ValueError as exc:
        raise RatingCsvError(
            f"line {line}: {column} value {raw!r} is not a number"
        ) from exc
    if not math.isfinite(value):
        raise RatingCsvError(f"line {line}: {column} value {raw!r} is not finite")
    return value


def parse_rating_csv(
    text: str,
    *,
    level_column: str,
    discharge_column: str,
    level_kind: ObservationValueKind,
    station: StationConfig,
    curve_id: RatingCurveId,
    version: int,
    valid_from: UtcDatetime,
    interpolation: InterpolationMethod,
    label: str | None,
    created_at: UtcDatetime,
    uploaded_by: UUID | None = None,
) -> RatingCurve:
    """Stage is converted to m a.s.l. like the observation loader, then validated."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise RatingCsvError("the file is empty")
    missing = [
        c for c in (level_column, discharge_column) if c not in reader.fieldnames
    ]
    if missing:
        raise RatingCsvError(
            f"column(s) {missing} not in the file; found {list(reader.fieldnames)}"
        )
    convert = convert_for_kind(station, level_kind)
    readings = [
        (
            line,
            _number(rec[level_column], line, level_column),
            _number(rec[discharge_column], line, discharge_column),
        )
        for line, rec in enumerate(reader, start=2)
    ]
    rows = sorted(((convert(r), line, r, q) for line, r, q in readings))
    points: list[dict[str, float]] = [
        {"water_level": stage, "discharge": q} for stage, _, _, q in rows
    ]
    if len(points) < 2:
        raise RatingCsvError(
            f"a rating table needs at least 2 points, got {len(points)}"
        )
    hint = (
        " (for a bridge sensor a larger reading is a lower level, so discharge "
        "must fall as the reading rises)"
        if station.water_level_gauge_type is WaterLevelGaugeType.BRIDGE_SENSOR
        and level_kind is ObservationValueKind.GAUGE_READING
        else ""
    )
    for (h0, _, _, q0), (h1, line, reading, q1) in zip(rows, rows[1:], strict=False):
        if h1 <= h0:
            raise RatingCsvError(
                f"line {line}: reading {reading:g} gives a duplicate or "
                f"non-increasing stage {h1:g} m a.s.l.{hint}"
            )
        if q1 < q0:
            raise RatingCsvError(
                f"line {line}: reading {reading:g} (stage {h1:g} m a.s.l.) has "
                f"discharge {q1:g}, which decreases from {q0:g} at a lower stage"
                f"{hint}"
            )
    curve = RatingCurve(
        id=curve_id,
        station_id=station.id,
        version=version,
        valid_from=valid_from,
        valid_to=None,
        points=points,
        interpolation=interpolation,
        uploaded_by=uploaded_by,
        created_at=created_at,
        rating_type_label=label,
    )
    try:
        RatingConverter.from_curve(curve)
    except RatingConversionError as exc:
        raise RatingCsvError(f"invalid rating table: {exc}") from exc
    return curve


def level_kind_choices() -> dict[str, ObservationValueKind]:
    return {
        "gauge-reading": ObservationValueKind.GAUGE_READING,
        "level-masl": ObservationValueKind.LEVEL_MASL,
    }


@dataclass(frozen=True, kw_only=True, slots=True)
class DerivedDischarge:
    rows: list[RawObservation]
    skipped_below: int
    skipped_above: int


def derive_discharge(levels: list[Observation], curve: RatingCurve) -> DerivedDischarge:
    """Out-of-range levels are skipped and counted, never stored clamped."""
    converter = RatingConverter.from_curve(curve)
    rows: list[RawObservation] = []
    below = above = 0
    for obs in levels:
        if obs.value is None:
            continue
        result = converter.convert(obs.value)
        match result.range_flag:
            case RatingRange.BELOW:
                below += 1
            case RatingRange.ABOVE:
                above += 1
            case _:
                rows.append(
                    RawObservation(
                        station_id=obs.station_id,
                        timestamp=obs.timestamp,
                        parameter="discharge",
                        value=result.discharge,
                        source=ObservationSource.RATING_CURVE_DERIVED,
                        rating_curve_id=curve.id,
                    )
                )
    return DerivedDischarge(rows=rows, skipped_below=below, skipped_above=above)


def stage_range(curve: RatingCurve) -> tuple[float, float]:
    stages = RatingConverter.from_curve(curve).stages
    return stages[0], stages[-1]


def point_count(curve: RatingCurve) -> int:
    return len(RatingConverter.from_curve(curve).stages)
