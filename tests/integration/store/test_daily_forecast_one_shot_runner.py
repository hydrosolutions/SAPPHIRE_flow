from __future__ import annotations

# Shared synthetic test builders are intentionally reused.
# pyright: reportPrivateUsage=false
import random
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.config.daily_evaluation import (
    DailyEvaluationConfig,
    DailyOutputContractConfig,
)
from sapphire_flow.db.metadata import forecasts, model_artifacts
from sapphire_flow.db.metadata import stations as station_table
from sapphire_flow.exceptions import (
    GroupForecastError,
    ModelPredictionFailure,
    StoreError,
)
from sapphire_flow.flows import daily_forecast_execution as execution
from sapphire_flow.store.committed_forecast_writer import PgCommittedForecastWriter
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    InputQualityLevel,
    ModelArtifactStatus,
    ModelAssignmentStatus,
    StationStatus,
)
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedParameterPayload,
)
from sapphire_flow.types.station import GroupModelAssignment, StationGroup
from tests.fakes.fake_stores import (
    FakeBasinStore,
    FakeHistoricalForcingStore,
    FakeModelArtifactStore,
    FakeModelStateStore,
    FakeObservationStore,
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
    _empty_qc_rules,
    _make_config,
)

if TYPE_CHECKING:
    from sapphire_flow.services.run_group_forecast import GroupForecastOutcome
    from sapphire_flow.types.daily_forecast_claim import (
        DailyClaimKey,
        FrozenDailyAssignment,
    )
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.forecast_reference import ForecastOutputKey
    from sapphire_flow.types.ids import ForecastId
    from sapphire_flow.types.model import GroupModelInputs


from sapphire_flow.flows.daily_forecast_one_shot import (
    OneShotGroupInputs,
    run_local_one_shot_group,
)
from sapphire_flow.flows.daily_forecast_one_shot_runtime import (
    LocalOneShotOutputs,
    create_local_one_shot_outputs,
)
from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
from sapphire_flow.types.daily_forecast import (
    OneShotDailyForecast,
    OneShotForecastTrigger,
)
from sapphire_flow.types.daily_forecast_one_shot import OneShotDailyKey
from sapphire_flow.types.daily_forecast_outcome import DailyMemberState
from sapphire_flow.types.enums import NwpCycleSource
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.integration.store.test_daily_forecast_flow import DailyTestModel
from tests.integration.store.test_daily_forecast_one_shot_runtime import (
    stage_synthetic_assignment,
)
from tests.unit.services.test_daily_weather_completeness import _complete_records
from tests.unit.services.test_operational_inputs import _ISSUE


class HistoricalWeatherTestModel(DailyTestModel):
    data_requirements = replace(
        DailyTestModel.data_requirements,
        future_dynamic_features=frozenset({"precipitation", "temperature"}),
    )

    def predict_batch(
        self, artifact: object, inputs: GroupModelInputs, rng: random.Random
    ) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]:
        assert artifact == b"daily"
        assert inputs.future_dynamic["precipitation"].to_list() == [24.0, 24.0]
        assert inputs.future_dynamic["temperature"].to_list() == [1.0, 1.0]
        assert {
            row.cycle_time
            for _, evidence in inputs.source_evidence
            for row in evidence.future_weather
        } == {inputs.issue_time}
        return super().predict_batch(artifact, inputs, rng)


