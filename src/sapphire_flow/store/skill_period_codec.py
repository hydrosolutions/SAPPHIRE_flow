import hashlib
import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sapphire_flow.store.forecast_period_codec import (
    ForecastPeriodPayload,
    parse_forecast_period,
)
from sapphire_flow.store.hindcast_period_codec import (
    HistoricalSourcePayload,
    parse_source_periods,
    source_periods_content,
)
from sapphire_flow.types.hindcast_inputs import HistoricalAvailabilityPolicy
from sapphire_flow.types.skill import SkillEvaluationContext

SKILL_PERIOD_FIELDS = (
    "period_definition",
    "reference_period_definition",
    "reference_displacement_seconds",
    "input_availability_policy",
    "source_periods",
    "source_periods_sha256",
)


class SkillPeriodPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_definition: ForecastPeriodPayload
    reference_period_definition: ForecastPeriodPayload
    reference_displacement_seconds: int = Field(strict=True)
    input_availability_policy: Literal["recorded_only", "retrospective_closure"]
    source_periods: tuple[HistoricalSourcePayload, ...] = Field(min_length=1)
    source_periods_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_domain(
        cls, context: SkillEvaluationContext | None
    ) -> "SkillPeriodPayload | None":
        return (
            cls.model_validate(evaluation_context_content(context))
            if context is not None
            else None
        )


def parse_evaluation_context(
    row: Mapping[str, object],
) -> SkillEvaluationContext | None:
    value = {name: row.get(name) for name in SKILL_PERIOD_FIELDS}
    if all(item is None for item in value.values()):
        return None
    payload = SkillPeriodPayload.model_validate(value)
    forecast = parse_forecast_period(payload.period_definition.model_dump())
    reference = parse_forecast_period(payload.reference_period_definition.model_dump())
    assert forecast is not None and reference is not None
    sources = parse_source_periods(
        [item.model_dump() for item in payload.source_periods]
    )
    if _source_digest(source_periods_content(sources)) != payload.source_periods_sha256:
        raise ValueError("evaluation native source digest mismatch")
    return SkillEvaluationContext(
        forecast_period=forecast,
        reference_period=reference,
        reference_displacement=timedelta(
            seconds=payload.reference_displacement_seconds
        ),
        input_availability_policy=HistoricalAvailabilityPolicy(
            payload.input_availability_policy
        ),
        source_periods=sources,
    )


def _source_digest(content: list[dict[str, object]]) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evaluation_context_content(
    context: SkillEvaluationContext | None,
) -> dict[str, object]:
    if context is None:
        return {str(name): None for name in SKILL_PERIOD_FIELDS}
    sources = source_periods_content(context.source_periods)
    return {
        "period_definition": context.forecast_period.content,
        "reference_period_definition": context.reference_period.content,
        "reference_displacement_seconds": int(
            context.reference_displacement.total_seconds()
        ),
        "input_availability_policy": context.input_availability_policy.value,
        "source_periods": sources,
        "source_periods_sha256": _source_digest(sources),
    }


def evaluation_contexts_content(
    contexts: tuple[SkillEvaluationContext, ...],
) -> list[dict[str, object]] | None:
    if not contexts:
        return None
    unique = {
        json.dumps(evaluation_context_content(item), sort_keys=True): item
        for item in contexts
    }
    return [evaluation_context_content(unique[key]) for key in sorted(unique)]
