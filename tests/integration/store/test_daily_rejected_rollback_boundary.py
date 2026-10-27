from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from sapphire_flow.db.metadata import forecasts, rejected_forecasts
from sapphire_flow.exceptions import DailyOutputRolledBackError, StoreError
from sapphire_flow.services.daily_forecast_completion import complete_daily_group
from sapphire_flow.services.run_group_forecast import GroupForecastOutcome
from sapphire_flow.services.run_station_forecast import StationForecastResult
from sapphire_flow.store.committed_forecast_writer import PgCommittedForecastWriter
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.daily_forecast import DailyAttemptDecision, DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaimKey,
    FrozenDailyAssignment,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedParameterPayload,
)
from tests.conftest import make_station_config
from tests.integration.store.test_daily_forecast_claim_store import NOW, _later
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_daily_forecast_finalization import _prepare
from tests.integration.store.test_daily_forecast_persistence import _forecasts


class TestRejectedRollbackOwnership:
    def test_later_rejected_rollback_keeps_earlier_committed_member_owned(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        second_id = StationId(uuid4())
        with isolated_committed_db_engine.begin() as conn:
            PgStationStore(conn).store_station(
                make_station_config(
                    station_id=second_id,
                    code=f"rejected-rollback-{second_id}",
                    tenant_id=key.tenant_id,
                )
            )
        frozen = replace(
            frozen,
            members=(*frozen.members, replace(frozen.members[0], station_id=second_id)),
        )
        claim, member, prior = _prepare(isolated_committed_db_engine, (key, frozen))
        accepted = _forecasts(claim, prior)
        assert claim.frozen.artifact_id is not None
        station = StationForecastResult(
            station_id=member.station_id,
            model_id=claim.key.model_id,
            artifact_id=claim.frozen.artifact_id,
            forecasts=list(accepted),
            new_state=None,
            ensembles={f.ensemble.parameter: f.ensemble for f in accepted},
        )
        payload = RejectedAssignmentPayload(
            station_id=second_id,
            model_id=claim.key.model_id,
            model_artifact_id=claim.frozen.artifact_id,
            issued_at=claim.key.context.issue_time,
            group_id=claim.key.group_id,
            parameters=tuple(
                RejectedParameterPayload(
                    ensemble=replace(e.ensemble, station_id=second_id),
                    qc_status=e.qc_status,
                    qc_flags=e.qc_flags,
                )
                for e in prior
            ),
        )

        def fail_after_insert(
            conn: sa.Connection,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            if statement.startswith("INSERT INTO rejected_forecasts"):
                raise RuntimeError("fixture rejected body failed after insert")

        @contextmanager
        def rejection_transaction() -> Iterator[sa.Connection]:
            with isolated_committed_db_engine.begin() as txn:
                event.listen(txn, "after_cursor_execute", fail_after_insert)
                yield txn

        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        with (
            isolated_committed_db_engine.connect() as conn,
            pytest.raises(StoreError, match="earlier daily output") as failure,
        ):
            complete_daily_group(
                claim=claim,
                result=GroupForecastOutcome(
                    results={member.station_id: station}, rejected=(payload,)
                ),
                exclusions=(),
                provenance={
                    member.station_id: member.provenance,
                    second_id: member.provenance,
                },
                forecast_writer=PgCommittedForecastWriter(
                    isolated_committed_db_engine.begin
                ),
                rejected_writer=PgRejectedForecastStore(
                    conn, transaction_factory=rejection_transaction
                ),
                store=store,
                clock=lambda: NOW,
            )
        assert not isinstance(failure.value, DailyOutputRolledBackError)
        assert isinstance(failure.value.__cause__, DailyOutputRolledBackError)
        with isolated_committed_db_engine.connect() as reader:
            assert reader.scalar(
                sa.select(sa.func.count())
                .select_from(forecasts)
                .where(forecasts.c.station_id == member.station_id)
            ) == len(accepted)
            assert (
                reader.scalar(
                    sa.select(sa.func.count())
                    .select_from(rejected_forecasts)
                    .where(rejected_forecasts.c.station_id == second_id)
                )
                == 0
            )
        snapshot = store.read_day(claim.key)
        assert snapshot.latest is not None
        assert snapshot.latest.state is DailyAttemptState.ACTIVE
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
