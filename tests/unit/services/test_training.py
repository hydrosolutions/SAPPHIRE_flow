from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.exceptions import TenantIsolationError
from sapphire_flow.services.training import (
    promote_artifact,
    store_and_promote_artifact,
    train_group_model,
    train_station_model,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelArtifactStatus
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId, TenantId
from sapphire_flow.types.model import GroupTrainingData, StationTrainingData
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from sapphire_flow.types.write_principal import WritePrincipal
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import FakeAuditLogStore, FakeModelArtifactStore

_START = ensure_utc(datetime(2020, 1, 1, tzinfo=UTC))
_END = ensure_utc(datetime(2021, 1, 1, tzinfo=UTC))
_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_CLOCK = lambda: _NOW  # noqa: E731
_RNG = random.Random(42)


def _make_training_data() -> StationTrainingData:
    past_targets = pl.DataFrame({"timestamp": [_START], "discharge": [10.0]})
    past_dynamic = pl.DataFrame(
        {
            "timestamp": [_START],
            "precipitation": [5.0],
            "temperature": [15.0],
        }
    )
    future_dynamic = past_dynamic.clear()
    return StationTrainingData(
        past_targets=past_targets,
        past_dynamic=past_dynamic,
        future_dynamic=future_dynamic,
        static=None,
        time_step=timedelta(hours=1),
        val_start=None,
    )


def _make_group_training_data() -> GroupTrainingData:
    rng = random.Random(1)
    sid = StationId(UUID(int=rng.getrandbits(128), version=4))
    gid = StationGroupId(UUID(int=rng.getrandbits(128), version=4))
    data = _make_training_data()
    sid_col = pl.lit(str(sid)).alias("station_id")

    def _reorder(df: pl.DataFrame) -> pl.DataFrame:
        cols = ["station_id"] + [c for c in df.columns if c != "station_id"]
        return df.select(cols)

    return GroupTrainingData(
        group_id=gid,
        station_ids=(sid,),
        past_targets=_reorder(data.past_targets.with_columns(sid_col)),
        past_dynamic=_reorder(data.past_dynamic.with_columns(sid_col)),
        future_dynamic=_reorder(data.future_dynamic.with_columns(sid_col)),
        static=None,
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


class TestPromoteArtifact:
    def test_promote_artifact_standalone(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("test_model")
        rng = random.Random(10)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        now = _NOW

        # Store an artifact directly (in TRAINING status)
        new_id, _sha = store.store_artifact(
            model_id=model_id,
            artifact_bytes=b"artifact",
            training_period_start=_START,
            training_period_end=_END,
            trained_at=now,
            station_id=station_id,
        )

        promote_artifact(
            artifact_store=store,
            model_id=model_id,
            new_id=new_id,
            station_id=station_id,
        )

        record = store.fetch_artifact_record(new_id)
        assert record is not None
        assert record.status == ModelArtifactStatus.ACTIVE

    def test_promote_artifact_supersedes_existing(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("test_model")
        rng = random.Random(11)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        now = _NOW

        # First artifact — store and promote via store_and_promote
        first_id = store_and_promote_artifact(
            artifact_store=store,
            model_id=model_id,
            artifact_bytes=b"v1",
            period_start=_START,
            period_end=_END,
            clock=_CLOCK,
            station_id=station_id,
        )

        # Second artifact — store raw, then promote via promote_artifact
        second_id, _sha = store.store_artifact(
            model_id=model_id,
            artifact_bytes=b"v2",
            training_period_start=_START,
            training_period_end=_END,
            trained_at=now,
            station_id=station_id,
        )

        promote_artifact(
            artifact_store=store,
            model_id=model_id,
            new_id=second_id,
            station_id=station_id,
        )

        first_record = store.fetch_artifact_record(first_id)
        second_record = store.fetch_artifact_record(second_id)

        assert first_record is not None
        assert first_record.status == ModelArtifactStatus.SUPERSEDED
        assert second_record is not None
        assert second_record.status == ModelArtifactStatus.ACTIVE


class TestPromotionTenantIsolation:
    """Plan 147 Slice E (R5/G6): promote_artifact/store_and_promote_artifact
    is the single promotion chokepoint every write path funnels through —
    tenant write-isolation is proven here. A rejection leaves NO domain
    change (no artifact row for store_and_promote_artifact; the artifact
    stays TRAINING, never ACTIVE, for standalone promote_artifact)."""

    def test_cross_tenant_store_and_promote_rejected_no_domain_change(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("test_model")
        rng = random.Random(20)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        tenant_b = TenantId(UUID(int=random.Random(21).getrandbits(128), version=4))
        audit = FakeAuditLogStore()
        principal = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)

        with pytest.raises(TenantIsolationError):
            store_and_promote_artifact(
                artifact_store=store,
                model_id=model_id,
                artifact_bytes=b"v1",
                period_start=_START,
                period_end=_END,
                clock=_CLOCK,
                station_id=station_id,
                principal=principal,
                target_tenant_id=tenant_b,
                audit_log_store=audit,
            )

        # No domain change: no artifact row was ever stored.
        assert (
            store.fetch_artifacts_by_status(
                model_id=model_id, status=ModelArtifactStatus.ACTIVE
            )
            == []
        )
        assert (
            store.fetch_artifacts_by_status(
                model_id=model_id, status=ModelArtifactStatus.TRAINING
            )
            == []
        )
        assert len(audit._entries) == 1  # type: ignore[attr-defined]
        assert audit._entries[0].event_type.value == "model_rejected"  # type: ignore[attr-defined]

    def test_same_tenant_store_and_promote_succeeds_and_audits(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("test_model")
        rng = random.Random(22)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        audit = FakeAuditLogStore()
        principal = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)

        artifact_id = store_and_promote_artifact(
            artifact_store=store,
            model_id=model_id,
            artifact_bytes=b"v1",
            period_start=_START,
            period_end=_END,
            clock=_CLOCK,
            station_id=station_id,
            principal=principal,
            target_tenant_id=DEFAULT_TENANT_ID,
            audit_log_store=audit,
        )

        record = store.fetch_artifact_record(artifact_id)
        assert record is not None
        assert record.status == ModelArtifactStatus.ACTIVE
        assert any(
            e.event_type.value == "model_promoted"  # type: ignore[attr-defined]
            for e in audit._entries  # type: ignore[attr-defined]
        )

    def test_global_admin_promotes_across_tenants(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("test_model")
        rng = random.Random(23)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        tenant_b = TenantId(UUID(int=random.Random(24).getrandbits(128), version=4))
        principal = WritePrincipal(id=None, tenant_id=None)

        artifact_id = store_and_promote_artifact(
            artifact_store=store,
            model_id=model_id,
            artifact_bytes=b"v1",
            period_start=_START,
            period_end=_END,
            clock=_CLOCK,
            station_id=station_id,
            principal=principal,
            target_tenant_id=tenant_b,
        )

        record = store.fetch_artifact_record(artifact_id)
        assert record is not None
        assert record.status == ModelArtifactStatus.ACTIVE

    def test_cross_tenant_standalone_promote_rejected_leaves_training(self) -> None:
        store = FakeModelArtifactStore()
        model_id = ModelId("test_model")
        rng = random.Random(25)
        station_id = StationId(UUID(int=rng.getrandbits(128), version=4))
        tenant_b = TenantId(UUID(int=random.Random(26).getrandbits(128), version=4))
        principal = WritePrincipal(id=None, tenant_id=DEFAULT_TENANT_ID)

        new_id, _sha = store.store_artifact(
            model_id=model_id,
            artifact_bytes=b"artifact",
            training_period_start=_START,
            training_period_end=_END,
            trained_at=_NOW,
            station_id=station_id,
        )

        with pytest.raises(TenantIsolationError):
            promote_artifact(
                artifact_store=store,
                model_id=model_id,
                new_id=new_id,
                station_id=station_id,
                principal=principal,
                target_tenant_id=tenant_b,
                now=_NOW,
            )

        record = store.fetch_artifact_record(new_id)
        assert record is not None
        assert record.status == ModelArtifactStatus.TRAINING
