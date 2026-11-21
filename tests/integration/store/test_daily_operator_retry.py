from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import daily_forecast_attempts
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptDecision, DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    DailyFailureReason,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_operator_retry import (
    OperatorAdmissionState,
    OperatorDailyForecast,
    OperatorRetryRequest,
    roster_sha256,
)
from sapphire_flow.types.ids import PrincipalId
from tests.integration.store.test_daily_forecast_claim_store import NOW, _later
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)


def exhausted(
    store: PgDailyForecastStore, assignment: tuple[DailyClaimKey, FrozenDailyAssignment]
) -> OperatorRetryRequest:
    key, frozen = assignment
    parent = None
    for scoped in (key, _later(key)):
        parent = store.claim(
            scoped, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(parent, DailyClaim)
        store.fail(parent, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    assert parent is not None and frozen.artifact_id is not None
    return OperatorRetryRequest(
        request_id=uuid4(),
        parent_id=parent.id,
        key=replace(key, context=OperatorDailyForecast(target_date=NOW.date())),
        artifact_id=frozen.artifact_id,
        artifact_sha256="c" * 64,
        configuration_sha256=frozen.configuration_sha256,
        roster_sha256=roster_sha256(frozen),
        actor=PrincipalId("operator"),
        reason="Deploy repaired runtime",
    )


def test_legacy_scheduled_reader_preserves_ordinal_without_matching_parent_link(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    key, frozen = assignment
    first = store.claim(
        key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
    )
    assert isinstance(first, DailyClaim)
    store.fail(first, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    second_id = uuid4()
    with isolated_committed_db_engine.begin() as conn:
        row = dict(
            conn.execute(
                sa.select(daily_forecast_attempts).where(
                    daily_forecast_attempts.c.id == first.id
                )
            )
            .mappings()
            .one()
        )
        legacy_parent = uuid4()
        row.update(summary=sa.null(), recovery=sa.null())
        conn.execute(
            daily_forecast_attempts.insert().values(
                **{
                    **row,
                    "id": legacy_parent,
                    "issued_at": NOW - timedelta(days=1),
                    "scheduled_tick": NOW - timedelta(days=1),
                }
            )
        )
        row.update(
            id=second_id,
            ordinal=2,
            parent_id=legacy_parent,
            scheduled_tick=NOW + timedelta(hours=6),
        )
        conn.execute(daily_forecast_attempts.insert().values(**row))
    day = store.read_day(key)
    assert day.latest is not None
    assert day.latest.claim.id == second_id
    assert day.authoritative is None
    assert store.read_days_bounded((key,))[key] == day


def test_operator_claim_preserves_automatic_budget_and_is_idempotent(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    request = exhausted(store, assignment)
    key, frozen = assignment
    current = replace(frozen, configuration_sha256="b" * 64)
    request = replace(request, configuration_sha256=current.configuration_sha256)
    admitted = store.claim_operator_retry(
        request,
        current,
        owner_id=uuid4(),
        started_at=NOW,
        software_version="test-new-runtime",
    )
    assert admitted.admission is OperatorAdmissionState.CREATED
    assert admitted.claim.parent_configuration_sha256 == frozen.configuration_sha256
    assert (
        store.claim(
            _later(key, 18),
            frozen,
            attempt_id=uuid4(),
            owner_id=uuid4(),
            started_at=NOW,
        )
        is DailyAttemptDecision.OWNED
    )
    duplicate = store.claim_operator_retry(
        request,
        current,
        owner_id=uuid4(),
        started_at=NOW,
        software_version="different-runtime",
    )
    assert duplicate.admission is OperatorAdmissionState.EXISTING
    assert duplicate.claim == admitted.claim
    store.fail(admitted.claim, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    duplicate = store.claim_operator_retry(
        request, current, owner_id=uuid4(), started_at=NOW, software_version="test"
    )
    assert duplicate.state is DailyAttemptState.FAILED
    assert duplicate.admission is OperatorAdmissionState.EXISTING
    assert (
        store.claim(
            _later(key, 18),
            frozen,
            attempt_id=uuid4(),
            owner_id=uuid4(),
            started_at=NOW,
        )
        is DailyAttemptDecision.EXHAUSTED
    )
    day = store.read_day(key)
    assert day.latest is not None and day.latest.claim == admitted.claim
    assert day.authoritative is None
    assert store.read_days_bounded((key,))[key] == day
    with pytest.raises(ValueError, match="different payload"):
        store.claim_operator_retry(
            replace(request, reason="different"),
            current,
            owner_id=uuid4(),
            started_at=NOW,
            software_version="test",
        )


def test_two_distinct_requests_cannot_fork_same_parent(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    request = exhausted(store, assignment)
    barrier = Barrier(2)

    def claim(index: int) -> str:
        barrier.wait(timeout=10)
        try:
            store.claim_operator_retry(
                replace(request, request_id=uuid4()),
                assignment[1],
                owner_id=uuid4(),
                started_at=NOW,
                software_version="test",
            )
            return "created"
        except ValueError as exc:
            assert "latest attempt" in str(exc) or "active owner" in str(exc)
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == ["created", "refused"]


@pytest.mark.parametrize("change", ["day", "configuration", "roster", "artifact_hash"])
def test_stale_or_changed_request_refused_before_admission(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    change: str,
) -> None:
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    request = exhausted(store, assignment)
    now = NOW
    if change == "day":
        now += timedelta(days=1)
    elif change == "configuration":
        request = replace(request, configuration_sha256="b" * 64)
    elif change == "roster":
        request = replace(request, roster_sha256="b" * 64)
    else:
        request = replace(request, artifact_sha256="b" * 64)
    with pytest.raises(ValueError, match="today|stale|cannot change"):
        store.claim_operator_retry(
            request,
            assignment[1],
            owner_id=uuid4(),
            started_at=now,
            software_version="test",
        )
    assert store.read_day(assignment[0]).latest.claim.id == request.parent_id


def test_operator_chain_keeps_prior_requests_immutable(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    request = exhausted(store, assignment)
    first = store.claim_operator_retry(
        request,
        assignment[1],
        owner_id=uuid4(),
        started_at=NOW,
        software_version="test",
    )
    store.fail(first.claim, reason=DailyFailureReason.MODEL_FAILURE, finished_at=NOW)
    second_request = replace(request, request_id=uuid4(), parent_id=first.claim.id)
    second = store.claim_operator_retry(
        second_request,
        assignment[1],
        owner_id=uuid4(),
        started_at=NOW,
        software_version="test",
    )
    day = store.read_day(assignment[0])
    assert day.latest.claim.id == second.claim.id
    assert store.read_days_bounded((assignment[0],))[assignment[0]] == day
    with (
        isolated_committed_db_engine.begin() as conn,
        pytest.raises(sa.exc.DBAPIError, match="immutable"),
    ):
        conn.execute(
            sa.text(
                "UPDATE daily_forecast_attempts SET operator_metadata='{}' WHERE id=:id"
            ),
            {"id": first.claim.id},
        )


def test_operator_crash_requires_confirmed_writer_termination(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
) -> None:
    from sapphire_flow.types.daily_forecast_recovery import (
        ConfirmedDailyTermination,
        DailyWriterState,
    )
    from sapphire_flow.types.enums import FlowRunState

    store = PgDailyForecastStore(isolated_committed_db_engine.begin)
    request = exhausted(store, assignment)
    admitted = store.claim_operator_retry(
        request,
        assignment[1],
        owner_id=uuid4(),
        started_at=NOW,
        software_version="test",
    )
    confirmation = ConfirmedDailyTermination(
        attempt_id=admitted.claim.id,
        owner_id=admitted.claim.owner_id,
        operator=PrincipalId("operator"),
        confirmed_at=NOW,
        run_state=FlowRunState.CRASHED,
        writer_state=DailyWriterState.STOPPED,
    )
    with pytest.raises(ValueError, match="writers must be confirmed stopped"):
        replace(confirmation, writer_state=DailyWriterState.UNKNOWN)
    assert store.read_day(assignment[0]).latest.state is DailyAttemptState.ACTIVE
    store.abandon(admitted.claim, confirmation)
    day = store.read_day(assignment[0])
    assert day.latest.state is DailyAttemptState.ABANDONED
    assert store.read_days_bounded((assignment[0],))[assignment[0]] == day
