from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import (
        FlowRegime,
        ForcingType,
        SkillFreshness,
        SkillSource,
    )
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class SkillScore:
    id: UUID
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId
    skill_source: SkillSource
    forcing_type: ForcingType | None
    computation_version: int
    computed_at: UtcDatetime
    lead_time_hours: int
    season: str | None
    flow_regime: FlowRegime | None
    flow_regime_config_id: UUID | None
    metric: str
    score: float
    sample_size: int
    freshness: SkillFreshness
    eval_period_start: UtcDatetime
    eval_period_end: UtcDatetime
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class SkillDiagram:
    id: UUID
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId
    skill_source: SkillSource
    computation_version: int
    lead_time_hours: int
    season: str | None
    flow_regime: FlowRegime | None
    flow_regime_config_id: UUID | None
    diagram_type: Literal["reliability", "roc", "rank_histogram"]
    threshold_level: str | None
    data: dict  # type: ignore[type-arg]
    eval_period_start: UtcDatetime
    eval_period_end: UtcDatetime
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class FlowRegimeConfig:
    id: UUID
    station_id: StationId
    parameter: str
    p50: float
    p90: float
    computed_at: UtcDatetime
    observation_count: int
    version: int
    created_at: UtcDatetime
