from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import timedelta

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class TrainingUnit:
    model_id: ModelId
    station_id: StationId | None
    group_id: StationGroupId | None
    station_ids: frozenset[StationId]
    training_period_start: UtcDatetime
    training_period_end: UtcDatetime
    time_step: timedelta

    def __post_init__(self) -> None:
        if (self.station_id is None) == (self.group_id is None):
            raise ValueError("Exactly one of station_id or group_id must be set")
        expected = frozenset({self.station_id}) if self.station_id is not None else None
        if expected is not None and self.station_ids != expected:
            raise ValueError("station-scoped: station_ids must equal {station_id}")
        if self.group_id is not None and len(self.station_ids) == 0:
            raise ValueError("group-scoped unit must have at least one station_id")


@dataclass(frozen=True, kw_only=True, slots=True)
class TrainingScope:
    units: tuple[TrainingUnit, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class HindcastStepResult:
    issue_time: UtcDatetime
    success: bool
    error: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class TrainingResult:
    training_unit: TrainingUnit
    artifact_id: ArtifactId | None
    hindcast_steps: list[HindcastStepResult]
    skill_computed: bool
    error: str | None = None
