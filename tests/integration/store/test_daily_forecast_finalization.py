from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptDecision, DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberReason,
    DailyMemberState,
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.daily_output import DailyOutputContract, DailyOutputEvidence
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    InputQualityLevel,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.ids import ForecastId
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


def _prepare(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> tuple[DailyClaim, DailyMemberOutcome, tuple[DailyOutputEvidence, ...]]:
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
    claim = PgDailyForecastStore(isolated_committed_db_engine.begin).claim(
        key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    assert isinstance(claim, DailyClaim)
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


class TestDailyFinalization:
    def test_committed_multi_parameter_rejection_can_close_evaluation_day(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, outcome, evidence = _prepare(isolated_committed_db_engine, assignment)
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        assert (
            store.finalize(
                claim, outcomes=(outcome,), evidence=evidence, finished_at=NOW
            )
            is DailyAttemptState.COMPLETE
        )
        assert (
            store.claim(
                _later(claim.key),
                claim.frozen,
                attempt_id=uuid4(),
                owner_id=uuid4(),
                started_at=NOW,
            )
            is DailyAttemptDecision.CLOSED
        )
        with isolated_committed_db_engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT summary FROM daily_forecast_attempts WHERE id = :id"),
                {"id": claim.id},
            ).scalar_one()
        assert {ref["output_id"] for ref in row[0]["references"]} == {
            str(ref.output_id) for ref in outcome.references
        }

    def test_rejected_summary_preserves_assembly_provenance(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, outcome, evidence = _prepare(isolated_committed_db_engine, assignment)
        PgDailyForecastStore(isolated_committed_db_engine.begin).finalize(
            claim, outcomes=(outcome,), evidence=evidence, finished_at=NOW
        )
        with isolated_committed_db_engine.connect() as conn:
            summary = conn.execute(
                sa.text("SELECT summary FROM daily_forecast_attempts WHERE id = :id"),
                {"id": claim.id},
            ).scalar_one()
        assert summary[0]["provenance"]["observation_staleness_hours"] == 2.0
        assert summary[0]["provenance"]["input_quality"] == "full"
        assert summary[0]["provenance"]["nwp_age_hours"] == 0.0

    @pytest.mark.parametrize(
        "damage",
        [
            "missing-evidence",
            "missing-member",
            "wrong-owner",
            "wrong-id",
            "changed-values",
            "changed-provenance",
            "forged-contract",
        ],
    )
    def test_unverified_output_does_not_release_claim(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        damage: str,
    ) -> None:
        claim, outcome, evidence = _prepare(isolated_committed_db_engine, assignment)
        original = claim
        outcomes = (outcome,)
        if damage == "missing-evidence":
            evidence = evidence[:1]
        elif damage == "missing-member":
            outcomes = ()
        elif damage == "wrong-owner":
            claim = replace(claim, owner_id=uuid4())
        elif damage == "wrong-id":
            ref = replace(evidence[0].reference, output_id=uuid4())
            outcome = replace(outcome, references=(ref, outcome.references[1]))
            outcomes = (outcome,)
            evidence = (replace(evidence[0], reference=ref), evidence[1])
        elif damage == "changed-provenance":
            outcomes = (
                replace(
                    outcome, provenance=replace(outcome.provenance, nwp_age_hours=24.0)
                ),
            )
        elif damage == "changed-values":
            ensemble = replace(
                evidence[0].ensemble,
                values=evidence[0].ensemble.values.with_columns(
                    pl.lit(100.0).alias("value")
                ),
            )
            evidence = (replace(evidence[0], ensemble=ensemble), evidence[1])
        else:
            contract = replace(claim.frozen.output_contracts[0], units="m")
            claim = replace(
                claim,
                frozen=replace(
                    claim.frozen,
                    output_contracts=(contract, claim.frozen.output_contracts[1]),
                ),
            )
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        with pytest.raises(ValueError, match="daily"):
            store.finalize(claim, outcomes=outcomes, evidence=evidence, finished_at=NOW)
        assert (
            store.claim(
                _later(original.key),
                original.frozen,
                attempt_id=uuid4(),
                owner_id=uuid4(),
                started_at=NOW,
            )
            is DailyAttemptDecision.OWNED
        )

    def test_finalize_is_immutable(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, outcome, evidence = _prepare(isolated_committed_db_engine, assignment)
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        store.finalize(claim, outcomes=(outcome,), evidence=evidence, finished_at=NOW)
        with pytest.raises(ValueError, match="daily"):
            store.finalize(
                claim, outcomes=(outcome,), evidence=evidence, finished_at=NOW
            )

    def test_idempotently_reused_ordinary_ids_are_authoritative(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, outcome, evidence = _prepare(isolated_committed_db_engine, assignment)
        accepted = []
        for item in evidence:
            forecast = _make_forecast(
                item.reference.key.station_id,
                claim.key.model_id,
                claim.frozen.artifact_id,
                issued_at=NOW,
                n_steps=2,
                n_members=2,
                parameter=item.reference.key.parameter,
                nwp_cycle_reference_time=NOW,
            )
            forecast = replace(
                forecast, ensemble=item.ensemble, qc_status=QcStatus.QC_PASSED
            )
            with isolated_committed_db_engine.connect() as conn:
                store = PgForecastStore(
                    conn, transaction_factory=isolated_committed_db_engine.begin
                )
                original_id = store.store_forecast(forecast)
                returned_id = store.store_forecast(
                    replace(forecast, id=ForecastId(uuid4()))
                )
            assert returned_id == original_id
            accepted.append(
                replace(
                    item,
                    reference=replace(
                        item.reference,
                        source=DailyOutputSource.FORECAST,
                        output_id=returned_id,
                    ),
                    qc_status=QcStatus.QC_PASSED,
                    qc_flags=(),
                    provenance=DailyInputProvenance(
                        nwp_source=NwpCycleSource.PRIMARY,
                        nwp_reference_time=NOW,
                        nwp_age_hours=0.0,
                    ),
                )
            )
        outcome = replace(
            outcome,
            references=tuple(item.reference for item in accepted),
            provenance=accepted[0].provenance,
        )
        result = PgDailyForecastStore(isolated_committed_db_engine.begin).finalize(
            claim, outcomes=(outcome,), evidence=tuple(accepted), finished_at=NOW
        )
        assert result is DailyAttemptState.COMPLETE

    def test_all_excluded_is_finalized_but_does_not_close_day(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        claim = store.claim(
            key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(claim, DailyClaim)
        excluded = replace(
            frozen.members[0],
            state=DailyMemberState.INPUT_EXCLUDED,
            reason=DailyMemberReason.MISSING_REQUIRED_TARGET,
        )
        assert (
            store.finalize(claim, outcomes=(excluded,), evidence=(), finished_at=NOW)
            is DailyAttemptState.INCOMPLETE
        )
        retry = store.claim(
            _later(key), frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(retry, DailyClaim)
        assert retry.parent_id == claim.id

    def test_another_attempts_rejected_rows_are_not_reused(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, outcome, evidence = _prepare(isolated_committed_db_engine, assignment)
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        excluded = replace(
            claim.frozen.members[0],
            state=DailyMemberState.INPUT_EXCLUDED,
            reason=DailyMemberReason.MISSING_REQUIRED_TARGET,
        )
        store.finalize(claim, outcomes=(excluded,), evidence=(), finished_at=NOW)
        retry = store.claim(
            _later(claim.key),
            claim.frozen,
            attempt_id=uuid4(),
            owner_id=uuid4(),
            started_at=NOW,
        )
        assert isinstance(retry, DailyClaim)
        with pytest.raises(ValueError, match="another attempt"):
            store.finalize(
                retry, outcomes=(outcome,), evidence=evidence, finished_at=NOW
            )

    def test_failed_summary_commit_leaves_claim_owned(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, outcome, evidence = _prepare(isolated_committed_db_engine, assignment)

        @contextmanager
        def refuse_commit() -> Iterator[sa.Connection]:
            with isolated_committed_db_engine.begin() as txn:
                yield txn
                raise RuntimeError("fixture summary commit refused")

        with pytest.raises(RuntimeError, match="summary commit refused"):
            PgDailyForecastStore(refuse_commit).finalize(
                claim, outcomes=(outcome,), evidence=evidence, finished_at=NOW
            )
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        assert (
            store.claim(
                _later(claim.key),
                claim.frozen,
                attempt_id=uuid4(),
                owner_id=uuid4(),
                started_at=NOW,
            )
            is DailyAttemptDecision.OWNED
        )
