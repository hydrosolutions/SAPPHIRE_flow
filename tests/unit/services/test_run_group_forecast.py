from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from sqlalchemy.exc import DisconnectionError

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.exceptions import ModelOutputError, StoreError
from sapphire_flow.services import run_group_forecast as service
from sapphire_flow.services.operational_inputs import OperationalInputMetadata
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet, QcFlag
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    ForecastStatus,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    NwpCycleSource,
    QcStatus,
    WarmUpSource,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
from sapphire_flow.types.model import (
    GroupModelInputs,
    StationInputData,
    StationModelInputs,
)
from sapphire_flow.types.station import GroupModelAssignment, StationGroup
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_models import FakeGroupForecastModel, FakeStationForecastModel
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakeStationGroupStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest import MonkeyPatch

_ISSUE = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
_CYCLE = ensure_utc(datetime(2026, 1, 9, 18, tzinfo=UTC))
_STEP = timedelta(hours=1)
_MODEL_ID = ModelId("group-model")
_OTHER_MODEL_ID = ModelId("station-model")


def _clock() -> UtcDatetime:
    return _ISSUE


def _time_frame(data: dict[str, list[object]]) -> pl.DataFrame:
    return pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def _make_station_inputs(
    station_id: StationId,
    base_value: float,
    static: pl.DataFrame | None = None,
) -> StationModelInputs:
    return StationModelInputs(
        station_id=station_id,
        data=StationInputData(
            past_targets=_time_frame(
                {
                    "timestamp": [_ISSUE - _STEP, _ISSUE],
                    "discharge": [base_value, base_value + 1.0],
                }
            ),
            past_dynamic=_time_frame(
                {
                    "timestamp": [_ISSUE - _STEP, _ISSUE],
                    "precipitation": [base_value + 2.0, base_value + 3.0],
                }
            ),
            future_dynamic=_time_frame(
                {
                    "timestamp": [_ISSUE + _STEP, _ISSUE + 2 * _STEP],
                    "temperature": [base_value + 4.0, base_value + 5.0],
                }
            ),
            static=static,
        ),
        issue_time=_ISSUE,
        forecast_horizon_steps=2,
        time_step=_STEP,
    )


def _make_metadata(nwp_age_hours: float) -> OperationalInputMetadata:
    return OperationalInputMetadata(
        warm_up_source=WarmUpSource.FRESH,
        warm_up_state_age_hours=1.0,
        observation_staleness_hours=0.5,
        prior_state=b"state",
        nwp_age_hours=nwp_age_hours,
    )


def _make_group(*station_ids: StationId) -> StationGroup:
    return StationGroup(
        id=StationGroupId(uuid4()),
        name="test-group",
        station_ids=frozenset(station_ids),
        created_at=_ISSUE,
    )


def _call_assemble_group(
    group: StationGroup,
    nwp_source_by_station: dict[StationId, str],
) -> (
    tuple[
        GroupModelInputs,
        dict[StationId, OperationalInputMetadata],
    ]
    | None
):
    return service.assemble_group_operational_inputs(
        group=group,
        model=FakeGroupForecastModel(),
        model_id=_MODEL_ID,
        issue_time=_ISSUE,
        cycle_time=_CYCLE,
        nwp_source_by_station=nwp_source_by_station,
        forcing_source=FakeWeatherReanalysisSource(),
        weather_forecast_store=FakeWeatherForecastStore(),
        obs_store=FakeObservationStore(),
        station_store=FakeStationStore(),
        basin_store=FakeBasinStore(),
        model_state_store=FakeModelStateStore(),
        clock=_clock,
        forecast_horizon_steps=2,
        time_step=_STEP,
    )


