# ruff: noqa: TC001 -- Pydantic resolves boundary annotations.
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal, TypeAlias

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from sapphire_flow.store.rated_threshold_codec import RatedThresholdContextPayload
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ThresholdSource
from sapphire_flow.types.probability import ProbabilityMethod
from sapphire_flow.types.rated_threshold_skill import RatedThresholdSkillContext
from sapphire_flow.types.threshold_skill import ThresholdSkillContext


class LegacyThresholdContextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    danger_level: str = Field(min_length=1)
    threshold_value: float
    threshold_source: ThresholdSource
    threshold_updated_at: AwareDatetime
    units: str = Field(min_length=1)
    probability_methods: tuple[ProbabilityMethod, ...]
    paired_count: int = Field(ge=0, strict=True)
    scored_count: int = Field(ge=0, strict=True)
    excluded_count: int = Field(ge=0, strict=True)
    paired_event_count: int = Field(ge=0, strict=True)
    scored_event_count: int = Field(ge=0, strict=True)
    scored_non_event_count: int = Field(ge=0, strict=True)
    coverage: Literal[
        "all_paired", "conditional_resolved_subset", "no_resolved_probabilities"
    ]
    schema_version: Literal[1]
    decision_probability: float = Field(ge=0, le=1)
    comparison: Literal["strict_gt"]
    reference: Literal["same_scored_subset_event_rate"]
    temporal_basis: Literal["legacy_step_aggregate"]
    threshold_basis: Literal["current_snapshot_historical_effective_dates_unknown"]


ThresholdContextPayload: TypeAlias = Annotated[
    LegacyThresholdContextPayload | RatedThresholdContextPayload,
    Field(discriminator="schema_version"),
]
_THRESHOLD_CONTEXT: TypeAdapter[
    LegacyThresholdContextPayload | RatedThresholdContextPayload
] = TypeAdapter(ThresholdContextPayload)


def parse_threshold_context_payload(
    value: object,
) -> LegacyThresholdContextPayload | RatedThresholdContextPayload:
    return _THRESHOLD_CONTEXT.validate_python(value)


def encode_threshold_context(
    context: ThresholdSkillContext | RatedThresholdSkillContext | None,
) -> dict[str, object] | None:
    if context is None:
        return None
    content = asdict(context)
    if isinstance(context, RatedThresholdSkillContext):
        content["event_support"] = context.event_support
    return parse_threshold_context_payload(content).model_dump(mode="json")


def decode_threshold_context(
    value: object,
) -> ThresholdSkillContext | RatedThresholdSkillContext | None:
    if value is None:
        return None
    payload = parse_threshold_context_payload(value)
    if isinstance(payload, RatedThresholdContextPayload):
        result = RatedThresholdSkillContext(
            **(
                payload.model_dump(exclude={"event_support"})
                | {"retrieved_at": ensure_utc(payload.retrieved_at)}
            )
        )
        if payload.event_support != result.event_support:
            raise ValueError("rated threshold event-support flag conflicts with counts")
        return result
    return ThresholdSkillContext(
        **(
            payload.model_dump()
            | {"threshold_updated_at": ensure_utc(payload.threshold_updated_at)}
        )
    )
