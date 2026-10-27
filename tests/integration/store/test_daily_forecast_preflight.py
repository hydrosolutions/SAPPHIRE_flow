# ruff: noqa: F811 -- imported pytest fixture.
from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.services.daily_forecast_preflight import claim_daily_candidates
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    DailyFailureReason,
    FrozenDailyAssignment,
)
from tests.integration.store.test_daily_forecast_claim_store import (  # noqa: F401
    NOW,
    _later,
    assignment,
)


class TestClaimDailyCandidates:
    def test_claims_full_candidate_roster(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        claims = claim_daily_candidates(
            tenant_id=key.tenant_id,
            deployment=key.deployment,
            context=key.context,
            candidates={key: frozen},
            store=store,
            owner_id=uuid4(),
            clock=lambda: NOW,
            id_gen=uuid4,
        )
        assert len(claims) == 1
        assert claims[0].frozen == frozen

    @pytest.mark.parametrize(
        "change", ["vanished", "configuration", "roster", "unavailable"]
    )
    def test_retry_preserves_roster_or_records_configuration_failure(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        change: str,
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        first = store.claim(
            key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(first, DailyClaim)
        store.fail(
            first, reason=DailyFailureReason.SHARED_INPUT_FAILURE, finished_at=NOW
        )
        later = _later(key)
        current = (
            replace(frozen, members=())
            if change == "roster"
            else replace(frozen, configuration_sha256="b" * 64)
        )
        candidates = {} if change in ("vanished", "unavailable") else {later: current}
        claims = claim_daily_candidates(
            tenant_id=key.tenant_id,
            deployment=key.deployment,
            context=later.context,
            candidates=candidates,
            unavailable_group_ids=(
                frozenset({key.group_id}) if change == "unavailable" else frozenset()
            ),
            store=store,
            owner_id=uuid4(),
            clock=lambda: later.context.scheduled_tick,
            id_gen=uuid4,
        )
        latest = store.read_day(later).latest
        assert latest is not None
        if change == "unavailable":
            assert claims == ()
            assert latest.claim == first
            assert latest.failure_reason is DailyFailureReason.SHARED_INPUT_FAILURE
        elif change == "roster":
            assert len(claims) == 1
            assert claims[0].frozen.members == frozen.members
        else:
            assert claims == ()
            assert latest.failure_reason is DailyFailureReason.CONFIGURATION_CHANGED

    def test_vanished_active_assignment_is_not_reclaimed(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        first = store.claim(
            key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        later = _later(key)
        assert (
            claim_daily_candidates(
                tenant_id=key.tenant_id,
                deployment=key.deployment,
                context=later.context,
                candidates={},
                store=store,
                owner_id=uuid4(),
                clock=lambda: later.context.scheduled_tick,
                id_gen=uuid4,
            )
            == ()
        )
        latest = store.read_day(key).latest
        assert latest is not None
        assert latest.claim == first
        assert latest.state is DailyAttemptState.ACTIVE

    def test_foreign_scope_is_rejected_before_claiming(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        with pytest.raises(ValueError, match="scope"):
            claim_daily_candidates(
                tenant_id=key.tenant_id,
                deployment="other",
                context=key.context,
                candidates={key: frozen},
                store=store,
                owner_id=uuid4(),
                clock=lambda: NOW,
                id_gen=uuid4,
            )
        assert store.read_day(key).latest is None

    def test_missing_artifact_is_recorded_without_runnable_claim(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        frozen = replace(frozen, artifact_id=None)
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        claims = claim_daily_candidates(
            tenant_id=key.tenant_id,
            deployment=key.deployment,
            context=key.context,
            candidates={key: frozen},
            store=store,
            owner_id=uuid4(),
            clock=lambda: NOW,
            id_gen=uuid4,
        )
        assert claims == ()
        latest = store.read_day(key).latest
        assert latest is not None
        assert latest.claim.frozen.members == frozen.members
        assert latest.claim.frozen.artifact_id is None
        assert latest.failure_reason is DailyFailureReason.CONFIGURATION_UNAVAILABLE

    def test_missing_artifact_cannot_finalize_outputs(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        claim = store.claim(
            key,
            replace(frozen, artifact_id=None),
            attempt_id=uuid4(),
            owner_id=uuid4(),
            started_at=NOW,
        )
        assert isinstance(claim, DailyClaim)
        with pytest.raises(ValueError, match="artifact"):
            store.finalize(claim, outcomes=(), evidence=(), finished_at=NOW)
        with (
            pytest.raises(sa.exc.IntegrityError, match="ck_daily_artifact"),
            isolated_committed_db_engine.begin() as txn,
        ):
            txn.execute(
                sa.text(
                    "UPDATE daily_forecast_attempts SET state = 'complete', "
                    "finished_at = :now, summary = '[]'::jsonb WHERE id = :id"
                ),
                {"now": NOW, "id": claim.id},
            )


class TestClaimAcknowledgement:
    def test_lost_ack_does_not_grant_ownership(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        claim = store.claim
        received: list[DailyClaim] = []

        def lose_ack(*args: object, **kwargs: object) -> None:
            claim(*args, **kwargs)
            raise ConnectionError("lost acknowledgement")

        monkeypatch.setattr(store, "claim", lose_ack)
        with pytest.raises(ConnectionError, match="acknowledgement"):
            claim_daily_candidates(
                tenant_id=key.tenant_id,
                deployment=key.deployment,
                context=key.context,
                candidates={key: frozen},
                store=store,
                owner_id=uuid4(),
                clock=lambda: NOW,
                id_gen=uuid4,
                on_claim=received.append,
            )
        assert received == []
        monkeypatch.setattr(store, "claim", claim)
        later = _later(key)
        assert (
            claim_daily_candidates(
                tenant_id=key.tenant_id,
                deployment=key.deployment,
                context=later.context,
                candidates={later: frozen},
                store=store,
                owner_id=uuid4(),
                clock=lambda: later.context.scheduled_tick,
                id_gen=uuid4,
            )
            == ()
        )
        day = store.read_day(key)
        assert day.latest is not None
        assert day.latest.state is DailyAttemptState.ACTIVE


class TestIsolatedAdmission:
    @pytest.mark.parametrize("invalid_first", [True, False])
    def test_invalid_group_cannot_consume_healthy_attempt(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        invalid_first: bool,
    ) -> None:
        from uuid import UUID

        from sapphire_flow.types.ids import StationGroupId

        key, frozen = assignment
        bad = replace(
            key,
            group_id=StationGroupId(UUID(int=0 if invalid_first else (1 << 128) - 1)),
        )
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        errors: list[tuple[DailyClaimKey, UUID | None, Exception]] = []
        claims = claim_daily_candidates(
            tenant_id=key.tenant_id,
            deployment=key.deployment,
            context=key.context,
            candidates={bad: frozen, key: frozen},
            store=store,
            owner_id=uuid4(),
            clock=lambda: NOW,
            id_gen=uuid4,
            on_error=lambda key, attempt_id, exc: errors.append((key, attempt_id, exc)),
        )
        assert len(claims) == 1 and claims[0].key == key
        assert claims[0].ordinal.value == 1
        assert len(errors) == 1 and errors[0][0] == bad
        assert store.read_day(bad).latest is None
        assert store.read_day(key).latest.state is DailyAttemptState.ACTIVE