def _patch_station_assembler(
    monkeypatch: MonkeyPatch,
    results: dict[
        StationId,
        tuple[StationModelInputs, OperationalInputMetadata] | None,
    ],
    calls: list[tuple[StationId, str]],
) -> None:
    def fake_assemble_station_operational_inputs(
        station_id: StationId,
        model: object,
        model_id: ModelId,
        issue_time: UtcDatetime,
        cycle_time: UtcDatetime,
        nwp_source: str,
        forcing_source: object,
        weather_forecast_store: object,
        obs_store: object,
        station_store: object,
        basin_store: object,
        model_state_store: object,
        clock: Callable[[], UtcDatetime],
        forecast_horizon_steps: int,
        time_step: timedelta,
    ) -> tuple[StationModelInputs, OperationalInputMetadata] | None:
        calls.append((station_id, nwp_source))
        return results[station_id]

    monkeypatch.setattr(
        service,
        "assemble_station_operational_inputs",
        fake_assemble_station_operational_inputs,
    )


def _make_config() -> DeploymentConfig:
    return DeploymentConfig(max_retention_days=1000)


def _make_assignment(group: StationGroup) -> GroupModelAssignment:
    return GroupModelAssignment(
        group_id=group.id,
        model_id=_MODEL_ID,
        time_step=_STEP,
        status=ModelAssignmentStatus.ACTIVE,
        priority=1,
        created_at=_ISSUE,
    )


def _make_ensemble(station_id: StationId, base_value: float) -> ForecastEnsemble:
    rows = [
        {
            "valid_time": _ISSUE + (step + 1) * _STEP,
            "member_id": member,
            "value": base_value + step + member,
        }
        for step in range(2)
        for member in range(2)
    ]
    values = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
    )
    return ForecastEnsemble.from_members(
        station_id=station_id,
        issued_at=_ISSUE,
        parameter="discharge",
        units="m³/s",
        time_step=_STEP,
        values=values,
        model_id=_MODEL_ID,
    )


def _make_group_inputs(group: StationGroup) -> GroupModelInputs:
    station_inputs = [
        _make_station_inputs(sid, float(index + 1))
        for index, sid in enumerate(sorted(group.station_ids, key=str))
    ]
    return GroupModelInputs(
        group_id=group.id,
        station_ids=tuple(station_input.station_id for station_input in station_inputs),
        past_targets=service._stack_station_frames(
            [
                (station_input.station_id, station_input.data.past_targets)
                for station_input in station_inputs
            ]
        ),
        past_dynamic=service._stack_station_frames(
            [
                (station_input.station_id, station_input.data.past_dynamic)
                for station_input in station_inputs
            ]
        ),
        future_dynamic=service._stack_station_frames(
            [
                (station_input.station_id, station_input.data.future_dynamic)
                for station_input in station_inputs
            ]
        ),
        static=None,
        issue_time=_ISSUE,
        forecast_horizon_steps=2,
        time_step=_STEP,
    )


def _make_metadata_by_station(
    station_ids: tuple[StationId, ...],
) -> dict[StationId, OperationalInputMetadata]:
    return {
        sid: _make_metadata(float(index + 1)) for index, sid in enumerate(station_ids)
    }


def _seed_group_artifact(
    artifact_store: FakeModelArtifactStore,
    group: StationGroup,
) -> ArtifactId:
    artifact_id, _ = artifact_store.store_artifact(
        model_id=_MODEL_ID,
        artifact_bytes=b"group-artifact",
        training_period_start=_ISSUE - timedelta(days=30),
        training_period_end=_ISSUE - timedelta(days=1),
        trained_at=_ISSUE - timedelta(hours=1),
        group_id=group.id,
        status=ModelArtifactStatus.ACTIVE,
    )
    return artifact_id


def _id_gen() -> Callable[[], UUID]:
    ids = [UUID(int=value) for value in range(1, 20)]
    index = 0

    def gen() -> UUID:
        nonlocal index
        value = ids[index]
        index += 1
        return value

    return gen


def _empty_qc_rules() -> ForecastQcRuleSet:
    return ForecastQcRuleSet(version="1.0", rules=())


