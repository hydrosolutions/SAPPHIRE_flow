from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptDecision
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_recovery import (
    ConfirmedDailyTermination,
    DailyRecoveryStatus,
    DailyWriterState,
)
from sapphire_flow.types.enums import FlowRunState
from sapphire_flow.types.ids import PrincipalId
from tests.integration.store.test_daily_forecast_claim_store import (
    NOW,
    _later,
)
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)


def _confirmation(claim: DailyClaim) -> ConfirmedDailyTermination:
    return ConfirmedDailyTermination(
        attempt_id=claim.id,
        owner_id=claim.owner_id,
        operator=PrincipalId("fixture-operator"),
        confirmed_at=NOW,
        run_state=FlowRunState.CANCELLED,
        writer_state=DailyWriterState.STOPPED,
    )


class TestDailyRecovery:
    def test_abandonment_is_audited_and_allows_only_the_existing_retry(
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
        store.abandon(claim, _confirmation(claim))
        recovered_day = store.read_day(key)
        assert recovered_day.latest is not None
        assert recovered_day.latest.recovery == _confirmation(claim)
        assert recovered_day.recovery_status is DailyRecoveryStatus.OPERATOR_RECOVERED
        retry = store.claim(
            _later(key), frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(retry, DailyClaim)
        assert retry.parent_id == claim.id
        assert store.recovery_status(key) is DailyRecoveryStatus.OPERATOR_RECOVERED
        with isolated_committed_db_engine.connect() as conn:
            recovery = conn.execute(
                sa.text("SELECT recovery FROM daily_forecast_attempts WHERE id = :id"),
                {"id": claim.id},
            ).scalar_one()
        assert recovery["operator"] == "fixture-operator"
        assert recovery["writer_state"] == "stopped"
        with pytest.raises(ValueError, match="daily"):
            store.finalize(claim, outcomes=frozen.members, evidence=(), finished_at=NOW)

    @pytest.mark.parametrize(
        "writer", [DailyWriterState.ACTIVE, DailyWriterState.UNKNOWN]
    )
    def test_run_termination_alone_is_not_enough(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        writer: DailyWriterState,
    ) -> None:
        key, frozen = assignment
        claim = PgDailyForecastStore(isolated_committed_db_engine.begin).claim(
            key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(claim, DailyClaim)
        with pytest.raises(ValueError, match="stopped"):
            replace(_confirmation(claim), writer_state=writer)

    def test_wrong_owner_confirmation_cannot_abandon(
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
        with pytest.raises(ValueError, match="exact claim"):
            store.abandon(claim, replace(_confirmation(claim), owner_id=uuid4()))

    def test_running_prefect_owner_cannot_be_confirmed_stopped(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        claim = PgDailyForecastStore(isolated_committed_db_engine.begin).claim(
            key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(claim, DailyClaim)
        with pytest.raises(ValueError, match="terminal"):
            replace(_confirmation(claim), run_state=FlowRunState.RUNNING)

    def test_recovery_does_not_reset_retry_budget_or_leak_scope(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        first = store.claim(
            key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(first, DailyClaim)
        store.abandon(first, _confirmation(first))
        retry = store.claim(
            _later(key), frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(retry, DailyClaim)
        store.abandon(retry, _confirmation(retry))
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
        assert (
            store.recovery_status(replace(key, deployment="other"))
            is DailyRecoveryStatus.UNTOUCHED
        )
