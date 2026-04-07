from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import random

    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import ArtifactScope
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.model import (
        GroupModelInputs,
        GroupTrainingData,
        ModelArtifact,
        ModelDataRequirements,
        ModelParams,
        StationModelInputs,
        StationTrainingData,
    )


@runtime_checkable
class StationForecastModel(Protocol):
    artifact_scope: ArtifactScope
    data_requirements: ModelDataRequirements

    def train(
        self, data: StationTrainingData, params: ModelParams, rng: random.Random
    ) -> ModelArtifact:
        raise NotImplementedError

    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]:
        raise NotImplementedError

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        raise NotImplementedError

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        raise NotImplementedError


@runtime_checkable
class GroupForecastModel(Protocol):
    artifact_scope: ArtifactScope
    data_requirements: ModelDataRequirements

    def train(
        self, data: GroupTrainingData, params: ModelParams, rng: random.Random
    ) -> ModelArtifact:
        raise NotImplementedError

    def predict_batch(
        self,
        artifact: ModelArtifact,
        inputs: GroupModelInputs,
        rng: random.Random,
    ) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]:
        raise NotImplementedError

    def serialize_artifact(self, artifact: ModelArtifact) -> bytes:
        raise NotImplementedError

    def deserialize_artifact(self, raw: bytes) -> ModelArtifact:
        raise NotImplementedError


ForecastModel = StationForecastModel | GroupForecastModel
