from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Literal
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.exceptions import DailyOutputRolledBackError
from sapphire_flow.flows._db import make_pg_stores
from sapphire_flow.services.daily_forecast_persistence import (
    commit_daily_forecasts,
    commit_daily_rejection,
)
from sapphire_flow.store.committed_forecast_writer import PgCommittedForecastWriter
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyOutputSource,
)
from sapphire_flow.types.daily_output import DailyOutputEvidence
from sapphire_flow.types.enums import QcStatus
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import ArtifactId, ForecastId
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedParameterPayload,
)
from tests.integration.store.test_daily_forecast_claim_store import NOW
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_daily_forecast_finalization import _prepare
from tests.integration.store.test_forecast_store import _make_forecast


def _forecasts(
    claim: DailyClaim, evidence: tuple[DailyOutputEvidence, ...]
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


def _writer(
    isolated_committed_db_engine: sa.Engine, source: Literal["direct", "runtime"]
) -> PgCommittedForecastWriter:
    if source == "direct":
        return PgCommittedForecastWriter(isolated_committed_db_engine.begin)
    with isolated_committed_db_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as conn:
        writer = make_pg_stores(conn)["daily_forecast_writer"]
        assert isinstance(writer, PgCommittedForecastWriter)
        return writer


class TestCommittedDailyOutputs:
    @pytest.mark.parametrize("writer_source", ["direct", "runtime"])
    def test_reused_ids_are_committed_and_can_finalize(
        self,
        isolated_committed_db_engine: sa.Engine,
        writer_source: Literal["direct", "runtime"],
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, outcome, prior = _prepare(isolated_committed_db_engine, assignment)
        forecasts = _forecasts(claim, prior)
        writer = _writer(isolated_committed_db_engine, writer_source)
        first = commit_daily_forecasts(
            claim, outcome.station_id, forecasts, outcome.provenance, writer
        )
        replacement = tuple(replace(f, id=ForecastId(uuid4())) for f in forecasts)
        second = commit_daily_forecasts(
            claim, outcome.station_id, replacement, outcome.provenance, writer
        )
        assert [e.reference.output_id for e in second] == [
            e.reference.output_id for e in first
        ]
        assert all(e.reference.source is DailyOutputSource.FORECAST for e in second)
        final = replace(outcome, references=tuple(e.reference for e in second))
        assert (
            PgDailyForecastStore(isolated_committed_db_engine.begin).finalize(
                claim, outcomes=(final,), evidence=second, finished_at=NOW
            )
            is DailyAttemptState.COMPLETE
        )

    def test_rejected_output_is_committed_under_claim_identity(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, outcome, prior = _prepare(isolated_committed_db_engine, assignment)
        payload = RejectedAssignmentPayload(
            station_id=outcome.station_id,
            model_id=claim.key.model_id,
            model_artifact_id=claim.frozen.artifact_id,
            issued_at=NOW,
            group_id=claim.key.group_id,
            parameters=tuple(
                RejectedParameterPayload(
                    ensemble=e.ensemble, qc_status=e.qc_status, qc_flags=e.qc_flags
                )
                for e in prior
            ),
        )
        with isolated_committed_db_engine.connect() as conn:
            evidence = commit_daily_rejection(
                claim,
                payload,
                outcome.provenance,
                PgRejectedForecastStore(
                    conn, transaction_factory=isolated_committed_db_engine.begin
                ),
            )
        final = replace(outcome, references=tuple(e.reference for e in evidence))
        assert (
            PgDailyForecastStore(isolated_committed_db_engine.begin).finalize(
                claim, outcomes=(final,), evidence=evidence, finished_at=NOW
            )
            is DailyAttemptState.COMPLETE
        )

    def test_invalid_later_parameter_prevents_all_ordinary_writes(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, outcome, prior = _prepare(isolated_committed_db_engine, assignment)
        forecasts = list(_forecasts(claim, prior))
        forecasts[-1] = replace(forecasts[-1], qc_status=QcStatus.RAW)
        with pytest.raises(ValueError, match="requires rejected evidence"):
            commit_daily_forecasts(
                claim,
                outcome.station_id,
                tuple(forecasts),
                outcome.provenance,
                PgCommittedForecastWriter(isolated_committed_db_engine.begin),
            )
        with isolated_committed_db_engine.connect() as conn:
            assert (
                conn.execute(
                    sa.text("SELECT count(*) FROM forecasts WHERE station_id = :sid"),
                    {"sid": claim.frozen.members[0].station_id},
                ).scalar_one()
                == 0
            )

    @pytest.mark.parametrize("writer_source", ["direct", "runtime"])
    def test_later_storage_failure_rolls_back_whole_batch(
        self,
        isolated_committed_db_engine: sa.Engine,
        writer_source: Literal["direct", "runtime"],
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, _, prior = _prepare(isolated_committed_db_engine, assignment)
        forecasts = list(_forecasts(claim, prior))
        assert len(forecasts) > 1
        forecasts[-1] = replace(forecasts[-1], model_artifact_id=ArtifactId(uuid4()))
        with pytest.raises(DailyOutputRolledBackError, match="rolled back") as failure:
            _writer(isolated_committed_db_engine, writer_source).write_committed(
                tuple(forecasts)
            )
        assert isinstance(failure.value.__cause__, sa.exc.IntegrityError)
        with isolated_committed_db_engine.connect() as conn:
            assert (
                conn.execute(
                    sa.text("SELECT count(*) FROM forecasts WHERE station_id = :sid"),
                    {"sid": claim.frozen.members[0].station_id},
                ).scalar_one()
                == 0
            )


class TestCommittedWriterBounds:
    def test_limits_apply_inside_write_transaction(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, _, prior = _prepare(isolated_committed_db_engine, assignment)
        limits: list[tuple[str, str]] = []

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with isolated_committed_db_engine.begin() as txn:
                yield txn
                limits.append(
                    (
                        txn.execute(sa.text("SHOW lock_timeout")).scalar_one(),
                        txn.execute(sa.text("SHOW statement_timeout")).scalar_one(),
                    )
                )

        PgCommittedForecastWriter(transaction).write_committed(_forecasts(claim, prior))
        assert limits == [("5s", "30s")]

    @pytest.mark.parametrize("writer_source", ["direct", "runtime"])
    def test_contention_refuses_batch_and_leaves_ownership_active(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        writer_source: Literal["direct", "runtime"],
    ) -> None:
        claim, _, prior = _prepare(isolated_committed_db_engine, assignment)
        writer = _writer(isolated_committed_db_engine, writer_source)
        with isolated_committed_db_engine.begin() as blocker:
            blocker.execute(sa.text("LOCK TABLE forecasts IN ACCESS EXCLUSIVE MODE"))
            with pytest.raises(
                DailyOutputRolledBackError, match="rolled back"
            ) as failure:
                writer.write_committed(_forecasts(claim, prior))
            assert isinstance(failure.value.__cause__, sa.exc.OperationalError)
            assert "lock timeout" in str(failure.value.__cause__)
        with isolated_committed_db_engine.connect() as conn:
            assert (
                conn.execute(
                    sa.text("SELECT count(*) FROM forecasts WHERE station_id = :sid"),
                    {"sid": claim.frozen.members[0].station_id},
                ).scalar_one()
                == 0
            )
        snapshot = PgDailyForecastStore(isolated_committed_db_engine.begin).read_day(
            claim.key
        )
        assert snapshot.latest is not None
        assert snapshot.latest.state is DailyAttemptState.ACTIVE
        assert snapshot.authoritative is None


class TestRollbackEvidence:
    @pytest.mark.parametrize("loss", ["rollback_ack", "commit_ack"])
    def test_missing_transaction_ack_is_not_rollback_proof(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        monkeypatch: pytest.MonkeyPatch,
        loss: str,
    ) -> None:
        claim, _, prior = _prepare(isolated_committed_db_engine, assignment)
        forecasts = list(_forecasts(claim, prior))
        if loss == "rollback_ack":
            forecasts[-1] = replace(
                forecasts[-1], model_artifact_id=ArtifactId(uuid4())
            )

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with isolated_committed_db_engine.begin() as txn:
                if loss == "rollback_ack":
                    rollback = txn.rollback

                    def lose_rollback_ack() -> None:
                        rollback()
                        raise ConnectionError("rollback acknowledgement lost")

                    monkeypatch.setattr(txn, "rollback", lose_rollback_ack)
                yield txn
            if loss == "commit_ack":
                raise ConnectionError("commit acknowledgement lost")

        with pytest.raises(ConnectionError, match="acknowledgement lost"):
            PgCommittedForecastWriter(transaction).write_committed(tuple(forecasts))
        day = PgDailyForecastStore(isolated_committed_db_engine.begin).read_day(
            claim.key
        )
        assert day.latest is not None
        assert day.latest.state is DailyAttemptState.ACTIVE