class _BatchGroupModel:
    artifact_scope = FakeGroupForecastModel.artifact_scope
    data_requirements = FakeGroupForecastModel.data_requirements

    def __init__(
        self,
        batch_result: dict[
            StationId,
            tuple[dict[str, ForecastEnsemble], bytes | None],
        ]
        | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.batch_result = batch_result or {}
        self.exc = exc
        self.deserialize_calls: list[bytes] = []
        self.predict_calls = 0

    def deserialize_artifact(self, raw: bytes) -> bytes:
        self.deserialize_calls.append(raw)
        return raw

    def predict_batch(
        self,
        artifact: object,
        inputs: GroupModelInputs,
        rng: random.Random,
    ) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]:
        self.predict_calls += 1
        if self.exc is not None:
            raise self.exc
        return self.batch_result


class _QcChecker:
    def __init__(self, failed_station_id: StationId | None = None) -> None:
        self.failed_station_id = failed_station_id

    def check(
        self,
        ensemble: ForecastEnsemble,
        rule_set: ForecastQcRuleSet,
        qc_overrides: list[object],
        baselines: list[object],
    ) -> list[QcFlag]:
        if ensemble.station_id == self.failed_station_id:
            return [
                QcFlag(
                    rule_id="range_check",
                    rule_version="1.0",
                    status=QcStatus.QC_FAILED,
                    detail="failed for test",
                )
            ]
        return []


def _call_run_group_forecast(
    *,
    group: StationGroup,
    group_inputs: GroupModelInputs,
    metadata_by_station: dict[StationId, OperationalInputMetadata],
    model: _BatchGroupModel,
    artifact_store: FakeModelArtifactStore,
    qc_checker: _QcChecker | None = None,
) -> dict[StationId, service.StationForecastResult]:
    return service.run_group_forecast(
        group=group,
        group_inputs=group_inputs,
        metadata_by_station=metadata_by_station,
        assignment=_make_assignment(group),
        model=model,
        artifact_store=artifact_store,
        qc_checker=qc_checker or _QcChecker(),
        qc_rules=_empty_qc_rules(),
        qc_overrides=[],
        baselines_by_station={sid: [] for sid in group_inputs.station_ids},
        nwp_cycle_reference_time=_CYCLE,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        config=_make_config(),
        clock=_clock,
        id_gen=_id_gen(),
        rng=random.Random(42),
    )


def test_assembles_group_inputs_and_metadata_by_station(
    monkeypatch: MonkeyPatch,
) -> None:
    sid_a = StationId(uuid4())
    sid_b = StationId(uuid4())
    static_a = pl.DataFrame({"elevation_m": [1000.0], "area_km2": [20.0]})
    static_b = pl.DataFrame({"elevation_m": [1500.0], "area_km2": [25.0]})
    station_inputs = {
        sid_a: _make_station_inputs(sid_a, 10.0, static=static_a),
        sid_b: _make_station_inputs(sid_b, 20.0, static=static_b),
    }
    metadata = {sid_a: _make_metadata(0.5), sid_b: _make_metadata(1.0)}
    calls: list[tuple[StationId, str]] = []
    _patch_station_assembler(
        monkeypatch,
        {sid: (station_inputs[sid], metadata[sid]) for sid in (sid_a, sid_b)},
        calls,
    )

    result = _call_assemble_group(
        _make_group(sid_a, sid_b),
        {sid_a: "icon-a", sid_b: "icon-b"},
    )

    assert result is not None
    group_inputs, metadata_by_station = result
    assert set(group_inputs.station_ids) == {sid_a, sid_b}
    assert metadata_by_station == metadata
    assert set(calls) == {(sid_a, "icon-a"), (sid_b, "icon-b")}
    assert group_inputs.past_targets.columns[0] == "station_id"
    assert group_inputs.past_dynamic.columns[0] == "station_id"
    assert group_inputs.future_dynamic.columns[0] == "station_id"
    assert group_inputs.static is not None
    assert group_inputs.static.columns[0] == "station_id"

    for sid, expected in station_inputs.items():
        sliced = group_inputs.for_station(sid)
        assert_frame_equal(sliced.past_targets, expected.data.past_targets)
        assert_frame_equal(sliced.past_dynamic, expected.data.past_dynamic)
        assert_frame_equal(sliced.future_dynamic, expected.data.future_dynamic)
        assert sliced.static is not None
        assert expected.data.static is not None
        assert_frame_equal(sliced.static, expected.data.static)


