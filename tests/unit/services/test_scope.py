from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sapphire_flow.services.scope import determine_training_scope
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ArtifactScope,
    ModelAssignmentStatus,
    StationStatus,
)
from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.model import ModelRecord
from sapphire_flow.types.station import ModelAssignment, StationGroup
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeModelStore,
    FakeStationGroupStore,
    FakeStationStore,
)

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_END = ensure_utc(datetime(2025, 12, 31, tzinfo=UTC))
_STEP = timedelta(hours=24)


def _model_record(model_id: ModelId, scope: ArtifactScope) -> ModelRecord:
    return ModelRecord(
        id=model_id,
        display_name=str(model_id),
        artifact_scope=scope,
        description="",
        created_at=_EPOCH,
    )


def _assignment(
    station_id: StationId,
    model_id: ModelId,
    status: ModelAssignmentStatus = ModelAssignmentStatus.ACTIVE,
) -> ModelAssignment:
    return ModelAssignment(
        station_id=station_id,
        model_id=model_id,
        time_step=_STEP,
        status=status,
        priority=1,
        created_at=_EPOCH,
    )


def _call(
    model_store: FakeModelStore,
    station_store: FakeStationStore,
    group_store: FakeStationGroupStore,
    *,
    model_ids: list[ModelId] | None = None,
    station_ids: list[StationId] | None = None,
    group_ids: list[StationGroupId] | None = None,
):
    return determine_training_scope(
        model_ids=model_ids,
        station_ids=station_ids,
        group_ids=group_ids,
        period_start=_EPOCH,
        period_end=_END,
        time_step=_STEP,
        model_store=model_store,
        station_store=station_store,
        group_store=group_store,
    )


class TestStationScoped:
    def test_basic(self):
        mid = ModelId("linear_daily")
        s1 = make_station_config(station_id=StationId(uuid4()), code="S1")
        s2 = make_station_config(station_id=StationId(uuid4()), code="S2")

        model_store = FakeModelStore()
        model_store.register_model(_model_record(mid, ArtifactScope.STATION))
        station_store = FakeStationStore()
        station_store.store_station(s1)
        station_store.store_station(s2)
        station_store.store_model_assignment(_assignment(s1.id, mid))
        station_store.store_model_assignment(_assignment(s2.id, mid))
        group_store = FakeStationGroupStore()

        scope = _call(model_store, station_store, group_store)

        assert len(scope.units) == 2
        unit_station_ids = {u.station_id for u in scope.units}
        assert unit_station_ids == {s1.id, s2.id}
        for u in scope.units:
            assert u.model_id == mid
            assert u.group_id is None
            assert u.training_period_start == _EPOCH
            assert u.training_period_end == _END
            assert u.time_step == _STEP

    def test_filters_non_operational(self):
        mid = ModelId("linear_daily")
        s_op = make_station_config(
            station_id=StationId(uuid4()),
            code="OP",
            station_status=StationStatus.OPERATIONAL,
        )
        s_onb = make_station_config(
            station_id=StationId(uuid4()),
            code="ONB",
            station_status=StationStatus.ONBOARDING,
        )

        model_store = FakeModelStore()
        model_store.register_model(_model_record(mid, ArtifactScope.STATION))
        station_store = FakeStationStore()
        station_store.store_station(s_op)
        station_store.store_station(s_onb)
        station_store.store_model_assignment(_assignment(s_op.id, mid))
        station_store.store_model_assignment(_assignment(s_onb.id, mid))
        group_store = FakeStationGroupStore()

        scope = _call(model_store, station_store, group_store)

        assert len(scope.units) == 1
        assert scope.units[0].station_id == s_op.id

    def test_filters_unassigned(self):
        mid = ModelId("linear_daily")
        s_assigned = make_station_config(station_id=StationId(uuid4()), code="A")
        s_unassigned = make_station_config(station_id=StationId(uuid4()), code="B")

        model_store = FakeModelStore()
        model_store.register_model(_model_record(mid, ArtifactScope.STATION))
        station_store = FakeStationStore()
        station_store.store_station(s_assigned)
        station_store.store_station(s_unassigned)
        station_store.store_model_assignment(_assignment(s_assigned.id, mid))
        group_store = FakeStationGroupStore()

        scope = _call(model_store, station_store, group_store)

        assert len(scope.units) == 1
        assert scope.units[0].station_id == s_assigned.id

    def test_filters_inactive_assignment(self):
        mid = ModelId("linear_daily")
        s1 = make_station_config(station_id=StationId(uuid4()), code="ACT")
        s2 = make_station_config(station_id=StationId(uuid4()), code="INA")

        model_store = FakeModelStore()
        model_store.register_model(_model_record(mid, ArtifactScope.STATION))
        station_store = FakeStationStore()
        station_store.store_station(s1)
        station_store.store_station(s2)
        station_store.store_model_assignment(
            _assignment(s1.id, mid, ModelAssignmentStatus.ACTIVE)
        )
        station_store.store_model_assignment(
            _assignment(s2.id, mid, ModelAssignmentStatus.INACTIVE)
        )
        group_store = FakeStationGroupStore()

        scope = _call(model_store, station_store, group_store)

        assert len(scope.units) == 1
        assert scope.units[0].station_id == s1.id

    def test_station_ids_filter(self):
        mid = ModelId("linear_daily")
        s1 = make_station_config(station_id=StationId(uuid4()), code="S1")
        s2 = make_station_config(station_id=StationId(uuid4()), code="S2")
        s3 = make_station_config(station_id=StationId(uuid4()), code="S3")

        model_store = FakeModelStore()
        model_store.register_model(_model_record(mid, ArtifactScope.STATION))
        station_store = FakeStationStore()
        station_store.store_station(s1)
        station_store.store_station(s2)
        station_store.store_station(s3)
        station_store.store_model_assignment(_assignment(s1.id, mid))
        station_store.store_model_assignment(_assignment(s2.id, mid))
        station_store.store_model_assignment(_assignment(s3.id, mid))
        group_store = FakeStationGroupStore()

        scope = _call(
            model_store, station_store, group_store, station_ids=[s1.id, s3.id]
        )

        assert len(scope.units) == 2
        unit_station_ids = {u.station_id for u in scope.units}
        assert unit_station_ids == {s1.id, s3.id}


