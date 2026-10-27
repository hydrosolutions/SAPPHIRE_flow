from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptState, OneShotForecastTrigger
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    DailyFailureReason,
    FrozenDailyAssignment,
)
from tests.integration.db.test_role_bootstrap import (
    bootstrapped as bootstrapped,
)
from tests.integration.db.test_role_bootstrap import (
    role_harness as role_harness,
)
from tests.integration.store.test_daily_forecast_claim_store import (
    NOW,
)
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_daily_forecast_finalization import _prepare
from tests.integration.store.test_daily_forecast_one_shot import (
    one_shot_key,
    prepare_one_shot,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


@pytest.fixture
def isolated_committed_db_engine(bootstrapped: _RoleBootstrapHarness) -> sa.Engine:
    return bootstrapped.owner_engine


@pytest.fixture
def worker_engine(bootstrapped: _RoleBootstrapHarness) -> Iterator[sa.Engine]:
    engine = sa.create_engine(
        bootstrapped.role_url("sapphire_worker", "worker-pw-initial")
    )
    try:
        yield engine
    finally:
        engine.dispose()


def test_worker_claim_fail_and_api_snapshot(
    bootstrapped: _RoleBootstrapHarness,
    worker_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    key, frozen = assignment
    worker = PgDailyForecastStore(worker_engine.begin)
    claim = worker.claim(
        key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    assert isinstance(claim, DailyClaim)
    worker.fail(claim, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    api = sa.create_engine(bootstrapped.role_url("sapphire_api", "api-pw-initial"))
    try:
        snapshot = PgDailyForecastStore(api.begin).read_day(key)
        assert snapshot.latest is not None
        assert snapshot.latest.state is DailyAttemptState.FAILED
        assert snapshot.latest.claim == claim
    finally:
        api.dispose()


def test_worker_finalizes_committed_rejected_outputs(
    worker_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    claim, outcome, evidence = _prepare(worker_engine, assignment)
    worker = PgDailyForecastStore(worker_engine.begin)
    assert (
        worker.finalize(claim, outcomes=(outcome,), evidence=evidence, finished_at=NOW)
        is DailyAttemptState.COMPLETE
    )
    assert worker.read_day(claim.key).authoritative is not None


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO daily_forecast_attempts (id) VALUES (gen_random_uuid())",
        "UPDATE daily_forecast_attempts SET state = 'failed'",
        "DELETE FROM daily_forecast_attempts",
    ],
)
def test_api_daily_writes_are_permission_denied(
    bootstrapped: _RoleBootstrapHarness,
    sql: str,
) -> None:
    engine = sa.create_engine(bootstrapped.role_url("sapphire_api", "api-pw-initial"))
    try:
        with (
            engine.begin() as conn,
            pytest.raises(sa.exc.ProgrammingError, match="permission denied"),
        ):
            conn.execute(sa.text(sql))
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE daily_forecast_attempts SET recovery = '{}'::jsonb",
        "INSERT INTO daily_forecast_attempts (id, recovery) "
        "VALUES (gen_random_uuid(), '{}'::jsonb)",
        "UPDATE daily_forecast_attempts SET owner_id = gen_random_uuid()",
        "DELETE FROM daily_forecast_attempts",
        "TRUNCATE daily_forecast_attempts",
    ],
)
def test_worker_cannot_recover_reassign_or_remove(
    worker_engine: sa.Engine,
    sql: str,
) -> None:
    with (
        worker_engine.begin() as conn,
        pytest.raises(sa.exc.ProgrammingError, match="permission denied"),
    ):
        conn.execute(sa.text(sql))


def test_rebootstrap_removes_stale_daily_column_writes(
    bootstrapped: _RoleBootstrapHarness,
) -> None:
    with bootstrapped.owner_engine.begin() as conn:
        conn.execute(
            sa.text(
                "GRANT UPDATE (recovery), INSERT (recovery) ON daily_forecast_attempts "
                "TO PUBLIC, sapphire_api, sapphire_worker"
            )
        )
    result = bootstrapped.run_bootstrap("api-pw-initial", "worker-pw-initial")
    assert result.returncode == 0, result.stderr
    with bootstrapped.owner_engine.connect() as conn:
        for role in ("sapphire_api", "sapphire_worker", "sapphire_operator"):
            for privilege in ("INSERT", "UPDATE"):
                assert (
                    conn.scalar(
                        sa.text(
                            "SELECT has_column_privilege(:role, "
                            "'daily_forecast_attempts', 'recovery', :privilege)"
                        ),
                        {"role": role, "privilege": privilege},
                    )
                    is False
                )


def test_worker_cannot_abandon_without_recovery_evidence(
    worker_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    key, frozen = assignment
    claim = PgDailyForecastStore(worker_engine.begin).claim(
        key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    assert isinstance(claim, DailyClaim)
    with (
        worker_engine.begin() as conn,
        pytest.raises(sa.exc.IntegrityError, match="ck_daily_terminal"),
    ):
        conn.execute(
            sa.text(
                "UPDATE daily_forecast_attempts SET state = 'abandoned', "
                "finished_at = :finished WHERE id = :id"
            ),
            {"finished": NOW, "id": claim.id},
        )
    latest = PgDailyForecastStore(worker_engine.begin).read_day(key).latest
    assert latest is not None
    assert latest.state is DailyAttemptState.ACTIVE


@pytest.mark.parametrize("trigger", list(OneShotForecastTrigger))
def test_worker_one_shot_insert_and_fail(
    worker_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    trigger: OneShotForecastTrigger,
) -> None:
    key, frozen = assignment
    store = PgDailyForecastStore(worker_engine.begin)
    execution = store.start_one_shot(
        one_shot_key(key, trigger),
        frozen,
        execution_id=uuid4(),
        owner_id=uuid4(),
        started_at=NOW,
    )
    store.fail(execution, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    snapshot = store.read_one_shot(execution.key, execution_id=execution.id)
    assert snapshot is not None
    assert snapshot.state is DailyAttemptState.FAILED
    assert store.read_day(key).latest is None


def test_worker_one_shot_finalizes_committed_outputs(
    worker_engine: sa.Engine, assignment: tuple[DailyClaimKey, FrozenDailyAssignment]
) -> None:
    execution, outcome, evidence = prepare_one_shot(worker_engine, assignment)
    assert (
        PgDailyForecastStore(worker_engine.begin).finalize(
            execution, outcomes=(outcome,), evidence=evidence, finished_at=NOW
        )
        is DailyAttemptState.COMPLETE
    )