def test_skips_station_when_station_assembly_returns_none(
    monkeypatch: MonkeyPatch,
) -> None:
    sid_a = StationId(uuid4())
    sid_b = StationId(uuid4())
    inputs_a = _make_station_inputs(sid_a, 10.0)
    metadata_a = _make_metadata(0.5)
    calls: list[tuple[StationId, str]] = []
    _patch_station_assembler(
        monkeypatch,
        {sid_a: (inputs_a, metadata_a), sid_b: None},
        calls,
    )

    result = _call_assemble_group(
        _make_group(sid_a, sid_b),
        {sid_a: "icon-a", sid_b: "icon-b"},
    )

    assert result is not None
    group_inputs, metadata_by_station = result
    assert group_inputs.station_ids == (sid_a,)
    assert metadata_by_station == {sid_a: metadata_a}
    assert set(calls) == {(sid_a, "icon-a"), (sid_b, "icon-b")}
    assert_frame_equal(
        group_inputs.for_station(sid_a).past_targets,
        inputs_a.data.past_targets,
    )


def test_all_stations_none_returns_none(monkeypatch: MonkeyPatch) -> None:
    sid_a = StationId(uuid4())
    sid_b = StationId(uuid4())
    calls: list[tuple[StationId, str]] = []
    _patch_station_assembler(monkeypatch, {sid_a: None, sid_b: None}, calls)

    result = _call_assemble_group(
        _make_group(sid_a, sid_b),
        {sid_a: "icon-a", sid_b: "icon-b"},
    )

    assert result is None
    assert set(calls) == {(sid_a, "icon-a"), (sid_b, "icon-b")}


def test_discover_group_runs_only_group_scoped_models() -> None:
    group_model_group = _make_group(StationId(uuid4()))
    station_model_group = _make_group(StationId(uuid4()))
    group_store = FakeStationGroupStore()
    group_store.store_group(group_model_group)
    group_store.store_group(station_model_group)
    group_store.store_group_model_assignment(
        GroupModelAssignment(
            group_id=group_model_group.id,
            model_id=_MODEL_ID,
            time_step=_STEP,
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_ISSUE,
        )
    )
    group_store.store_group_model_assignment(
        GroupModelAssignment(
            group_id=station_model_group.id,
            model_id=_OTHER_MODEL_ID,
            time_step=_STEP,
            status=ModelAssignmentStatus.ACTIVE,
            priority=1,
            created_at=_ISSUE,
        )
    )

    runs = service.discover_group_runs(
        {
            _MODEL_ID: FakeGroupForecastModel(),
            _OTHER_MODEL_ID: FakeStationForecastModel(),
        },
        group_store,
    )

    assert runs == [(group_model_group, _MODEL_ID)]


