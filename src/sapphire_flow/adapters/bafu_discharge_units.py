from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from sapphire_flow.types.observation_units import (
    SourceDischargeValue,
)
from sapphire_flow.types.observation_units import (
    normalize_discharge as normalize_discharge,
)


class _DischargeLiteral(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    value: float

    @field_validator("value", mode="before")
    @classmethod
    def parse_literal(cls, raw: object) -> float:
        if not isinstance(raw, str):
            raise ValueError("invalid discharge literal: expected decimal text")
        return SourceDischargeValue.from_literal(raw).value


def parse_discharge_literal(literal: str) -> SourceDischargeValue:
    parsed = _DischargeLiteral.model_validate({"value": literal})
    return SourceDischargeValue(value=parsed.value)
