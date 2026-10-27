from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import daily_forecast_attempts
from sapphire_flow.exceptions import ModelOutputError
from sapphire_flow.services.daily_forecast_completion import complete_daily_group
from sapphire_flow.services.run_group_forecast import GroupForecastOutcome
from sapphire_flow.services.run_station_forecast import StationForecastResult
from sapphire_flow.store.committed_forecast_writer import PgCommittedForecastWriter
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.daily_forecast import (
    DailyAttemptDecision,
    DailyAttemptOrdinal,
    DailyAttemptState,
    OneShotDailyForecast,
    OneShotForecastTrigger,
)
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    DailyFailureReason,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_one_shot import (
    OneShotDailyExecution,
    OneShotDailyKey,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberState,
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.daily_forecast_recovery import DailyRecoveryStatus
from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.daily_output import DailyOutputContract, DailyOutputEvidence
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    InputQualityLevel,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedForecastEntry,
    RejectedParameterPayload,
)
from tests.conftest import make_forecast_ensemble
from tests.integration.store.test_daily_forecast_claim_store import (
    NOW,
    _later,
)
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_forecast_store import _make_forecast


def prepare_one_shot(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> tuple[OneShotDailyExecution, DailyMemberOutcome, tuple[DailyOutputEvidence, ...]]:
    key, frozen = assignment
    contracts = tuple(
        DailyOutputContract(
            parameter=parameter,
            units="m³/s",
            representation=EnsembleRepresentation.MEMBERS,
            time_step=timedelta(hours=1),
            first_lead=timedelta(hours=1),
            horizon_steps=2,
            series_keys=(0.0, 1.0),
        )
        for parameter in frozen.members[0].required_parameters
    )
    frozen = replace(frozen, output_contracts=contracts)
    claim = PgDailyForecastStore(isolated_committed_db_engine.begin).start_one_shot(
        one_shot_key(key),
        frozen,
        execution_id=uuid4(),
        owner_id=uuid4(),
        started_at=NOW,
    )
    sid = frozen.members[0].station_id
    params = []
    flags = (
        QcFlag(
            rule_id="range_check",
            rule_version="1",
            status=QcStatus.QC_FAILED,
            detail="fixture",
        ),
    )
    for parameter in frozen.members[0].required_parameters:
        ensemble = make_forecast_ensemble(
            station_id=sid,
            model_id=key.model_id,
            parameter=parameter,
            n_steps=2,
            n_members=2,
        )
        ensemble = replace(
            ensemble,
            issued_at=NOW,
            values=ensemble.values.with_columns(
                pl.col("valid_time") + (NOW - ensemble.issued_at)
            ),
        )
        params.append(
            RejectedParameterPayload(
                ensemble=ensemble, qc_status=QcStatus.QC_FAILED, qc_flags=flags
            )
        )
    entry = RejectedForecastEntry(
        attempt_id=claim.id,
        payload=RejectedAssignmentPayload(
            station_id=sid,
            model_id=key.model_id,
            model_artifact_id=frozen.artifact_id,
            issued_at=NOW,
            group_id=key.group_id,
            parameters=tuple(params),
        ),
    )
    with isolated_committed_db_engine.connect() as conn:
        mapping = PgRejectedForecastStore(
            conn, transaction_factory=isolated_committed_db_engine.begin
        ).write_committed(entry)
    refs = tuple(
        DailyOutputReference(
            key=output_key, source=DailyOutputSource.REJECTED, output_id=output_id
        )
        for output_key, output_id in mapping.items()
    )
    provenance = DailyInputProvenance(
        nwp_source=NwpCycleSource.PRIMARY,
        nwp_reference_time=NOW,
        nwp_age_hours=0.0,
        observation_staleness_hours=2.0,
        input_quality=InputQualityLevel.FULL,
    )
    evidence = tuple(
        DailyOutputEvidence(
            reference=ref,
            ensemble=param.ensemble,
            qc_status=param.qc_status,
            qc_flags=param.qc_flags,
            provenance=provenance,
        )
        for ref, param in zip(refs, params, strict=True)
    )
    outcome = replace(
        frozen.members[0],
        state=DailyMemberState.PRODUCED,
        references=refs,
        provenance=provenance,
    )
    return claim, outcome, evidence


def _forecasts(
    claim: OneShotDailyExecution, evidence: tuple[DailyOutputEvidence, ...]
) -> tuple[OperationalForecast, ...]:
    assert claim.frozen.artifact_id is not None
    provenance = evidence[0].provenance
    return tuple(
        replace(
            _make_forecast(
                item.reference.key.station_id,
                claim.key.model_id,
                claim.frozen.artifact_id,
                issued_at=NOW,
                parameter=item.ensemble.parameter,
            ),
            ensemble=item.ensemble,
            qc_status=QcStatus.QC_PASSED,
            nwp_cycle_source=provenance.nwp_source,
            nwp_cycle_reference_time=provenance.nwp_reference_time,
            observation_staleness_hours=provenance.observation_staleness_hours,
            input_quality=provenance.input_quality,
            input_quality_flags=provenance.input_quality_flags,
        )
        for item in evidence
    )


def one_shot_key(
    key: DailyClaimKey, trigger: OneShotForecastTrigger = OneShotForecastTrigger.MANUAL
) -> OneShotDailyKey:
    return OneShotDailyKey(
        tenant_id=key.tenant_id,
        deployment=key.deployment,
        group_id=key.group_id,
        model_id=key.model_id,
        context=OneShotDailyForecast(trigger=trigger, target_date=NOW.date()),
    )


@pytest.mark.parametrize("trigger", list(OneShotForecastTrigger))
def test_one_shots_cannot_shadow_claims_or_consume_retry_budget(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    trigger: OneShotForecastTrigger,
) -> None:
    key, frozen = assignment
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    one_key = one_shot_key(key, trigger)
    active = store.start_one_shot(
        one_key, frozen, execution_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    failed = store.start_one_shot(
        one_key, frozen, execution_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    store.fail(failed, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    assert store.read_day(key).latest is None
    assert store.pending_keys(key.tenant_id, key.deployment, key.context) == ()
    assert store.recovery_status(key) is DailyRecoveryStatus.UNTOUCHED
    first = store.claim(
        key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    assert isinstance(first, DailyClaim)
    assert first.ordinal is DailyAttemptOrdinal.INITIAL
    assert store.read_one_shot(one_key, execution_id=active.id).execution == active
    store.fail(first, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    later = _later(key)
    assert store.pending_keys(key.tenant_id, key.deployment, later.context) == (later,)
    retry = store.claim(
        later, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    assert isinstance(retry, DailyClaim)
    assert retry.ordinal is DailyAttemptOrdinal.RETRY
    store.fail(retry, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    assert (
        store.claim(
            _later(key, 12),
            frozen,
            attempt_id=uuid4(),
            owner_id=uuid4(),
            started_at=NOW,
        )
        is DailyAttemptDecision.EXHAUSTED
    )


def test_duplicate_id_and_wrong_scope_refused(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    key, frozen = assignment
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    one_key = one_shot_key(key)
    execution = store.start_one_shot(
        one_key, frozen, execution_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    with pytest.raises(ValueError, match="already been admitted"):
        store.start_one_shot(
            one_key, frozen, execution_id=execution.id, owner_id=uuid4(), started_at=NOW
        )
    assert (
        store.read_one_shot(
            replace(one_key, deployment="elsewhere"), execution_id=execution.id
        )
        is None
    )
    assert (
        store.read_one_shot(
            one_shot_key(key, OneShotForecastTrigger.REPLAY), execution_id=execution.id
        )
        is None
    )
    for wrong in (
        replace(execution, owner_id=uuid4()),
        replace(execution, frozen=replace(frozen, configuration_sha256="b" * 64)),
        replace(execution, key=one_shot_key(key, OneShotForecastTrigger.REPLAY)),
    ):
        with pytest.raises(ValueError, match="owned claim|frozen database identity"):
            store.fail(wrong, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    assert (
        store.read_one_shot(one_key, execution_id=execution.id).state
        is DailyAttemptState.ACTIVE
    )


def test_finalization_exact_evidence_and_scheduled_authority(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    execution, outcome, evidence = prepare_one_shot(
        isolated_committed_db_engine, assignment
    )
    key, frozen = assignment
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    with pytest.raises(ValueError, match="frozen database identity"):
        store.finalize(
            replace(execution, key=one_shot_key(key, OneShotForecastTrigger.REPLAY)),
            outcomes=(outcome,),
            evidence=evidence,
            finished_at=NOW,
        )
    with pytest.raises(ValueError, match="exact evidence"):
        store.finalize(
            execution, outcomes=(outcome,), evidence=evidence[:1], finished_at=NOW
        )
    assert (
        store.finalize(
            execution, outcomes=(outcome,), evidence=evidence, finished_at=NOW
        )
        is DailyAttemptState.COMPLETE
    )
    snapshot = store.read_one_shot(execution.key, execution_id=execution.id)
    assert snapshot.outcomes == (outcome,)
    assert store.read_day(key).authoritative is None
    first = store.claim(
        key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    assert isinstance(first, DailyClaim)
    assert store.read_day(key).latest.claim == first
    assert store.recovery_status(key) is DailyRecoveryStatus.UNTOUCHED
    with pytest.raises(ValueError, match="active owned claim"):
        store.fail(execution, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)


def test_refused_summary_commit_retains_active_one_shot(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    execution, outcome, evidence = prepare_one_shot(
        isolated_committed_db_engine, assignment
    )

    @contextmanager
    def refused() -> Iterator[sa.Connection]:
        with isolated_committed_db_engine.begin() as conn:
            yield conn
            raise RuntimeError("refused commit")

    with pytest.raises(RuntimeError, match="refused commit"):
        PgDailyForecastStore(refused).finalize(
            execution, outcomes=(outcome,), evidence=evidence, finished_at=NOW
        )
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    assert (
        store.read_one_shot(execution.key, execution_id=execution.id).state
        is DailyAttemptState.ACTIVE
    )
    with pytest.raises(ValueError, match="already been admitted"):
        store.start_one_shot(
            execution.key,
            execution.frozen,
            execution_id=execution.id,
            owner_id=execution.owner_id,
            started_at=NOW,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"trigger_kind": "scheduled", "ordinal": None, "scheduled_tick": NOW},
        {"trigger_kind": "scheduled", "ordinal": 1, "scheduled_tick": None},
        {"trigger_kind": "manual", "ordinal": 1},
        {"trigger_kind": "replay", "scheduled_tick": NOW},
        {"trigger_kind": "manual", "issued_at": NOW + timedelta(hours=1)},
        {"trigger_kind": "replay", "parent_id": "self"},
        {"trigger_kind": None},
        {"trigger_kind": "other"},
    ],
)
def test_discriminator_constraints_reject_null_loopholes(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    changes: dict[str, object],
) -> None:
    key, frozen = assignment
    values = dict(
        id=uuid4(),
        tenant_id=key.tenant_id,
        deployment=key.deployment,
        group_id=key.group_id,
        model_id=key.model_id,
        data_use="standard",
        issued_at=NOW,
        trigger_kind="manual",
        scheduled_tick=None,
        ordinal=None,
        parent_id=None,
        owner_id=uuid4(),
        started_at=NOW,
        state="active",
        artifact_id=frozen.artifact_id,
        configuration_sha256=frozen.configuration_sha256,
        roster=[],
        output_contracts=[],
    )
    values.update(changes)
    if values["parent_id"] == "self":
        values["parent_id"] = values["id"]
    with (
        pytest.raises(sa.exc.IntegrityError, match="constraint"),
        isolated_committed_db_engine.begin() as conn,
    ):
        conn.execute(sa.insert(daily_forecast_attempts).values(**values))


@pytest.mark.parametrize(
    "column,value",
    [("trigger_kind", "replay"), ("owner_id", None), ("issued_at", None)],
)
def test_one_shot_identity_immutable(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    column: str,
    value: object,
) -> None:
    key, frozen = assignment
    execution = PgDailyForecastStore(isolated_committed_db_engine.begin).start_one_shot(
        one_shot_key(key),
        frozen,
        execution_id=uuid4(),
        owner_id=uuid4(),
        started_at=NOW,
    )
    with (
        pytest.raises(sa.exc.DBAPIError, match="immutable"),
        isolated_committed_db_engine.begin() as conn,
    ):
        conn.execute(
            sa.update(daily_forecast_attempts)
            .where(daily_forecast_attempts.c.id == execution.id)
            .values(**{column: value})
        )


def test_one_shot_cannot_be_recovered_even_by_owner(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    key, frozen = assignment
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    execution = store.start_one_shot(
        one_shot_key(key),
        frozen,
        execution_id=uuid4(),
        owner_id=uuid4(),
        started_at=NOW,
    )
    with pytest.raises(ValueError, match="one-shot recovery"):
        store.abandon(execution, None)
    with (
        pytest.raises(sa.exc.IntegrityError, match="ck_daily_recovery"),
        isolated_committed_db_engine.begin() as conn,
    ):
        conn.execute(
            sa.update(daily_forecast_attempts)
            .where(daily_forecast_attempts.c.id == execution.id)
            .values(state="abandoned", finished_at=NOW, recovery={})
        )


class TestOneShotGroupCompletion:
    @pytest.mark.parametrize("case", ["accepted", "invalid", "missing", "post_state"])
    def test_finalizes_whole_summary(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        case: str,
    ) -> None:
        claim, member, prior = prepare_one_shot(
            isolated_committed_db_engine, assignment
        )
        forecasts = _forecasts(claim, prior)
        assert claim.frozen.artifact_id is not None
        if case == "invalid":
            forecasts = tuple(
                replace(f, ensemble=replace(f.ensemble, units="wrong"))
                for f in forecasts
            )
        station = StationForecastResult(
            station_id=member.station_id,
            model_id=claim.key.model_id,
            artifact_id=claim.frozen.artifact_id,
            forecasts=list(forecasts),
            new_state=b"bad" if case == "post_state" else None,
            ensembles={f.ensemble.parameter: f.ensemble for f in forecasts},
        )
        result = GroupForecastOutcome(
            results={} if case == "missing" else {member.station_id: station}
        )
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        with isolated_committed_db_engine.connect() as conn:
            rejected = PgRejectedForecastStore(
                conn, transaction_factory=isolated_committed_db_engine.begin
            )
            if case == "post_state":
                with pytest.raises(ModelOutputError, match="stateless.*post-state"):
                    complete_daily_group(
                        claim=claim,
                        result=result,
                        exclusions=(),
                        provenance={member.station_id: member.provenance},
                        forecast_writer=PgCommittedForecastWriter(
                            isolated_committed_db_engine.begin
                        ),
                        rejected_writer=rejected,
                        store=store,
                        clock=lambda: NOW,
                    )
            else:
                state = complete_daily_group(
                    claim=claim,
                    result=result,
                    exclusions=(),
                    provenance={member.station_id: member.provenance},
                    forecast_writer=PgCommittedForecastWriter(
                        isolated_committed_db_engine.begin
                    ),
                    rejected_writer=rejected,
                    store=store,
                    clock=lambda: NOW,
                )
                assert state.state is (
                    DailyAttemptState.COMPLETE
                    if case == "accepted"
                    else DailyAttemptState.INCOMPLETE
                )
        snapshot = store.read_one_shot(claim.key, execution_id=claim.id)
        assert snapshot is not None
        assert snapshot.state is (
            DailyAttemptState.ACTIVE
            if case == "post_state"
            else DailyAttemptState.COMPLETE
            if case == "accepted"
            else DailyAttemptState.INCOMPLETE
        )


def test_scheduled_identity_cannot_transition_one_shot(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    execution, outcome, evidence = prepare_one_shot(
        isolated_committed_db_engine, assignment
    )
    key, _ = assignment
    forged = DailyClaim(
        id=execution.id,
        owner_id=execution.owner_id,
        key=key,
        ordinal=DailyAttemptOrdinal.INITIAL,
        frozen=execution.frozen,
        parent_id=None,
        started_at=NOW,
    )
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    with pytest.raises(ValueError, match="frozen database identity"):
        store.finalize(forged, outcomes=(outcome,), evidence=evidence, finished_at=NOW)
    with pytest.raises(ValueError, match="frozen database identity"):
        store.fail(forged, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    snapshot = store.read_one_shot(execution.key, execution_id=execution.id)
    assert snapshot is not None and snapshot.state is DailyAttemptState.ACTIVE


def test_completed_one_shot_never_replaces_scheduled_authority(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    from tests.integration.store.test_daily_forecast_finalization import _prepare

    scheduled, scheduled_outcome, scheduled_evidence = _prepare(
        isolated_committed_db_engine, assignment
    )
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    store.finalize(
        scheduled,
        outcomes=(scheduled_outcome,),
        evidence=scheduled_evidence,
        finished_at=NOW,
    )
    before = store.read_day(scheduled.key)
    execution, outcome, evidence = prepare_one_shot(
        isolated_committed_db_engine, assignment
    )
    store.finalize(execution, outcomes=(outcome,), evidence=evidence, finished_at=NOW)
    assert store.read_day(scheduled.key) == before
    assert (
        store.pending_keys(
            scheduled.key.tenant_id,
            scheduled.key.deployment,
            _later(scheduled.key).context,
        )
        == ()
    )
    assert (
        store.claim(
            _later(scheduled.key),
            scheduled.frozen,
            attempt_id=uuid4(),
            owner_id=uuid4(),
            started_at=NOW,
        )
        is DailyAttemptDecision.CLOSED
    )


def test_uncommitted_reference_cannot_finalize_one_shot(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    execution, outcome, evidence = prepare_one_shot(
        isolated_committed_db_engine, assignment
    )
    reference = replace(evidence[0].reference, output_id=uuid4())
    wrong_evidence = (replace(evidence[0], reference=reference), *evidence[1:])
    wrong_outcome = replace(
        outcome, references=tuple(e.reference for e in wrong_evidence)
    )
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    with pytest.raises(ValueError, match="committed|missing|match"):
        store.finalize(
            execution,
            outcomes=(wrong_outcome,),
            evidence=wrong_evidence,
            finished_at=NOW,
        )
    snapshot = store.read_one_shot(execution.key, execution_id=execution.id)
    assert snapshot is not None and snapshot.state is DailyAttemptState.ACTIVE


def test_migration_metadata_discriminator_and_index_parity(
    isolated_committed_db_engine: sa.Engine,
) -> None:
    inspector = sa.inspect(isolated_committed_db_engine)
    columns = {c["name"]: c for c in inspector.get_columns("daily_forecast_attempts")}
    for name in ("trigger_kind", "scheduled_tick", "ordinal", "parent_id"):
        assert columns[name]["nullable"] == daily_forecast_attempts.c[name].nullable
    indices = {i["name"]: i for i in inspector.get_indexes("daily_forecast_attempts")}
    for name in ("uq_daily_active", "uq_daily_ordinal"):
        assert indices[name]["unique"]
        assert "trigger_kind" in indices[name]["dialect_options"]["postgresql_where"]
        assert "scheduled" in indices[name]["dialect_options"]["postgresql_where"]
        declared = next(i for i in daily_forecast_attempts.indexes if i.name == name)
        assert "trigger_kind" in str(declared.dialect_options["postgresql"]["where"])
    checks = {
        c["name"]: c["sqltext"]
        for c in inspector.get_check_constraints("daily_forecast_attempts")
    }
    assert "ordinal IS NOT NULL" in checks["ck_daily_identity"]
    assert "scheduled_tick IS NOT NULL" in checks["ck_daily_identity"]
    assert "ordinal IS NULL" in checks["ck_daily_identity"]
    assert "recovery IS NULL" in checks["ck_daily_recovery"]