def test_run_group_forecast_returns_station_results() -> None:
    sid_a = StationId(uuid4())
    sid_b = StationId(uuid4())
    group = _make_group(sid_a, sid_b)
    group_inputs = _make_group_inputs(group)
    artifact_store = FakeModelArtifactStore()
    artifact_id = _seed_group_artifact(artifact_store, group)
    ensembles = {
        sid_a: {"discharge": _make_ensemble(sid_a, 10.0)},
        sid_b: {"discharge": _make_ensemble(sid_b, 20.0)},
    }
    model = _BatchGroupModel(
        {
            sid_a: (ensembles[sid_a], b"state-a"),
            sid_b: (ensembles[sid_b], b"state-b"),
        }
    )

    results = _call_run_group_forecast(
        group=group,
        group_inputs=group_inputs,
        metadata_by_station=_make_metadata_by_station(group_inputs.station_ids),
        model=model,
        artifact_store=artifact_store,
    )

    assert set(results) == {sid_a, sid_b}
    assert model.deserialize_calls == [b"group-artifact"]
    assert model.predict_calls == 1
    for sid, expected_state in [(sid_a, b"state-a"), (sid_b, b"state-b")]:
        station_result = results[sid]
        assert station_result.station_id == sid
        assert station_result.model_id == _MODEL_ID
        assert station_result.artifact_id == artifact_id
        assert station_result.new_state == expected_state
        assert station_result.ensembles == ensembles[sid]
        assert len(station_result.forecasts) == 1
        forecast = station_result.forecasts[0]
        assert forecast.station_id == sid
        assert forecast.model_id == _MODEL_ID
        assert forecast.model_artifact_id == artifact_id
        assert forecast.ensemble is ensembles[sid]["discharge"]
        assert forecast.status == ForecastStatus.RAW
        assert forecast.qc_status == QcStatus.QC_PASSED
        assert forecast.issued_at == _ISSUE
        assert forecast.nwp_cycle_reference_time == _CYCLE
        assert forecast.nwp_cycle_source == NwpCycleSource.PRIMARY


def test_run_group_forecast_no_active_artifact_returns_empty() -> None:
    sid = StationId(uuid4())
    group = _make_group(sid)
    group_inputs = _make_group_inputs(group)
    model = _BatchGroupModel({sid: ({"discharge": _make_ensemble(sid, 10.0)}, None)})

    results = _call_run_group_forecast(
        group=group,
        group_inputs=group_inputs,
        metadata_by_station=_make_metadata_by_station(group_inputs.station_ids),
        model=model,
        artifact_store=FakeModelArtifactStore(),
    )

    assert results == {}
    assert model.deserialize_calls == []
    assert model.predict_calls == 0


def test_run_group_forecast_omits_station_with_qc_failed_parameter() -> None:
    sid_a = StationId(uuid4())
    sid_b = StationId(uuid4())
    group = _make_group(sid_a, sid_b)
    group_inputs = _make_group_inputs(group)
    artifact_store = FakeModelArtifactStore()
    _seed_group_artifact(artifact_store, group)
    model = _BatchGroupModel(
        {
            sid_a: ({"discharge": _make_ensemble(sid_a, 10.0)}, b"state-a"),
            sid_b: ({"discharge": _make_ensemble(sid_b, 20.0)}, b"state-b"),
        }
    )

    results = _call_run_group_forecast(
        group=group,
        group_inputs=group_inputs,
        metadata_by_station=_make_metadata_by_station(group_inputs.station_ids),
        model=model,
        artifact_store=artifact_store,
        qc_checker=_QcChecker(failed_station_id=sid_b),
    )

    assert set(results) == {sid_a}
    assert results[sid_a].new_state == b"state-a"


def test_run_group_forecast_logs_missing_station_output() -> None:
    import structlog.testing

    sid_a = StationId(uuid4())
    sid_b = StationId(uuid4())
    group = _make_group(sid_a, sid_b)
    group_inputs = _make_group_inputs(group)
    artifact_store = FakeModelArtifactStore()
    _seed_group_artifact(artifact_store, group)
    model = _BatchGroupModel(
        {
            sid_a: (
                {"discharge": _make_ensemble(sid_a, 10.0)},
                b"state-a",
            )
        }
    )

    with structlog.testing.capture_logs() as captured:
        results = _call_run_group_forecast(
            group=group,
            group_inputs=group_inputs,
            metadata_by_station=_make_metadata_by_station(group_inputs.station_ids),
            model=model,
            artifact_store=artifact_store,
        )

    assert set(results) == {sid_a}
    events = [
        event
        for event in captured
        if event.get("event") == "run_group_forecast.batch_missing_station_outputs"
    ]
    assert len(events) == 1
    assert events[0]["station_ids"] == [str(sid_b)]


