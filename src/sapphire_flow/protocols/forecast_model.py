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


# --- Plan 399 T1 — OPTIONAL warm-start capability ------------------------------
#
# Mirrors FI's `RetrainableModel`: a model MAY support fine-tuning from an
# existing artifact. Deliberately SEPARATE, optional protocols — the Swiss
# statistical models implement none of this and must keep satisfying
# `StationForecastModel` / `GroupForecastModel` untouched.
#
# ⛔ DO NOT use `isinstance(model, RetrainableStationModel)` on a wrapped FI
# model to decide support. `ForecastInterfaceAdapter` has no `__getattr__`
# passthrough, so if it defines `retrain` unconditionally the structural check
# passes for EVERY FI model — including one whose inner model cannot retrain —
# and D2's refusal never fires for the case it exists for. Ask the adapter's
# `supports_warm_start` instead, which proxies the INNER model (the same
# precedent as its `config_hash` property).


@runtime_checkable
class RetrainableStationModel(Protocol):
    def retrain(
        self,
        base_artifact: ModelArtifact,
        data: StationTrainingData,
        params: ModelParams,
        rng: random.Random,
    ) -> ModelArtifact:
        raise NotImplementedError


@runtime_checkable
class RetrainableGroupModel(Protocol):
    def retrain(
        self,
        base_artifact: ModelArtifact,
        data: GroupTrainingData,
        params: ModelParams,
        rng: random.Random,
    ) -> ModelArtifact:
        raise NotImplementedError