@pytest.mark.parametrize("trigger", list(OneShotForecastTrigger))
@pytest.mark.parametrize(
    "case",
    [
        "normal",
        "historical_weather",
        "mixed",
        "excluded",
        "missing_artifact",
        "missing_artifact_fail_ack",
        "assembly_failure",
        "assignment_race",
        "model_failure",
        "anticipated_failure",
        "poststate",
        "uncertain_commit",
        "uncertain_summary",
        "carried_validation",
        "carried_rollback",
        "carried_uncertain",
    ],
)
def test_one_shot_runner(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    monkeypatch: pytest.MonkeyPatch,
    trigger: OneShotForecastTrigger,
    case: str,
) -> None:
    outputs = create_local_one_shot_outputs()
    try:
        stage_synthetic_assignment(
            isolated_committed_db_engine, outputs.engine, *assignment
        )
        key, frozen = assignment
        sid = frozen.members[0].station_id
        daily_model_id = ModelId("cmal_small")
        stations, observations = FakeStationStore(), FakeObservationStore()
        weather, artifacts = FakeWeatherForecastStore(), FakeModelArtifactStore()
        forcing, groups = FakeHistoricalForcingStore(), FakeStationGroupStore()
        states = FakeModelStateStore()
        _build_station_and_stores(
            sid,
            _MODEL_ID,
            stations,
            observations,
            weather,
            artifacts,
            forcing,
            n_obs=80,
            seed_nwp=case != "historical_weather",
        )
        station = stations.fetch_station(sid)
        assert station is not None
        stations.store_station(
            replace(
                station,
                tenant_id=key.tenant_id,
                station_status=StationStatus.SUSPENDED
                if case == "excluded"
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
        with outputs.engine.begin() as conn:
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
                )
            }
        )
        if case == "mixed":
            excluded_id = StationId(uuid4())
            with outputs.engine.begin() as conn:
                row = dict(
                    conn.execute(
                        sa.select(station_table).where(station_table.c.id == sid)
                    )
                    .mappings()
                    .one()
                )
                row.update(id=excluded_id, code=str(excluded_id))
                conn.execute(sa.insert(station_table).values(**row))
            group = replace(group, station_ids=frozenset({sid, excluded_id}))
            groups.store_group(group)
        model = (
            HistoricalWeatherTestModel()
            if case == "historical_weather"
            else DailyTestModel()
        )
        context = OneShotDailyForecast(trigger=trigger, target_date=_NOW.date())
        actual_now = _NOW + timedelta(days=3) if case == "historical_weather" else _NOW
        if case == "historical_weather":
            weather.store_weather_forecasts(
                [
                    replace(
                        row,
                        station_id=sid,
                        cycle_time=context.issue_time,
                        valid_time=context.issue_time + (row.valid_time - _ISSUE),
                    )
                    for row in _complete_records()
                ]
            )
        execution_id = uuid4()
        key = OneShotDailyKey(
            tenant_id=key.tenant_id,
            deployment="test",
            group_id=group.id,
            model_id=daily_model_id,
            context=context,
        )

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("scheduled admission must not run")

        monkeypatch.setattr(execution, "current_scheduled_daily_run", forbidden)
        monkeypatch.setattr(execution, "claim_daily_candidates", forbidden)
        monkeypatch.setattr(execution, "assemble_scheduled_daily_weather", forbidden)
        monkeypatch.setattr(PgDailyForecastStore, "pending_keys", forbidden)
        monkeypatch.setattr(PgDailyForecastStore, "claim", forbidden)
        if case == "historical_weather":
            monkeypatch.setattr(weather, "fetch_received_cycles", forbidden)
        if case in ("missing_artifact", "missing_artifact_fail_ack"):
            artifacts = FakeModelArtifactStore()
        failure_calls: list[object] = []
        if case == "missing_artifact_fail_ack":

            def lose_failure_ack(*args: object, **kwargs: object) -> None:
                failure_calls.append(args)
                raise StoreError("synthetic lost commit acknowledgement")

            monkeypatch.setattr(PgDailyForecastStore, "fail", lose_failure_ack)
        if case == "assignment_race":
            read_assignments = groups.fetch_group_model_assignments
            assignment_reads: list[object] = []

            def vanish_assignment(group_id: object) -> list[GroupModelAssignment]:
                assignment_reads.append(group_id)
                return [] if len(assignment_reads) == 3 else read_assignments(group_id)

            monkeypatch.setattr(
                groups, "fetch_group_model_assignments", vanish_assignment
            )
        if case == "assembly_failure":

            def fail_assembly(**kwargs: object) -> None:
                raise ValueError("synthetic setup failure")

            monkeypatch.setattr(
                execution, "assemble_group_operational_inputs_outcome", fail_assembly
            )
        if case in ("model_failure", "anticipated_failure", "poststate"):
            predict = model.predict_batch

            def predict_changed(
                artifact: object, inputs: GroupModelInputs, rng: random.Random
            ) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]:
                if case == "anticipated_failure":
                    raise ModelPredictionFailure("synthetic anticipated failure")
                if case == "model_failure":
                    raise ValueError("synthetic unexpected bug")
                return {
                    sid: (ensembles, b"forbidden")
                    for sid, (ensembles, _) in predict(artifact, inputs, rng).items()
                }

            monkeypatch.setattr(model, "predict_batch", predict_changed)
        if case == "uncertain_commit":
            write = PgCommittedForecastWriter.write_committed

            def uncertain(
                self: PgCommittedForecastWriter,
                forecasts: tuple[OperationalForecast, ...],
            ) -> dict[ForecastOutputKey, ForecastId]:
                write(self, forecasts)
                raise StoreError("synthetic lost commit acknowledgement")

            monkeypatch.setattr(PgCommittedForecastWriter, "write_committed", uncertain)
        if case == "uncertain_summary":

            def refuse_summary(*args: object, **kwargs: object) -> None:
                raise StoreError("synthetic lost commit acknowledgement")

            monkeypatch.setattr(PgDailyForecastStore, "finalize", refuse_summary)
        if case.startswith("carried_"):
            original_run = execution.run_group_forecast

            def carry_rejection(**kwargs: object) -> GroupForecastOutcome:
                outcome = original_run(**kwargs)
                result = next(iter(outcome.results.values()))
                payload = RejectedAssignmentPayload(
                    station_id=result.station_id,
                    model_id=result.model_id,
                    model_artifact_id=result.artifact_id,
                    issued_at=context.issue_time,
                    group_id=group.id,
                    parameters=tuple(
                        RejectedParameterPayload(
                            ensemble=f.ensemble,
                            qc_status=f.qc_status,
                            qc_flags=f.qc_flags,
                        )
                        for f in result.forecasts
                    ),
                )
                raise GroupForecastError(
                    "synthetic carried failure",
                    rejected=(payload, payload)
                    if case == "carried_validation"
                    else (payload,),
                    original=RuntimeError("synthetic build failure"),
                )

            monkeypatch.setattr(execution, "run_group_forecast", carry_rejection)
            if case == "carried_rollback":

                def rollback_rejection(
                    conn: object,
                    cursor: object,
                    statement: str,
                    parameters: object,
                    context: object,
                    executemany: bool,
                ) -> None:
                    if statement.startswith("INSERT INTO rejected_forecasts"):
                        raise RuntimeError("synthetic body failure")

                sa.event.listen(
                    outputs.engine, "after_cursor_execute", rollback_rejection
                )
            if case == "carried_uncertain":
                original_write = outputs.rejected.write_committed

                def lose_capture_ack(entry: object) -> object:
                    original_write(entry)
                    raise StoreError("synthetic lost commit acknowledgement")

                monkeypatch.setattr(
                    outputs.rejected, "write_committed", lose_capture_ack
                )
        inputs = OneShotGroupInputs(
            operational_ids=frozenset() if case == "excluded" else frozenset({sid}),
            forecast_bindings={sid: stations.fetch_weather_sources(sid)[0]},
            forcing_source=FakeWeatherReanalysisSource(),
            weather_forecast_store=weather,
            obs_store=observations,
            station_store=stations,
            basin_store=FakeBasinStore(),
            model_state_store=states,
            cycle_time=context.issue_time if case == "historical_weather" else _NOW,
            nwp_cycle_source=(
                NwpCycleSource.PRIMARY
                if case == "historical_weather"
                else NwpCycleSource.RUNOFF_ONLY
            ),
            nwp_cycle_reference_time=(
                context.issue_time if case == "historical_weather" else None
            ),
            qc_checker=ForecastOutputQualityChecker(),
            qc_rules=_empty_qc_rules(),
            all_baselines={},
            water_level_datums_masl={},
            rng=random.Random(42),
        )

        def run(
            runtime: LocalOneShotOutputs = outputs,
        ) -> tuple[int, tuple[str, ...]]:
            return run_local_one_shot_group(
                outputs=runtime,
                context=context,
                execution_id=execution_id,
                owner_id=uuid4(),
                group_id=group.id,
                config=config,
                model=model,
                group_store=groups,
                artifact_store=artifacts,
                inputs=inputs,
                clock=lambda: actual_now,
                id_gen=uuid4,
            )

        with pytest.raises(ValueError, match="open owned"):
            run(cast("LocalOneShotOutputs", object()))
        if case in (
            "uncertain_commit",
            "uncertain_summary",
            "missing_artifact_fail_ack",
            "carried_uncertain",
        ):
            with pytest.raises(StoreError, match="lost commit acknowledgement"):
                run()
        else:
            result = run()
            if case == "historical_weather":
                assert result == (1, ())
        snapshot = outputs.ledger.read_one_shot(key, execution_id=execution_id)
        assert snapshot is not None
        assert snapshot.execution.key.context == context
        assert snapshot.execution.started_at == actual_now
        assert (
            frozenset(m.station_id for m in snapshot.execution.frozen.members)
            == group.station_ids
        )
        if case in ("normal", "mixed", "historical_weather"):
            assert snapshot.state is DailyAttemptState.COMPLETE
            assert model.issues == [context.issue_time]
            assert context.issue_time != _NOW
            if case == "historical_weather":
                assert snapshot.finished_at == actual_now
                assert snapshot.execution.frozen.artifact_id == artifact_id
                assert snapshot.outcomes is not None
                (member,) = snapshot.outcomes
                assert member.state is DailyMemberState.PRODUCED
                assert member.provenance.nwp_source is NwpCycleSource.PRIMARY
                assert member.provenance.nwp_reference_time == context.issue_time
                assert member.provenance.nwp_age_hours == 78.0
                assert member.provenance.input_quality is InputQualityLevel.DEGRADED
                assert actual_now > context.issue_time + timedelta(days=3)
                with outputs.engine.connect() as conn:
                    stored = conn.execute(sa.select(forecasts)).mappings().one()
                assert stored["id"] == member.references[0].output_id
                assert stored["issued_at"] == context.issue_time
                assert stored["model_artifact_id"] == artifact_id
                assert stored["nwp_cycle_reference_time"] == context.issue_time
                assert stored["nwp_cycle_source"] == "primary"
                assert stored["input_quality"] == "degraded"
                assert stored["time_step_seconds"] == 86400

            if case == "mixed":
                assert snapshot.outcomes is not None
                assert {m.state for m in snapshot.outcomes} == {
                    DailyMemberState.PRODUCED,
                    DailyMemberState.CONFIGURATION_EXCLUDED,
                }
        elif case in (
            "uncertain_commit",
            "uncertain_summary",
            "missing_artifact_fail_ack",
            "carried_uncertain",
        ):
            assert snapshot.state is DailyAttemptState.ACTIVE
            assert model.issues == (
                [] if case == "missing_artifact_fail_ack" else [context.issue_time]
            )
            if case == "missing_artifact_fail_ack":
                assert len(failure_calls) == 1
        elif case == "excluded":
            assert snapshot.state is DailyAttemptState.INCOMPLETE
            assert snapshot.outcomes is not None
            assert snapshot.outcomes[0].state is DailyMemberState.CONFIGURATION_EXCLUDED
            assert model.issues == []
        elif case == "assignment_race":
            from sapphire_flow.types.daily_forecast_claim import DailyFailureReason

            assert snapshot.state is DailyAttemptState.FAILED
            assert snapshot.failure_reason is DailyFailureReason.CONFIGURATION_CHANGED
            assert model.issues == []
        elif case == "anticipated_failure":
            assert snapshot.state is DailyAttemptState.INCOMPLETE
            assert snapshot.outcomes is not None
            assert snapshot.outcomes[0].state is DailyMemberState.MODEL_FAILED
            assert model.issues == []
        elif case == "model_failure":
            assert snapshot.state is DailyAttemptState.FAILED
            assert model.issues == []
        elif case in ("poststate", "carried_validation", "carried_rollback"):
            assert snapshot.state is DailyAttemptState.FAILED
            assert model.issues == [context.issue_time]
        else:
            assert snapshot.state is DailyAttemptState.FAILED
            assert model.issues == []
        calls = list(model.issues)
        with pytest.raises(ValueError, match="already been admitted"):
            run()
        assert model.issues == calls
        assert states.fetch_latest_state(sid, daily_model_id) is None
        outputs.close()
        with pytest.raises(ValueError, match="open owned"):
            run()
    finally:
        outputs.cleanup()
