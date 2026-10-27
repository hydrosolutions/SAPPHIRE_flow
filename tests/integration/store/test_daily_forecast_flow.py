from __future__ import annotations

import random
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from sapphire_flow.config.daily_evaluation import (
    DailyEvaluationConfig,
    DailyOutputContractConfig,
)
from sapphire_flow.config.evaluation_forecasts import EvaluationForecastsConfig
from sapphire_flow.db.metadata import model_artifacts
from sapphire_flow.exceptions import (
    ConfigurationError,
    GroupForecastError,
    ModelOutputError,
    StoreError,
)
from sapphire_flow.flows import daily_forecast_execution as execution
from sapphire_flow.flows.daily_forecast_context import ScheduledDailyRun
from sapphire_flow.flows.run_forecast_cycle import run_forecast_cycle_flow
from sapphire_flow.services import forecast_combination
from sapphire_flow.services.run_group_forecast import GroupForecastOutcome
from sapphire_flow.store.committed_forecast_writer import PgCommittedForecastWriter
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptState, ScheduledDailyForecast
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    DailyFailureReason,
    FrozenDailyAssignment,
)
from sapphire_flow.types.enums import (
    AlertEligibility,
    EnsembleRepresentation,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    ModelCombinationStrategy,
    PipelineCheckType,
    StationStatus,
)
from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId, StationId
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedParameterPayload,
)
from sapphire_flow.types.station import (
    GroupModelAssignment,
    ModelAssignment,
    StationGroup,
)
from tests.fakes.fake_adapters import FakeWeatherForecastSource
from tests.fakes.fake_models import FakeGroupForecastModel
from tests.fakes.fake_stores import (
    FakeAlertStore,
    FakeBasinStore,
    FakeClimBaselineStore,
    FakeForecastStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeObservationStore,
    FakePipelineHealthStore,
    FakeStationGroupStore,
    FakeStationStore,
    FakeWeatherForecastStore,
)
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.unit.flows.test_run_forecast_cycle import (
    _MODEL_ID,
    _NOW,
    _build_station_and_stores,
    _clock,
    _empty_qc_rules,
    _make_config,
    _SmallFakeModel,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.forecast_reference import ForecastOutputKey
    from sapphire_flow.types.ids import ForecastId
    from sapphire_flow.types.model import GroupModelInputs


@contextmanager
def rejected_write_fault(engine: sa.Engine) -> Iterator[None]:
    def reject(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO rejected_forecasts"):
            raise RuntimeError("synthetic rejection body failure")

    event.listen(engine, "after_cursor_execute", reject)
    try:
        yield
    finally:
        event.remove(engine, "after_cursor_execute", reject)


@pytest.fixture
def fault_cleanup() -> Iterator[ExitStack]:
    with ExitStack() as stack:
        yield stack


def test_rejected_write_fault_is_removed_after_assertion_failure() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE rejected_forecasts (id INTEGER)"))
        with (
            pytest.raises(AssertionError, match="downstream assertion"),
            rejected_write_fault(engine),
        ):
            with (
                pytest.raises(RuntimeError, match="synthetic rejection body"),
                engine.begin() as conn,
            ):
                conn.execute(sa.text("INSERT INTO rejected_forecasts VALUES (1)"))
            raise AssertionError("downstream assertion")
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO rejected_forecasts VALUES (2)"))
            assert conn.execute(sa.text("SELECT id FROM rejected_forecasts")).all() == [
                (2,)
            ]
    finally:
        engine.dispose()


class DailyTestModel(FakeGroupForecastModel):
    alert_eligibility = AlertEligibility.SKILL_FORECAST
    output_model_id: ModelId | None = None
    data_requirements = replace(
        FakeGroupForecastModel.data_requirements,
        past_dynamic_features=frozenset(),
        lookback_steps=1,
        forecast_horizon_steps=2,
    )

    def __init__(
        self, mode: Literal["normal", "empty_first", "empty"] = "normal"
    ) -> None:
        self.issues: list[UtcDatetime] = []
        self.mode = mode

    def predict_batch(
        self, artifact: object, inputs: GroupModelInputs, rng: random.Random
    ) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]:
        self.issues.append(inputs.issue_time)
        if self.mode == "empty" or (
            self.mode == "empty_first" and len(self.issues) == 1
        ):
            return {}
        return {
            sid: (
                {
                    p: replace(e, model_id=self.output_model_id)
                    for p, e in ensembles.items()
                },
                state,
            )
            for sid, (ensembles, state) in super()
            .predict_batch(artifact, inputs, rng)
            .items()
        }


class UncertainCommittedWriter(PgCommittedForecastWriter):
    def write_committed(
        self, forecasts: tuple[OperationalForecast, ...]
    ) -> dict[ForecastOutputKey, ForecastId]:
        super().write_committed(forecasts)
        raise StoreError("simulated lost commit acknowledgement")


class TestDailyForecastFlow:
    @pytest.mark.parametrize(
        "case",
        [
            "scheduled",
            "artifact_missing",
            "artifact_inactive",
            "artifact_model",
            "artifact_group",
            "artifact_bytes",
            "artifact_unknown",
            "artifact_store",
            "deserialize_configuration",
            "deserialize_integrity",
            "deserialize_internal",
            "predict_configuration",
            "predict_integrity",
            "predict_internal",
            "native_fi_failure",
            "shared_store",
            "model_integrity",
            "gate_config",
            "carried",
            "carried_unknown",
            "fail_ack",
            "uncertain_finalize",
            "missing_group",
            "retry_read_config",
            "retry_execution_config",
            "rejected_rollback",
            "manual",
            "no_operational",
            "uncertain_commit",
            "missing_plugin",
            "prewrite_failure",
            "preflight_failure",
            "partial_admission",
            "proven_rollback",
            "overlap",
            "retry",
            "exhausted",
        ],
    )
    def test_full_flow_routes_only_claimed_daily_work(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        fault_cleanup: ExitStack,
        case: Literal[
            "scheduled",
            "artifact_missing",
            "artifact_inactive",
            "artifact_model",
            "artifact_group",
            "artifact_bytes",
            "artifact_unknown",
            "artifact_store",
            "deserialize_configuration",
            "deserialize_integrity",
            "deserialize_internal",
            "predict_configuration",
            "predict_integrity",
            "predict_internal",
            "native_fi_failure",
            "shared_store",
            "model_integrity",
            "gate_config",
            "carried",
            "carried_unknown",
            "fail_ack",
            "uncertain_finalize",
            "missing_group",
            "retry_read_config",
            "retry_execution_config",
            "rejected_rollback",
            "manual",
            "no_operational",
            "uncertain_commit",
            "missing_plugin",
            "prewrite_failure",
            "preflight_failure",
            "partial_admission",
            "proven_rollback",
            "overlap",
            "retry",
            "exhausted",
        ],
    ) -> None:
        combination_calls: list[
            tuple[StationId, set[ModelId], list[OperationalForecast]]
        ] = []
        build_combined = forecast_combination.build_combined_forecasts

        def observe_combination(**kwargs: Any) -> list[OperationalForecast]:
            forecasts = build_combined(**kwargs)
            combination_calls.append(
                (kwargs["station_id"], set(kwargs["multi_result"].results), forecasts)
            )
            return forecasts

        monkeypatch.setattr(
            forecast_combination, "build_combined_forecasts", observe_combination
        )
        key, frozen = assignment
        sid = frozen.members[0].station_id
        daily_model_id = ModelId("cmal_small")
        stations, observations = FakeStationStore(), FakeObservationStore()
        weather, artifacts = FakeWeatherForecastStore(), FakeModelArtifactStore()
        forcing, groups = FakeHistoricalForcingStore(), FakeStationGroupStore()
        ordinary, states = FakeForecastStore(), FakeModelStateStore()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stations,
            observations,
            weather,
            artifacts,
            forcing,
            n_obs=80,
        )
        second_model_id = ModelId("second_station_model")
        stations.store_model_assignment(
            ModelAssignment(
                station_id=sid,
                model_id=second_model_id,
                time_step=timedelta(hours=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=2,
                created_at=_NOW,
            )
        )
        artifacts.store_artifact(
            second_model_id,
            b"second",
            _NOW,
            _NOW,
            _NOW,
            station_id=sid,
            status=ModelArtifactStatus.ACTIVE,
        )
        station = stations.fetch_station(sid)
        assert station is not None
        stations.store_station(
            replace(
                station,
                tenant_id=key.tenant_id,
                station_status=StationStatus.SUSPENDED
                if case == "no_operational"
                else StationStatus.OPERATIONAL,
            )
        )
        group = StationGroup(
            id=key.group_id,
            name="daily-flow",
            tenant_id=key.tenant_id,
            station_ids=frozenset({sid}),
            created_at=_NOW,
        )
        groups.store_group(group)
        groups.store_group_model_assignment(
            GroupModelAssignment(
                group_id=group.id,
                model_id=daily_model_id,
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=2,
                created_at=_NOW,
            )
        )
        artifact_id = artifacts.store_artifact(
            daily_model_id,
            b"daily",
            _NOW,
            _NOW,
            _NOW,
            group_id=group.id,
            status=ModelArtifactStatus.ACTIVE,
        )[0]
        with isolated_committed_db_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO models "
                    "(id, display_name, artifact_scope, description) "
                    "VALUES ('cmal_small', 'Daily synthetic', 'group', 'test') "
                    "ON CONFLICT (id) DO NOTHING"
                )
            )
            conn.execute(
                sa.insert(model_artifacts).values(
                    id=artifact_id,
                    model_id=daily_model_id,
                    group_id=group.id,
                    status="active",
                    artifact_path="fixture",
                    sha256_hash="c" * 64,
                    training_period_start=_NOW,
                    training_period_end=_NOW,
                    trained_at=_NOW,
                )
            )
        deployment_id = uuid4()
        config = _make_config().model_copy(
            update={
                "enable_forecast_alerts": True,
                "forecast_combination_strategy": ModelCombinationStrategy.POOLED,
                "daily_evaluation": DailyEvaluationConfig(
                    tenant_id=key.tenant_id,
                    deployment="test",
                    prefect_deployment_id=deployment_id,
                    group_ids=(group.id,),
                    output_contracts=(
                        DailyOutputContractConfig(
                            parameter="discharge",
                            units="m³/s",
                            representation=EnsembleRepresentation.MEMBERS,
                            first_lead_hours=24,
                            horizon_steps=2,
                            series_keys=tuple(float(i) for i in range(21)),
                        ),
                    ),
                ),
            }
        )
        if case == "missing_group":
            config = config.model_copy(
                update={
                    "daily_evaluation": config.daily_evaluation.model_copy(
                        update={"group_ids": (group.id, UUID(int=(1 << 128) - 1))}
                    )
                }
            )
        scheduled = ScheduledDailyRun(
            owner_id=uuid4(), context=ScheduledDailyForecast(scheduled_tick=_NOW)
        )
        monkeypatch.setattr(
            execution,
            "current_scheduled_daily_run",
            lambda **_: None if case == "manual" else scheduled,
        )
        model = DailyTestModel(
            "empty_first"
            if case in ("retry", "retry_read_config", "retry_execution_config")
            else "empty"
            if case == "exhausted"
            else "normal"
        )
        runner_faults = {
            "configuration": ConfigurationError,
            "integrity": ModelOutputError,
            "internal": RuntimeError,
        }
        if case.startswith(("deserialize_", "predict_")):
            stage, kind = case.split("_", 1)

            def raise_model_fault(*args: object, **kwargs: object) -> None:
                raise runner_faults[kind]("synthetic typed model fault")

            monkeypatch.setattr(
                model,
                "deserialize_artifact" if stage == "deserialize" else "predict_batch",
                raise_model_fault,
            )
        if case.startswith("artifact_"):
            fetch_record = artifacts.fetch_artifact_record
            fetch_bytes = artifacts.fetch_artifact
            record_reads = 0
            status_reads = 0
            fetch_status = artifacts.fetch_artifacts_by_status

            def tracking_status(
                model_id: ModelId,
                status: ModelArtifactStatus,
                *,
                station_id: StationId | None = None,
                group_id: StationGroupId | None = None,
            ) -> list[ArtifactId]:
                nonlocal status_reads
                if model_id == daily_model_id and group_id == group.id:
                    status_reads += 1
                return fetch_status(
                    model_id, status, station_id=station_id, group_id=group_id
                )

            monkeypatch.setattr(artifacts, "fetch_artifacts_by_status", tracking_status)

            def racing_record(lookup_id: ArtifactId) -> object:
                nonlocal record_reads
                record = fetch_record(lookup_id)
                if lookup_id != artifact_id:
                    return record
                record_reads += 1
                # Preparation and controller recheck read active IDs before the runner.
                assert status_reads == 2
                if case == "artifact_unknown":
                    raise RuntimeError("synthetic artifact lookup fault")
                if case == "artifact_store":
                    raise StoreError("synthetic artifact store fault")
                if case == "artifact_missing":
                    return None
                assert record is not None
                if case == "artifact_inactive":
                    return replace(record, status=ModelArtifactStatus.SUPERSEDED)
                if case == "artifact_model":
                    return replace(record, model_id=ModelId("different"))
                if case == "artifact_group":
                    return replace(record, group_id=StationGroupId(uuid4()))
                return record

            def missing_bytes(lookup_id: ArtifactId) -> object:
                return None if lookup_id == artifact_id else fetch_bytes(lookup_id)

            monkeypatch.setattr(artifacts, "fetch_artifact_record", racing_record)
            if case == "artifact_bytes":
                monkeypatch.setattr(artifacts, "fetch_artifact", missing_bytes)
        if case == "native_fi_failure":
            from sapphire_flow.adapters import forecast_interface as fi
            from tests.unit.adapters.test_forecast_interface_adapter_nan_gate import (
                RecordingFIForecastModel,
            )
            from tests.unit.flows.test_run_forecast_cycle import _make_nwp_records

            offset = _NOW - scheduled.context.issue_time
            weather.store_weather_forecasts(
                [
                    replace(
                        record,
                        cycle_time=scheduled.context.issue_time,
                        valid_time=record.valid_time - offset - timedelta(hours=1),
                    )
                    for record in _make_nwp_records(sid)
                ]
            )
            raw = RecordingFIForecastModel(
                fi.ModelFailure(
                    model_name="synthetic",
                    issue_datetime=scheduled.context.issue_time,
                    cause=fi.FailureCause.INPUT_DATA,
                    message="anticipated shortfall",
                ),
                artifact_scope=fi.FIArtifactScope.GROUP,
                requirement=fi.InputRequirement(
                    targets={
                        "discharge": fi.TargetSpec(
                            unit=fi.Unit.M3_PER_S,
                            representations=frozenset(
                                {fi.OutputRepresentation.TRAJECTORIES}
                            ),
                        )
                    },
                    dynamic={
                        timedelta(days=1): fi.SpatialInputSpec(
                            data={
                                fi.FISpatialRepresentation.POINT: fi.DynamicInputSpec(
                                    future_known={
                                        "nwp": {
                                            "temperature": fi.FutureKnownVariable(
                                                unit=fi.Unit.DEG_C,
                                                future_steps=2,
                                                max_nan=0,
                                            )
                                        }
                                    }
                                )
                            }
                        )
                    },
                ),
            )
            model = fi.ForecastInterfaceAdapter(
                raw, station_code_resolver=lambda _: "gauge"
            )
        if case == "overlap":
            overlapping = replace(group, id=StationGroupId(uuid4()), name="overlap")
            groups.store_group(overlapping)
            groups.store_group_model_assignment(
                GroupModelAssignment(
                    group_id=overlapping.id,
                    model_id=daily_model_id,
                    time_step=timedelta(days=1),
                    status=ModelAssignmentStatus.ACTIVE,
                    priority=2,
                    created_at=_NOW,
                )
            )
            artifacts.store_artifact(
                daily_model_id,
                b"overlap",
                _NOW,
                _NOW,
                _NOW,
                group_id=overlapping.id,
                status=ModelArtifactStatus.ACTIVE,
            )
        ordinary_model_id = ModelId("ordinary_group")
        ordinary_model = DailyTestModel()
        ordinary_model.output_model_id = ordinary_model_id
        groups.store_group_model_assignment(
            GroupModelAssignment(
                group_id=group.id,
                model_id=ordinary_model_id,
                time_step=timedelta(days=1),
                status=ModelAssignmentStatus.ACTIVE,
                priority=3,
                created_at=_NOW,
            )
        )
        artifacts.store_artifact(
            ordinary_model_id,
            b"ordinary",
            _NOW,
            _NOW,
            _NOW,
            group_id=group.id,
            status=ModelArtifactStatus.ACTIVE,
        )
        health = FakePipelineHealthStore()
        ledger = PgDailyForecastStore(isolated_committed_db_engine.begin)
        controllers: list[execution.DailyForecastExecution] = []
        original_claim_scheduled = execution.DailyForecastExecution.claim_scheduled

        def track_controller(controller: execution.DailyForecastExecution) -> None:
            controllers.append(controller)
            original_claim_scheduled(controller)

        monkeypatch.setattr(
            execution.DailyForecastExecution, "claim_scheduled", track_controller
        )
        fail_calls: list[UUID] = []
        if case == "uncertain_finalize":

            def fail_finalize(*args: object, **kwargs: object) -> None:
                raise StoreError("secret finalization acknowledgement payload")

            monkeypatch.setattr(ledger, "finalize", fail_finalize)
        if case == "fail_ack":

            def fail_without_ack(claim: object, **kwargs: object) -> None:
                fail_calls.append(claim.id)
                raise StoreError("secret failure acknowledgement payload")

            monkeypatch.setattr(ledger, "fail", fail_without_ack)
        original_run = execution.run_group_forecast
        if case in (
            "shared_store",
            "model_integrity",
            "gate_config",
            "carried",
            "carried_unknown",
            "fail_ack",
            "rejected_rollback",
        ):

            def failing_run(**kwargs: object) -> GroupForecastOutcome:
                if case in ("shared_store", "fail_ack"):
                    raise StoreError("secret shared failure payload")
                if case == "model_integrity":
                    raise ModelOutputError("secret malformed model payload")
                if case == "gate_config":
                    return GroupForecastOutcome(
                        results={},
                        failure=ConfigurationError("secret gate configuration"),
                    )
                outcome = original_run(**kwargs)
                station_result = next(iter(outcome.results.values()))
                payload = RejectedAssignmentPayload(
                    station_id=station_result.station_id,
                    model_id=station_result.model_id,
                    model_artifact_id=station_result.artifact_id,
                    issued_at=kwargs["group_inputs"].issue_time,
                    group_id=kwargs["group"].id,
                    parameters=tuple(
                        RejectedParameterPayload(
                            ensemble=f.ensemble,
                            qc_status=f.qc_status,
                            qc_flags=f.qc_flags,
                        )
                        for f in station_result.forecasts
                    ),
                )
                if case == "rejected_rollback":
                    return GroupForecastOutcome(results={}, rejected=(payload,))
                raise GroupForecastError(
                    "secret builder payload",
                    rejected=(payload,),
                    original=RuntimeError("secret original payload"),
                )

            monkeypatch.setattr(execution, "run_group_forecast", failing_run)
        with isolated_committed_db_engine.connect() as conn:
            kwargs = dict(
                pipeline_health_store=health,
                station_store=stations,
                obs_store=observations,
                weather_forecast_store=weather,
                forecast_store=ordinary,
                model_state_store=states,
                artifact_store=artifacts,
                alert_store=FakeAlertStore(),
                baseline_store=FakeClimBaselineStore(),
                basin_store=FakeBasinStore(),
                group_store=groups,
                forcing_store=forcing,
                adapter=FakeWeatherForecastSource(result={}),
                models={
                    _MODEL_ID: _SmallFakeModel(),
                    second_model_id: _SmallFakeModel(),
                    daily_model_id: model,
                    ordinary_model_id: ordinary_model,
                },
                config=config,
                qc_rules=_empty_qc_rules(),
                clock=_clock,
                rng=random.Random(42),
                daily_forecast_store=ledger,
                daily_forecast_writer=PgCommittedForecastWriter(
                    isolated_committed_db_engine.begin
                ),
                rejected_forecast_store=PgRejectedForecastStore(
                    conn, transaction_factory=isolated_committed_db_engine.begin
                ),
            )
            rejected_writer = kwargs["rejected_forecast_store"]
            real_rejected_write = rejected_writer.write_committed
            if case == "carried_unknown":

                def lose_rejected_ack(entry: object) -> object:
                    real_rejected_write(entry)
                    raise StoreError("secret rejected commit acknowledgement")

                monkeypatch.setattr(
                    rejected_writer, "write_committed", lose_rejected_ack
                )
            if case == "rejected_rollback":
                fault_cleanup.enter_context(
                    rejected_write_fault(isolated_committed_db_engine)
                )
            if case == "missing_plugin":
                kwargs["models"].pop(daily_model_id)
            if case == "preflight_failure":

                def fail_preflight(**kwargs: object) -> None:
                    raise StoreError("preflight unavailable")

                monkeypatch.setattr(
                    execution, "prepare_daily_candidates", fail_preflight
                )
            if case == "partial_admission":
                prepare = execution.prepare_daily_candidates

                def partial_candidates(
                    **kwargs: object,
                ) -> dict[DailyClaimKey, FrozenDailyAssignment]:
                    candidates = prepare(**kwargs)
                    first_key = next(iter(candidates))
                    second_key = replace(
                        first_key, group_id=StationGroupId(UUID(int=(1 << 128) - 1))
                    )
                    return {**candidates, second_key: candidates[first_key]}

                monkeypatch.setattr(
                    execution, "prepare_daily_candidates", partial_candidates
                )
            original_validate = execution.validate_daily_group
            original_store = PgForecastStore.store_forecast
            if case == "proven_rollback":

                def fail_after_insert(
                    self: PgForecastStore, forecast: OperationalForecast
                ) -> None:
                    original_store(self, forecast)
                    raise ValueError("body failed after insert")

                monkeypatch.setattr(
                    PgForecastStore, "store_forecast", fail_after_insert
                )
            if case == "prewrite_failure":

                def fail_validation(**kwargs: object) -> None:
                    raise ValueError("invalid pure output")

                monkeypatch.setattr(execution, "validate_daily_group", fail_validation)
            if case == "uncertain_commit":
                kwargs["daily_forecast_writer"] = UncertainCommittedWriter(
                    isolated_committed_db_engine.begin
                )
                result = run_forecast_cycle_flow(**kwargs)
                assert result.alerts_checked
                assert any("Daily group" in error for error in result.errors)
                assert ordinary_model.issues == [_NOW]
                assert combination_calls == [(sid, {_MODEL_ID, second_model_id}, [])]
                assert (
                    ordinary.fetch_latest_forecast(sid, model_id=ordinary_model_id)
                    is not None
                )
                assert health.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS)
                scheduled = replace(
                    scheduled,
                    owner_id=uuid4(),
                    context=ScheduledDailyForecast(
                        scheduled_tick=_NOW + timedelta(hours=6)
                    ),
                )
                kwargs["rng"] = random.Random(42)
                run_forecast_cycle_flow(**kwargs)
            else:
                result = run_forecast_cycle_flow(**kwargs)
                if case in (
                    "missing_plugin",
                    "shared_store",
                    "model_integrity",
                    "gate_config",
                    "carried",
                    "carried_unknown",
                    "fail_ack",
                    "uncertain_finalize",
                    "missing_group",
                    "rejected_rollback",
                    "preflight_failure",
                    "prewrite_failure",
                    "partial_admission",
                    "proven_rollback",
                ):
                    assert result.alerts_checked
                    assert ordinary_model.issues == [_NOW]
                    assert result.errors
                    assert combination_calls == [
                        (sid, {_MODEL_ID, second_model_id}, [])
                    ]
                    assert (
                        ordinary.fetch_latest_forecast(sid, model_id=ordinary_model_id)
                        is not None
                    )
                    assert health.fetch_recent(PipelineCheckType.FORECAST_FRESHNESS)
            if case in ("prewrite_failure", "proven_rollback", "rejected_rollback"):
                first = ledger.read_day(
                    replace(key, model_id=daily_model_id, context=scheduled.context)
                )
                assert first.latest is not None
                assert first.latest.failure_reason is (
                    DailyFailureReason.INTEGRITY_FAILURE
                    if case == "prewrite_failure"
                    else DailyFailureReason.PERSISTENCE_FAILURE
                )
                if case == "rejected_rollback":
                    fault_cleanup.close()
                    monkeypatch.setattr(execution, "run_group_forecast", original_run)
                monkeypatch.setattr(
                    execution, "validate_daily_group", original_validate
                )
                monkeypatch.setattr(PgForecastStore, "store_forecast", original_store)
                scheduled = replace(
                    scheduled,
                    owner_id=uuid4(),
                    context=ScheduledDailyForecast(
                        scheduled_tick=_NOW + timedelta(hours=6)
                    ),
                )
                kwargs["clock"] = lambda: scheduled.context.scheduled_tick
                kwargs["rng"] = random.Random(42)
                run_forecast_cycle_flow(**kwargs)
            if case == "scheduled":
                kwargs["rng"] = random.Random(42)
                run_forecast_cycle_flow(**kwargs)
            if case in (
                "retry",
                "exhausted",
                "retry_read_config",
                "retry_execution_config",
            ):
                if case == "retry_read_config":
                    kwargs["config"] = config.model_copy(
                        update={
                            "evaluation_forecasts": EvaluationForecastsConfig(
                                tenant_id=key.tenant_id,
                                deployment="test",
                                group_ids=(group.id,),
                            )
                        }
                    )
                if case == "retry_execution_config":
                    kwargs["config"] = config.model_copy(
                        update={
                            "nwp_max_fallback_age_hours": (
                                config.nwp_max_fallback_age_hours + 1
                            )
                        }
                    )
                scheduled = replace(
                    scheduled,
                    owner_id=uuid4(),
                    context=ScheduledDailyForecast(
                        scheduled_tick=_NOW + timedelta(hours=6)
                    ),
                )
                kwargs["clock"] = lambda: scheduled.context.scheduled_tick
                kwargs["rng"] = random.Random(42)
                run_forecast_cycle_flow(**kwargs)
                if case == "exhausted":
                    scheduled = replace(
                        scheduled,
                        owner_id=uuid4(),
                        context=ScheduledDailyForecast(
                            scheduled_tick=_NOW + timedelta(hours=12)
                        ),
                    )
                    kwargs["rng"] = random.Random(42)
                    run_forecast_cycle_flow(**kwargs)
        read_key = replace(key, model_id=daily_model_id, context=scheduled.context)
        day = ledger.read_day(read_key)
        if case in ("missing_plugin", "preflight_failure"):
            assert day.latest is None
            assert model.issues == []
        elif case in ("prewrite_failure", "proven_rollback", "rejected_rollback"):
            assert day.authoritative is not None
            assert day.authoritative.claim.ordinal.value == 2
            assert day.authoritative.state is DailyAttemptState.COMPLETE
        elif case in ("partial_admission", "missing_group"):
            assert model.issues == [scheduled.context.issue_time]
            assert day.authoritative is not None
            assert day.authoritative.state is DailyAttemptState.COMPLETE
            assert day.authoritative.claim.ordinal.value == 1
            assert (
                "preparation" in caplog.text
                if case == "missing_group"
                else "admission_refused" in caplog.text
            )
        elif case.startswith("artifact_"):
            assert record_reads == 1
            assert status_reads == 2
            assert isinstance(model, DailyTestModel)
            assert model.issues == []
            assert day.latest is not None
            assert day.latest.state is DailyAttemptState.FAILED
            assert day.latest.failure_reason is {
                "artifact_bytes": DailyFailureReason.INTEGRITY_FAILURE,
                "artifact_unknown": DailyFailureReason.INTERNAL_FAILURE,
                "artifact_store": DailyFailureReason.PERSISTENCE_FAILURE,
            }.get(case, DailyFailureReason.CONFIGURATION_UNAVAILABLE)
            assert day.latest.outcomes is None
            assert day.authoritative is None
        elif case.startswith(("deserialize_", "predict_")):
            assert day.latest is not None
            assert day.latest.state is DailyAttemptState.FAILED
            assert (
                day.latest.failure_reason
                is {
                    "configuration": DailyFailureReason.CONFIGURATION_UNAVAILABLE,
                    "integrity": DailyFailureReason.INTEGRITY_FAILURE,
                    "internal": DailyFailureReason.INTERNAL_FAILURE,
                }[case.split("_", 1)[1]]
            )
            assert day.latest.outcomes is None
            assert day.authoritative is None
        elif case == "native_fi_failure":
            assert raw.predict_inputs is not None
            assert day.authoritative is not None
            assert day.authoritative.state is DailyAttemptState.INCOMPLETE
            assert day.authoritative.outcomes is not None
            assert all(
                member.state.value == "model_failed"
                and member.reason.value == "model_failure"
                for member in day.authoritative.outcomes
            )
        elif case == "manual":
            assert day.latest is None
            assert model.issues == []
        elif case in (
            "shared_store",
            "model_integrity",
            "gate_config",
            "carried",
            "carried_unknown",
            "fail_ack",
            "uncertain_finalize",
        ):
            assert day.latest is not None
            expected = {
                "shared_store": DailyFailureReason.PERSISTENCE_FAILURE,
                "model_integrity": DailyFailureReason.INTEGRITY_FAILURE,
                "gate_config": DailyFailureReason.CONFIGURATION_UNAVAILABLE,
                "carried": DailyFailureReason.INTERNAL_FAILURE,
            }
            assert day.latest.state is (
                DailyAttemptState.ACTIVE
                if case in ("carried_unknown", "fail_ack", "uncertain_finalize")
                else DailyAttemptState.FAILED
            )
            assert day.latest.failure_reason is expected.get(case)
            assert day.latest.outcomes is None
            assert all(
                m.state.value == "pending" and not m.references
                for m in day.latest.claim.frozen.members
            )
            assert "secret" not in caplog.text
            assert str(day.latest.claim.id) in caplog.text
            if case == "fail_ack":
                assert fail_calls == [day.latest.claim.id]
            if case in ("fail_ack", "carried_unknown", "uncertain_finalize"):
                controller = controllers[0]
                assert day.latest.claim.id in controller.pending
                controller.fail_unstarted(DailyFailureReason.PERSISTENCE_FAILURE)
                assert day.latest.claim.id in controller.pending
                if case == "fail_ack":
                    assert fail_calls == [day.latest.claim.id]
            with isolated_committed_db_engine.connect() as check:
                assert check.execute(
                    sa.text(
                        "SELECT count(*) FROM rejected_forecasts WHERE attempt_id = :id"
                    ),
                    {"id": day.latest.claim.id},
                ).scalar_one() == (1 if case in ("carried", "carried_unknown") else 0)
                assert check.execute(
                    sa.text(
                        "SELECT count(*) FROM forecasts WHERE model_id = 'cmal_small' "
                        "AND station_id = :sid"
                    ),
                    {"sid": sid},
                ).scalar_one() == (1 if case == "uncertain_finalize" else 0)
        elif case == "retry_execution_config":
            assert day.latest is not None
            assert day.latest.claim.ordinal.value == 2
            assert day.latest.failure_reason is DailyFailureReason.CONFIGURATION_CHANGED
            assert model.issues == [scheduled.context.issue_time]
        elif case == "uncertain_commit":
            assert day.latest is not None
            assert day.latest.state is DailyAttemptState.ACTIVE
            assert day.authoritative is None
            assert model.issues == [scheduled.context.issue_time]
            with isolated_committed_db_engine.connect() as conn:
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT count(*) FROM forecasts WHERE station_id = :sid "
                            "AND model_id = 'cmal_small'"
                        ),
                        {"sid": sid},
                    ).scalar_one()
                    == 1
                )
        elif case in ("retry", "exhausted", "retry_read_config"):
            assert day.authoritative is not None
            assert day.authoritative.claim.ordinal.value == 2
            assert day.authoritative.state is (
                DailyAttemptState.COMPLETE
                if case in ("retry", "retry_read_config")
                else DailyAttemptState.INCOMPLETE
            )
            assert model.issues == [
                scheduled.context.issue_time,
                scheduled.context.issue_time,
            ]
            with isolated_committed_db_engine.connect() as conn:
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT count(*) FROM daily_forecast_attempts "
                            "WHERE group_id = :gid"
                        ),
                        {"gid": group.id},
                    ).scalar_one()
                    == 2
                )
        elif case == "no_operational":
            assert day.latest is not None
            assert (
                day.latest.failure_reason is DailyFailureReason.NO_OPERATIONAL_STATIONS
            )
            assert model.issues == []
        else:
            assert day.authoritative is not None
            assert day.authoritative.state is DailyAttemptState.COMPLETE
            assert model.issues == [scheduled.context.issue_time]
            assert states.fetch_latest_state(sid, daily_model_id) is None
            regular = ordinary.fetch_latest_forecast(sid, model_id=_MODEL_ID)
            assert regular is not None and regular.issued_at == _NOW
            assert ordinary.fetch_latest_forecast(sid, model_id=daily_model_id) is None


