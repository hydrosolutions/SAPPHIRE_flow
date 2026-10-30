from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum

_DECIMAL_LITERAL = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)


class DischargeSourceUnit(Enum):
    LITRES_PER_SECOND = "l/s"
    CUBIC_METRES_PER_SECOND = "m3/s"


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceDischargeValue:
    value: float

    @classmethod
    def from_literal(cls, literal: str) -> SourceDischargeValue:
        if _DECIMAL_LITERAL.fullmatch(literal) is None:
            raise ValueError("invalid discharge literal: expected decimal text")
        value = float(literal)
        if not math.isfinite(value):
            raise ValueError("invalid discharge literal: non-finite value")
        if value == 0 and any(
            digit in "123456789" for digit in literal.lower().partition("e")[0]
        ):
            raise ValueError("invalid discharge literal: underflow to zero")
        return cls(value=value)

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not math.isfinite(self.value):
            raise ValueError("source discharge must be finite numeric data")
        object.__setattr__(self, "value", 0.0 if self.value == 0 else float(self.value))


@dataclass(frozen=True, kw_only=True, slots=True)
class CanonicalDischargeValue:
    value: float

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not math.isfinite(self.value):
            raise ValueError("canonical discharge must be finite numeric data")
        object.__setattr__(self, "value", 0.0 if self.value == 0 else float(self.value))


class DischargeUnderflowError(ValueError):
    pass


def normalize_discharge(
    value: SourceDischargeValue, unit: DischargeSourceUnit
) -> CanonicalDischargeValue:
    match unit:
        case DischargeSourceUnit.LITRES_PER_SECOND:
            converted = value.value / 1000.0
            if value.value != 0 and converted == 0:
                raise DischargeUnderflowError("discharge conversion underflow to zero")
            return CanonicalDischargeValue(value=converted)
        case DischargeSourceUnit.CUBIC_METRES_PER_SECOND:
            return CanonicalDischargeValue(value=value.value)
        case _:
            raise ValueError(f"unsupported discharge source unit: {unit!r}")
