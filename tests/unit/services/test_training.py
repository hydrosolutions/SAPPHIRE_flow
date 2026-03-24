from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import polars as pl

from sapphire_flow.services.training import (
    store_and_promote_artifact,
    train_group_model,
    train_station_model,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelArtifactStatus
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.model import GroupTrainingData, TrainingData
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import FakeModelArtifactStore

_START = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
_END = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_CLOCK = lambda: _NOW  # noqa: E731
_RNG = random.Random(42)


def _make_training_data() -> TrainingData:
    forcing = pl.DataFrame(
        {
            "timestamp": [_START],
            "precipitation": [5.0],
            "temperature": [15.0],
        }
    )
    obs = pl.DataFrame({"timestamp": [_START], "discharge": [10.0]})
    targets = pl.DataFrame({"timestamp": [_START], "discharge": [10.0]})
    return TrainingData(
        forcing=forcing,
        observations=obs,
        targets=targets,
        static_attributes=None,
        time_step=timedelta(hours=1),
        val_start=None,
    )


def _make_group_training_data() -> GroupTrainingData:
    rng = random.Random(1)
    sid = StationId(UUID(int=rng.getrandbits(128), version=4))
    gid = StationGroupId(UUID(int=rng.getrandbits(128), version=4))
    return GroupTrainingData(
        group_id=gid,
        station_data={sid: _make_training_data()},
        time_step=timedelta(hours=1),
        val_start=None,
    )


class TestTrainStationModel:
    def test_returns_bytes(self) -> None:
        model = FakeStationForecastModel()
        data = _make_training_data()
        result = train_station_model(model, data, {}, _RNG)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_bytes_match_fake_artifact(self) -> None:
        model = FakeStationForecastModel()
        data = _make_training_data()
        result = train_station_model(model, data, {}, _RNG)
        assert result == b"fake_artifact"


class TestTrainGroupModel:
    def test_returns_bytes(self) -> None:
        model = FakeGroupForecastModel()
        data = _make_group_training_data()
        result = train_group_model(model, data, {}, _RNG)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_bytes_match_fake_group_artifact(self) -> None:
        model = FakeGroupForecastModel()
        data = _make_group_training_data()
        result = train_group_model(model, data, {}, _RNG)
        assert result == b"fake_group_artifact"


class TestStoreAndPromote:
    def test_promote_first_artifact(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("test_model")
        rng = random.Random(5)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))

        artifact_id = store_and_promote_artifact(
            artifact_store=store,
            model_id=model_id,
            artifact_bytes=b"artifact_v1",
            period_start=_START,
            period_end=_END,
            clock=_CLOCK,
            station_id=station_id,
        )

        record = store.fetch_artifact_record(artifact_id)
        assert record is not None
        assert record.status == ModelArtifactStatus.ACTIVE
        assert record.promoted_at is not None

    def test_store_and_promote_returns_artifact_id(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("test_model")
        rng = random.Random(6)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))

        artifact_id = store_and_promote_artifact(
            artifact_store=store,
            model_id=model_id,
            artifact_bytes=b"v1",
            period_start=_START,
            period_end=_END,
            clock=_CLOCK,
            station_id=station_id,
        )

        assert artifact_id is not None
        result = store.fetch_artifact(artifact_id)
        assert result is not None
        _, stored_bytes = result
        assert stored_bytes == b"v1"

    def test_old_active_artifact_superseded(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("test_model")
        rng = random.Random(7)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))

        # Store first artifact and promote it
        first_id = store_and_promote_artifact(
            artifact_store=store,
            model_id=model_id,
            artifact_bytes=b"v1",
            period_start=_START,
            period_end=_END,
            clock=_CLOCK,
            station_id=station_id,
        )

        # Store second artifact — first should become SUPERSEDED
        second_id = store_and_promote_artifact(
            artifact_store=store,
            model_id=model_id,
            artifact_bytes=b"v2",
            period_start=_START,
            period_end=_END,
            clock=_CLOCK,
            station_id=station_id,
        )

        first_record = store.fetch_artifact_record(first_id)
        second_record = store.fetch_artifact_record(second_id)

        assert first_record is not None
        assert first_record.status == ModelArtifactStatus.SUPERSEDED
        assert first_record.superseded_at is not None

        assert second_record is not None
        assert second_record.status == ModelArtifactStatus.ACTIVE

    def test_only_one_active_after_double_promote(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("test_model")
        rng = random.Random(8)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))

        store_and_promote_artifact(
            artifact_store=store,
            model_id=model_id,
            artifact_bytes=b"v1",
            period_start=_START,
            period_end=_END,
            clock=_CLOCK,
            station_id=station_id,
        )
        store_and_promote_artifact(
            artifact_store=store,
            model_id=model_id,
            artifact_bytes=b"v2",
            period_start=_START,
            period_end=_END,
            clock=_CLOCK,
            station_id=station_id,
        )

        active_ids = store.fetch_artifacts_by_status(
            model_id=model_id,
            status=ModelArtifactStatus.ACTIVE,
            station_id=station_id,
        )
        assert len(active_ids) == 1

    def test_group_scoped_artifact_promoted(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("group_model")
        rng = random.Random(9)
        group_id = StationGroupId(UUID(int=rng.getrandbits(128), version=4))

        artifact_id = store_and_promote_artifact(
            artifact_store=store,
            model_id=model_id,
            artifact_bytes=b"group_artifact",
            period_start=_START,
            period_end=_END,
            clock=_CLOCK,
            group_id=group_id,
        )

        record = store.fetch_artifact_record(artifact_id)
        assert record is not None
        assert record.status == ModelArtifactStatus.ACTIVE
        assert record.group_id == group_id
        assert record.station_id is None