def test_run_group_forecast_skips_unknown_station_output() -> None:
    import structlog.testing

    sid = StationId(uuid4())
    unknown_sid = StationId(uuid4())
    group = _make_group(sid)
    group_inputs = _make_group_inputs(group)
    artifact_store = FakeModelArtifactStore()
    _seed_group_artifact(artifact_store, group)
    model = _BatchGroupModel(
        {
            sid: ({"discharge": _make_ensemble(sid, 10.0)}, b"state"),
            unknown_sid: (
                {"discharge": _make_ensemble(unknown_sid, 99.0)},
                b"unknown-state",
            ),
        }
    )

    with structlog.testing.capture_logs() as captured:
        results = _call_run_group_forecast(
            group=group,
            group_inputs=group_inputs,
            metadata_by_station=_make_metadata_by_station(group_inputs.station_ids),
            model=model,
            artifact_store=artifact_store,
        )

    assert set(results) == {sid}
    events = [
        event
        for event in captured
        if event.get("event") == "run_group_forecast.batch_unexpected_station_output"
    ]
    assert len(events) == 1
    assert events[0]["station_id"] == str(unknown_sid)


def test_run_group_forecast_logs_empty_batch_output() -> None:
    import structlog.testing

    sid = StationId(uuid4())
    group = _make_group(sid)
    group_inputs = _make_group_inputs(group)
    artifact_store = FakeModelArtifactStore()
    _seed_group_artifact(artifact_store, group)

    with structlog.testing.capture_logs() as captured:
        results = _call_run_group_forecast(
            group=group,
            group_inputs=group_inputs,
            metadata_by_station=_make_metadata_by_station(group_inputs.station_ids),
            model=_BatchGroupModel({}),
            artifact_store=artifact_store,
        )

    assert results == {}
    assert any(
        event.get("event") == "run_group_forecast.batch_empty" for event in captured
    )
    assert any(
        event.get("event") == "run_group_forecast.batch_missing_station_outputs"
        for event in captured
    )


def test_run_group_forecast_model_output_error_returns_empty() -> None:
    sid = StationId(uuid4())
    group = _make_group(sid)
    group_inputs = _make_group_inputs(group)
    artifact_store = FakeModelArtifactStore()
    _seed_group_artifact(artifact_store, group)

    results = _call_run_group_forecast(
        group=group,
        group_inputs=group_inputs,
        metadata_by_station=_make_metadata_by_station(group_inputs.station_ids),
        model=_BatchGroupModel(exc=ModelOutputError("bad output")),
        artifact_store=artifact_store,
    )

    assert results == {}


def test_run_group_forecast_connection_fatal_propagates_store_error() -> None:
    sid = StationId(uuid4())
    group = _make_group(sid)
    group_inputs = _make_group_inputs(group)
    artifact_store = FakeModelArtifactStore()
    _seed_group_artifact(artifact_store, group)

    with pytest.raises(StoreError):
        _call_run_group_forecast(
            group=group,
            group_inputs=group_inputs,
            metadata_by_station=_make_metadata_by_station(group_inputs.station_ids),
            model=_BatchGroupModel(exc=DisconnectionError("connection reset")),
            artifact_store=artifact_store,
        )


class _NwpBatchGroupModel:
    """GROUP model declaring future NWP forcing (Plan 090 Finding 2)."""

    def __init__(
        self,
        batch_result: dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]],
    ) -> None:
        from sapphire_flow.types.enums import (
            ArtifactScope,
            EnsembleMode,
            SpatialRepresentation,
        )
        from sapphire_flow.types.model import ModelDataRequirements

        self.artifact_scope = ArtifactScope.GROUP
        self.data_requirements = ModelDataRequirements(
            target_parameters=frozenset({"discharge"}),
            past_dynamic_features=frozenset(),
            future_dynamic_features=frozenset({"precipitation"}),
            static_features=frozenset(),
            supported_time_steps=frozenset({_STEP}),
            lookback_steps=1,
            forecast_horizon_steps=2,
            spatial_input_type=SpatialRepresentation.BASIN_AVERAGE,
            ensemble_mode=EnsembleMode.SINGLE,
        )
        self.batch_result = batch_result
        self.predict_calls = 0
        self.deserialize_calls: list[bytes] = []

    def deserialize_artifact(self, raw: bytes) -> bytes:
        self.deserialize_calls.append(raw)
        return raw

    def predict_batch(
        self,
        artifact: object,
        inputs: GroupModelInputs,
        rng: random.Random,
    ) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]:
        self.predict_calls += 1
        return self.batch_result


