from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import random
    from datetime import timedelta

    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import ArtifactScope, SpatialRepresentation
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.model import (
        GroupTrainingData,
        ModelArtifact,
        ModelInputs,
        ModelParams,
        TrainingData,
    )


@runtime_checkable
class StationForecastModel(Protocol):
    artifact_scope: ArtifactScope
    required_features: frozenset[str]
    required_static_attributes: frozenset[str]
    spatial_input_type: SpatialRepresentation
    supported_time_steps: frozenset[timedelta]

    def train(
        self, data: TrainingData, params: ModelParams, rng: random.Random
    ) -> ModelArtifact: ...
    def predict(
        self,
        artifact: ModelArtifact,
        inputs: ModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[ForecastEnsemble, bytes | None]: ...
    def serialize_artifact(self, artifact: ModelArtifact) -> bytes: ...
    def deserialize_artifact(self, raw: bytes) -> ModelArtifact: ...


@runtime_checkable
class GroupForecastModel(Protocol):
    artifact_scope: ArtifactScope
    required_features: frozenset[str]
    required_static_attributes: frozenset[str]
    spatial_input_type: SpatialRepresentation
    supported_time_steps: frozenset[timedelta]

    def train(
        self, data: GroupTrainingData, params: ModelParams, rng: random.Random
    ) -> ModelArtifact: ...
    def predict_batch(
        self,
        artifact: ModelArtifact,
        inputs: dict[StationId, ModelInputs],
        rng: random.Random,
    ) -> dict[StationId, tuple[ForecastEnsemble, bytes | None]]: ...
    def serialize_artifact(self, artifact: ModelArtifact) -> bytes: ...
    def deserialize_artifact(self, raw: bytes) -> ModelArtifact: ...


ForecastModel = StationForecastModel | GroupForecastModel
