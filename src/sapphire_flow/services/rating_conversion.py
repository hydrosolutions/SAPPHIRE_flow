# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import InterpolationMethod

if TYPE_CHECKING:
    from sapphire_flow.types.rating_curve import RatingCurve


class RatingConversionError(ValueError):
    """The rating curve or input level is invalid for level→discharge conversion."""


class RatingRange(Enum):
    """Which side of the tabulated stage domain a level fell on."""

    IN_RANGE = auto()
    BELOW = auto()  # level < lowest tabulated stage
    ABOVE = auto()  # level > highest tabulated stage


@dataclass(frozen=True, kw_only=True, slots=True)
class ConversionResult:
    discharge: float
    # Caller (ingest / reprocess) decides whether a non-IN_RANGE flag needs a QC flag.
    range_flag: RatingRange


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingConverter:
    """Pure level→discharge converter for a single rating curve.

    Build once via ``from_curve`` (validates + sorts the table); ``convert`` is a cheap,
    deterministic lookup with no I/O. Out-of-range levels are clamped to the nearest
    tabulated endpoint and reported via ``ConversionResult.range_flag`` — it never
    extrapolates and never decides QC policy (Plan 131 D2).
    """

    stages: tuple[float, ...]
    discharges: tuple[float, ...]
    interpolation: InterpolationMethod

    @classmethod
    def from_curve(cls, curve: RatingCurve) -> RatingConverter:
        if not curve.points:
            raise RatingConversionError("rating curve has no points")

        pairs: list[tuple[float, float]] = []
        for i, point in enumerate(curve.points):
            try:
                raw_h = point["water_level"]
                raw_q = point["discharge"]
            except (KeyError, TypeError) as exc:
                raise RatingConversionError(
                    f"rating point {i} is missing water_level/discharge: {point!r}"
                ) from exc
            h = _as_finite_float(raw_h, i, "water_level")
            q = _as_finite_float(raw_q, i, "discharge")
            pairs.append((h, q))

        # Do not assume DB/table order — sort by stage; the stage axis must then be
        # strictly increasing (a duplicate stage would map one level to two discharges).
        pairs.sort(key=lambda t: t[0])
        stages = tuple(h for h, _ in pairs)
        discharges = tuple(q for _, q in pairs)

        for i in range(1, len(stages)):
            if stages[i] <= stages[i - 1]:
                raise RatingConversionError(
                    f"duplicate or non-increasing stage {stages[i]} (rating point {i})"
                )
            # Discharge may TIE at adjacent stages (low-flow plateaus); only a DECREASE
            # is invalid for a monotone level→discharge relationship.
            if discharges[i] < discharges[i - 1]:
                raise RatingConversionError(
                    f"discharge decreases from {discharges[i - 1]} to {discharges[i]} "
                    f"at stage {stages[i]} (rating point {i})"
                )

        if curve.interpolation is InterpolationMethod.LOG_LINEAR and any(
            q <= 0.0 for q in discharges
        ):
            raise RatingConversionError(
                "log_linear interpolation requires every discharge > 0"
            )

        return cls(
            stages=stages, discharges=discharges, interpolation=curve.interpolation
        )

    def convert(self, level: float) -> ConversionResult:
        if not math.isfinite(level):
            raise RatingConversionError(f"level must be finite, got {level!r}")

        stages = self.stages
        discharges = self.discharges

        if level < stages[0]:
            return ConversionResult(
                discharge=discharges[0], range_flag=RatingRange.BELOW
            )
        if level > stages[-1]:
            return ConversionResult(
                discharge=discharges[-1], range_flag=RatingRange.ABOVE
            )

        i = bisect.bisect_left(stages, level)
        if stages[i] == level:  # exact tabulated hit
            return ConversionResult(
                discharge=discharges[i], range_flag=RatingRange.IN_RANGE
            )

        # stages[i-1] < level < stages[i]
        h0, h1 = stages[i - 1], stages[i]
        q0, q1 = discharges[i - 1], discharges[i]
        frac = (level - h0) / (h1 - h0)

        if self.interpolation is InterpolationMethod.LINEAR:
            discharge = q0 + frac * (q1 - q0)
        else:  # LOG_LINEAR: linear in log-discharge vs stage
            discharge = math.exp(math.log(q0) + frac * (math.log(q1) - math.log(q0)))

        return ConversionResult(discharge=discharge, range_flag=RatingRange.IN_RANGE)


def convert_level_to_discharge(level: float, curve: RatingCurve) -> ConversionResult:
    """One-shot convenience wrapper. Batch callers build a ``RatingConverter`` once."""
    return RatingConverter.from_curve(curve).convert(level)


def _as_finite_float(value: object, index: int, field: str) -> float:
    # bool is an int subclass — reject it so a stray True/False can't masquerade as 1/0.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RatingConversionError(
            f"rating point {index} has non-numeric {field}: {value!r}"
        )
    result = float(value)
    if not math.isfinite(result):
        raise RatingConversionError(
            f"rating point {index} has non-finite {field}: {value!r}"
        )
    return result
