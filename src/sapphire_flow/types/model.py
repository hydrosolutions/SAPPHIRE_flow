from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import polars as pl  # noqa: TC002

if TYPE_CHECKING:
    from datetime import timedelta
    from uuid import UUID

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

PROVENANCE_SUFFIX = "_provenance"


def forcing_provenance_columns(forcing: pl.DataFrame) -> list[str]:
    return [c for c in forcing.columns if c.endswith(PROVENANCE_SUFFIX)]


def parameter_columns(forcing: pl.DataFrame) -> list[str]:
    return [
        c
        for c in forcing.columns
        if c != "timestamp" and not c.endswith(PROVENANCE_SUFFIX)
    ]


def validate_forcing_provenance(forcing: pl.DataFrame) -> None:
    param_cols = parameter_columns(forcing)
    expected = {f"{p}{PROVENANCE_SUFFIX}" for p in param_cols}
    actual = set(forcing_provenance_columns(forcing))
    if missing := expected - actual:
        raise ValueError(f"Missing provenance columns: {missing}")
    if extra := actual - expected:
        raise ValueError(f"Orphaned provenance columns: {extra}")


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelInputs:
    station_id: StationId
    forcing: pl.DataFrame | xr.Dataset
    observations: pl.DataFrame
    static_attributes: pl.DataFrame | None
    issue_time: UtcDatetime
    forecast_horizon_steps: int
    time_step: timedelta
    warm_up_steps: int | None


@dataclass(frozen=True, kw_only=True, slots=True)
class TrainingData:
    forcing: pl.DataFrame
    observations: pl.DataFrame
    targets: pl.DataFrame
    static_attributes: pl.DataFrame | None
    time_step: timedelta
    val_start: UtcDatetime | None


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupTrainingData:
    group_id: StationGroupId
    station_data: dict[StationId, TrainingData]
    time_step: timedelta
    val_start: UtcDatetime | None


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRecord:
    id: ModelId
    display_name: str
    artifact_scope: ArtifactScope
    description: str
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRegistryEntry:
    id: ModelId
    display_name: str
    description: str
    artifact_scope: ArtifactScope
    required_features: frozenset[str]
    required_static_attributes: frozenset[str]
    spatial_input_type: SpatialRepresentation
    supported_time_steps: frozenset[timedelta]
    registered_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelArtifactRecord:
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
