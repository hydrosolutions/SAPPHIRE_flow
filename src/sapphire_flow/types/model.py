from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from datetime import timedelta
    from uuid import UUID

    import polars as pl
    import xarray as xr

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import (
        ArtifactScope,
        ModelArtifactStatus,
        SpatialRepresentation,
    )
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId

ModelParams = dict[str, Any]
ModelArtifact = Any


class ModelInputs(NamedTuple):
    station_id: StationId
    forcing: pl.DataFrame | xr.Dataset
    observations: pl.DataFrame
    static_attributes: pl.DataFrame | None
    issue_time: UtcDatetime
    forecast_horizon_steps: int
    time_step: timedelta
    warm_up_steps: int | None


class TrainingData(NamedTuple):
    forcing: pl.DataFrame
    observations: pl.DataFrame
    targets: pl.DataFrame
    static_attributes: pl.DataFrame | None
    time_step: timedelta
    val_start: UtcDatetime | None


class GroupTrainingData(NamedTuple):
    group_id: StationGroupId
    station_data: dict[StationId, TrainingData]
    time_step: timedelta
    val_start: UtcDatetime | None


class ModelRegistryEntry(NamedTuple):
    id: ModelId
    description: str
    artifact_scope: ArtifactScope
    required_features: frozenset[str]
    required_static_attributes: frozenset[str]
    spatial_input_type: SpatialRepresentation
    supported_time_steps: frozenset[timedelta]
    registered_at: UtcDatetime


class ModelArtifactRecord(NamedTuple):
    id: ArtifactId
    model_id: ModelId
    station_id: StationId | None
    group_id: StationGroupId | None
    status: ModelArtifactStatus
    artifact_path: str
    training_period_start: UtcDatetime
    training_period_end: UtcDatetime
    trained_at: UtcDatetime
    promoted_at: UtcDatetime | None
    promoted_by: UUID | None
    superseded_at: UtcDatetime | None
    created_at: UtcDatetime