@pytest.mark.parametrize("fault_stage", ["group", "artifacts"])
def test_scheduled_preparation_store_fault_preserves_retry_and_other_group(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    monkeypatch: pytest.MonkeyPatch,
    fault_stage: str,
) -> None:
    from sapphire_flow.db.metadata import station_groups
    from sapphire_flow.store.station_store import PgStationStore
    from tests.conftest import make_station_config

    key, frozen = assignment
    groups, artifacts = FakeStationGroupStore(), FakeModelArtifactStore()
    healthy_id = StationGroupId(uuid4())
    healthy_station_id = StationId(uuid4())
    model_id = ModelId("cmal_small")
    group_ids = (key.group_id, healthy_id)
    with isolated_committed_db_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO models (id, display_name, artifact_scope, description) "
                "VALUES ('cmal_small', 'Daily synthetic', 'group', 'test') "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        conn.execute(
            sa.insert(station_groups).values(
                id=healthy_id, tenant_id=key.tenant_id, name=str(healthy_id)
            )
        )
        PgStationStore(conn).store_station(
            make_station_config(
                station_id=healthy_station_id,
                code=f"daily-{healthy_station_id}",
                tenant_id=key.tenant_id,
            )
        )
        for gid in group_ids:
            groups.store_group(
                StationGroup(
                    id=gid,
                    name=str(gid),
                    tenant_id=key.tenant_id,
                    station_ids=frozenset(
                        {
                            frozen.members[0].station_id
                            if gid == key.group_id
                            else healthy_station_id
                        }
                    ),
                    created_at=_NOW,
                )
            )
            groups.store_group_model_assignment(
                GroupModelAssignment(
                    group_id=gid,
                    model_id=model_id,
                    time_step=timedelta(days=1),
                    status=ModelAssignmentStatus.ACTIVE,
                    priority=2,
                    created_at=_NOW,
                )
            )
            artifact_id = artifacts.store_artifact(
                model_id,
                b"synthetic",
                _NOW,
                _NOW,
                _NOW,
                group_id=gid,
                status=ModelArtifactStatus.ACTIVE,
            )[0]
            conn.execute(
                sa.insert(model_artifacts).values(
                    id=artifact_id,
                    model_id=model_id,
                    group_id=gid,
                    status="active",
                    artifact_path="fixture",
                    sha256_hash="c" * 64,
                    training_period_start=_NOW,
                    training_period_end=_NOW,
                    trained_at=_NOW,
                )
            )
    daily = DailyEvaluationConfig(
        tenant_id=key.tenant_id,
        deployment="test",
        prefect_deployment_id=uuid4(),
        group_ids=(key.group_id,),
        output_contracts=(
            DailyOutputContractConfig(
                parameter="discharge",
                units="m³/s",
                representation=EnsembleRepresentation.MEMBERS,
                first_lead_hours=24,
                horizon_steps=2,
                series_keys=tuple(float(i) for i in range(21)),
            ),
        ),
    )
    config = _make_config().model_copy(update={"daily_evaluation": daily})
    scheduled = ScheduledDailyRun(
        owner_id=uuid4(), context=ScheduledDailyForecast(scheduled_tick=_NOW)
    )

    def current_run(**_: object) -> ScheduledDailyRun:
        return scheduled

    monkeypatch.setattr(execution, "current_scheduled_daily_run", current_run)
    ledger = PgDailyForecastStore(isolated_committed_db_engine.begin)
    with isolated_committed_db_engine.connect() as conn:

        def controller() -> execution.DailyForecastExecution:
            return execution.DailyForecastExecution(
                config=config,
                settings=daily.to_domain(),
                model=DailyTestModel(),
                group_store=groups,
                artifact_store=artifacts,
                store=ledger,
                writer=PgCommittedForecastWriter(isolated_committed_db_engine.begin),
                rejected_writer=PgRejectedForecastStore(
                    conn, transaction_factory=isolated_committed_db_engine.begin
                ),
                clock=lambda: scheduled.context.scheduled_tick,
                id_gen=uuid4,
            )

        initial = controller()
        initial.claim_scheduled()
        first = next(iter(initial.pending.values()))
        assert isinstance(first, DailyClaim)
        initial.fail_claim(first, DailyFailureReason.SHARED_INPUT_FAILURE)
        daily = daily.model_copy(update={"group_ids": group_ids})
        config = config.model_copy(update={"daily_evaluation": daily})
        scheduled = replace(
            scheduled,
            owner_id=uuid4(),
            context=ScheduledDailyForecast(scheduled_tick=_NOW + timedelta(hours=6)),
        )
        fetch_group = groups.fetch_group
        fetch_artifacts = artifacts.fetch_artifacts_by_status

        def unavailable_group(gid: StationGroupId) -> StationGroup | None:
            if gid == key.group_id:
                raise StoreError("synthetic preparation read failure")
            return fetch_group(gid)

        def unavailable_artifacts(
            model_id: ModelId,
            status: ModelArtifactStatus,
            *,
            station_id: StationId | None = None,
            group_id: StationGroupId | None = None,
        ) -> list[ArtifactId]:
            if group_id == key.group_id:
                raise StoreError("synthetic preparation read failure")
            return fetch_artifacts(
                model_id, status, station_id=station_id, group_id=group_id
            )

        with monkeypatch.context() as patch:
            if fault_stage == "group":
                patch.setattr(groups, "fetch_group", unavailable_group)
            else:
                patch.setattr(
                    artifacts, "fetch_artifacts_by_status", unavailable_artifacts
                )
            unavailable = controller()
            unavailable.claim_scheduled()
        assert unavailable.errors
        assert {claim.key.group_id for claim in unavailable.pending.values()} == {
            healthy_id
        }
        healthy = next(iter(unavailable.pending.values()))
        assert isinstance(healthy, DailyClaim)
        assert healthy.ordinal.value == 1
        day = ledger.read_day(first.key)
        assert day.latest is not None
        assert day.latest.claim.id == first.id
        assert day.latest.state is DailyAttemptState.FAILED
        assert day.latest.failure_reason is DailyFailureReason.SHARED_INPUT_FAILURE
        scheduled = replace(
            scheduled,
            owner_id=uuid4(),
            context=ScheduledDailyForecast(scheduled_tick=_NOW + timedelta(hours=12)),
        )
        available = controller()
        available.claim_scheduled()
        assert not available.errors
        assert {claim.key.group_id for claim in available.pending.values()} == {
            key.group_id
        }
        retry = next(iter(available.pending.values()))
        assert isinstance(retry, DailyClaim)
        assert retry.ordinal.value == 2
        assert retry.frozen == first.frozen
        healthy_day = ledger.read_day(healthy.key)
        assert healthy_day.latest is not None
        assert healthy_day.latest.claim.id == healthy.id
        assert healthy_day.latest.state is DailyAttemptState.ACTIVE
