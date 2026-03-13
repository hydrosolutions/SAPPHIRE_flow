from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NamedTuple

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import FlowRegime, ForcingType, SkillSource
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationId


class SkillScore(NamedTuple):
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
    is_stale: bool
    created_at: UtcDatetime


class SkillDiagram(NamedTuple):
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
    created_at: UtcDatetime


class FlowRegimeConfig(NamedTuple):
    id: UUID
    station_id: StationId
    q50: float
    q90: float
    computed_at: UtcDatetime
    observation_count: int
    version: int
    created_at: UtcDatetime