def _precip_group_inputs(group: StationGroup, future_rows: int) -> GroupModelInputs:
    station_inputs: list[StationModelInputs] = []
    for sid in sorted(group.station_ids, key=str):
        times = [_ISSUE + (i + 1) * _STEP for i in range(future_rows)]
        future_dynamic = _time_frame(
            {"timestamp": list(times), "precipitation": [1.0] * future_rows}
        )
        station_inputs.append(
            StationModelInputs(
                station_id=sid,
                data=StationInputData(
                    past_targets=_time_frame(
                        {"timestamp": [_ISSUE], "discharge": [10.0]}
                    ),
                    past_dynamic=_time_frame(
                        {"timestamp": [_ISSUE], "precipitation": [1.0]}
                    ),
                    future_dynamic=future_dynamic,
                    static=None,
                ),
                issue_time=_ISSUE,
                forecast_horizon_steps=2,
                time_step=_STEP,
            )
        )
    return GroupModelInputs(
        group_id=group.id,
        station_ids=tuple(si.station_id for si in station_inputs),
        past_targets=service._stack_station_frames(
            [(si.station_id, si.data.past_targets) for si in station_inputs]
        ),
        past_dynamic=service._stack_station_frames(
            [(si.station_id, si.data.past_dynamic) for si in station_inputs]
        ),
        future_dynamic=service._stack_station_frames(
            [(si.station_id, si.data.future_dynamic) for si in station_inputs]
        ),
        static=None,
        issue_time=_ISSUE,
        forecast_horizon_steps=2,
        time_step=_STEP,
    )


class TestGroupCoverageGuard:
    """Plan 090 Finding 2: the GROUP path enforces the same D1 coverage guard
    before ``predict_batch`` so an NWP-consuming group model cannot emit a
    truncated batch forecast when a member station's future frame is short.
    """

    def test_short_future_frame_skips_group_model(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        group = _make_group(sid_a, sid_b)
        # 1 future row but the model needs forecast_horizon_steps=2.
        group_inputs = _precip_group_inputs(group, future_rows=1)
        artifact_store = FakeModelArtifactStore()
        _seed_group_artifact(artifact_store, group)
        model = _NwpBatchGroupModel(
            {
                sid_a: ({"discharge": _make_ensemble(sid_a, 10.0)}, None),
                sid_b: ({"discharge": _make_ensemble(sid_b, 20.0)}, None),
            }
        )

        results = _call_run_group_forecast(
            group=group,
            group_inputs=group_inputs,
            metadata_by_station=_make_metadata_by_station(group_inputs.station_ids),
            model=model,  # type: ignore[arg-type]
            artifact_store=artifact_store,
        )

        assert results == {}
        assert model.predict_calls == 0

    def test_adequate_future_frame_runs_group_model(self) -> None:
        sid_a = StationId(uuid4())
        sid_b = StationId(uuid4())
        group = _make_group(sid_a, sid_b)
        group_inputs = _precip_group_inputs(group, future_rows=2)
        artifact_store = FakeModelArtifactStore()
        _seed_group_artifact(artifact_store, group)
        model = _NwpBatchGroupModel(
            {
                sid_a: ({"discharge": _make_ensemble(sid_a, 10.0)}, None),
                sid_b: ({"discharge": _make_ensemble(sid_b, 20.0)}, None),
            }
        )

        results = _call_run_group_forecast(
            group=group,
            group_inputs=group_inputs,
            metadata_by_station=_make_metadata_by_station(group_inputs.station_ids),
            model=model,  # type: ignore[arg-type]
            artifact_store=artifact_store,
        )

        assert set(results) == {sid_a, sid_b}
        assert model.predict_calls == 1
