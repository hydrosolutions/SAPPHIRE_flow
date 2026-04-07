from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from sapphire_flow.types.enums import ModelArtifactStatus

if TYPE_CHECKING:
    import random
    from collections.abc import Callable

    from sapphire_flow.protocols.forecast_model import (
        GroupForecastModel,
        StationForecastModel,
    )
    from sapphire_flow.protocols.stores import ModelArtifactStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
    from sapphire_flow.types.model import (
        GroupTrainingData,
        ModelParams,
        StationTrainingData,
    )

log = structlog.get_logger()


def train_station_model(
    model: StationForecastModel,
    data: StationTrainingData,
    params: ModelParams,
    rng: random.Random,
) -> bytes:
    artifact = model.train(data, params, rng)
    return model.serialize_artifact(artifact)


def train_group_model(
    model: GroupForecastModel,
    data: GroupTrainingData,
    params: ModelParams,
    rng: random.Random,
) -> bytes:
    artifact = model.train(data, params, rng)
    return model.serialize_artifact(artifact)


def promote_artifact(
    artifact_store: ModelArtifactStore,
    model_id: ModelId,
    new_id: ArtifactId,
    *,
    station_id: StationId | None = None,
    group_id: StationGroupId | None = None,
) -> None:
    """Transition existing ACTIVE artifacts to SUPERSEDED, then activate new_id."""
    existing_active = artifact_store.fetch_artifacts_by_status(
        model_id=model_id,
        status=ModelArtifactStatus.ACTIVE,
        station_id=station_id,
        group_id=group_id,
    )
    for old_id in existing_active:
        artifact_store.transition_artifact_status(
            old_id, ModelArtifactStatus.SUPERSEDED
        )
        log.info(
            "training.artifact_superseded",
            model_id=str(model_id),
            artifact_id=str(old_id),
        )

    artifact_store.transition_artifact_status(new_id, ModelArtifactStatus.ACTIVE)
    log.info(
        "training.artifact_promoted",
        model_id=str(model_id),
        artifact_id=str(new_id),
    )


def store_and_promote_artifact(
    artifact_store: ModelArtifactStore,
    model_id: ModelId,
    artifact_bytes: bytes,
    period_start: UtcDatetime,
    period_end: UtcDatetime,
    clock: Callable[[], UtcDatetime],
    *,
    station_id: StationId | None = None,
    group_id: StationGroupId | None = None,
) -> ArtifactId:
    trained_at = clock()
    new_id, _sha256 = artifact_store.store_artifact(
        model_id=model_id,
        artifact_bytes=artifact_bytes,
        training_period_start=period_start,
        training_period_end=period_end,
        trained_at=trained_at,
        station_id=station_id,
        group_id=group_id,
    )

    promote_artifact(
        artifact_store=artifact_store,
        model_id=model_id,
        new_id=new_id,
        station_id=station_id,
        group_id=group_id,
    )

    return new_id