class TestGroupScoped:
    def test_basic(self):
        mid = ModelId("lstm_group")
        s1_id = StationId(uuid4())
        s2_id = StationId(uuid4())
        gid = StationGroupId(uuid4())
        group = StationGroup(
            id=gid,
            name="test-group",
            station_ids=frozenset({s1_id, s2_id}),
            created_at=_EPOCH,
        )

        model_store = FakeModelStore()
        model_store.register_model(_model_record(mid, ArtifactScope.GROUP))
        station_store = FakeStationStore()
        group_store = FakeStationGroupStore()
        group_store.store_group(group)
        group_store.seed_group_model_assignment(gid, mid)

        scope = _call(model_store, station_store, group_store)

        assert len(scope.units) == 1
        unit = scope.units[0]
        assert unit.model_id == mid
        assert unit.group_id == gid
        assert unit.station_id is None
        assert unit.station_ids == frozenset({s1_id, s2_id})
        assert unit.training_period_start == _EPOCH
        assert unit.training_period_end == _END

    def test_group_ids_filter(self):
        mid = ModelId("lstm_group")
        g1_id = StationGroupId(uuid4())
        g2_id = StationGroupId(uuid4())
        g1 = StationGroup(
            id=g1_id,
            name="group-1",
            station_ids=frozenset({StationId(uuid4())}),
            created_at=_EPOCH,
        )
        g2 = StationGroup(
            id=g2_id,
            name="group-2",
            station_ids=frozenset({StationId(uuid4())}),
            created_at=_EPOCH,
        )

        model_store = FakeModelStore()
        model_store.register_model(_model_record(mid, ArtifactScope.GROUP))
        station_store = FakeStationStore()
        group_store = FakeStationGroupStore()
        group_store.store_group(g1)
        group_store.store_group(g2)
        group_store.seed_group_model_assignment(g1_id, mid)
        group_store.seed_group_model_assignment(g2_id, mid)

        scope = _call(model_store, station_store, group_store, group_ids=[g1_id])

        assert len(scope.units) == 1
        assert scope.units[0].group_id == g1_id


class TestModelIdsFilter:
    def test_model_ids_filter(self):
        mid1 = ModelId("model_a")
        mid2 = ModelId("model_b")
        station = make_station_config(station_id=StationId(uuid4()), code="S1")

        model_store = FakeModelStore()
        model_store.register_model(_model_record(mid1, ArtifactScope.STATION))
        model_store.register_model(_model_record(mid2, ArtifactScope.STATION))
        station_store = FakeStationStore()
        station_store.store_station(station)
        station_store.store_model_assignment(_assignment(station.id, mid1))
        station_store.store_model_assignment(_assignment(station.id, mid2))
        group_store = FakeStationGroupStore()

        scope = _call(model_store, station_store, group_store, model_ids=[mid1])

        assert len(scope.units) == 1
        assert scope.units[0].model_id == mid1


class TestEmptyScope:
    def test_empty_scope_no_models(self):
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        group_store = FakeStationGroupStore()

        scope = _call(model_store, station_store, group_store)

        assert scope.units == ()

    def test_empty_scope_explicit_model_ids_none_registered(self):
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        group_store = FakeStationGroupStore()

        scope = _call(
            model_store, station_store, group_store, model_ids=[ModelId("missing")]
        )

        assert scope.units == ()
