from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import model_artifacts, models, station_groups, tenants
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.daily_forecast import (
    DailyAttemptDecision,
    DailyAttemptOrdinal,
    ScheduledDailyForecast,
)
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    DailyFailureReason,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberState,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import (
    ArtifactId,
    ModelId,
    StationGroupId,
    StationId,
    TenantId,
)
from tests.conftest import make_station_config

NOW = ensure_utc(datetime(2026, 10, 26, tzinfo=UTC))


@pytest.fixture
def assignment(
    isolated_committed_db_engine: sa.Engine,
) -> tuple[DailyClaimKey, FrozenDailyAssignment]:
    tenant, group, artifact = (
        TenantId(uuid4()),
        StationGroupId(uuid4()),
        ArtifactId(uuid4()),
    )
    station_id = StationId(uuid4())
    model = ModelId(f"daily-{uuid4()}")
    with isolated_committed_db_engine.begin() as conn:
        conn.execute(
            sa.insert(tenants).values(id=tenant, code=str(tenant), name="Daily test")
        )
        PgStationStore(conn).store_station(
            make_station_config(
                station_id=station_id, code=f"daily-{station_id}", tenant_id=tenant
            )
        )
        conn.execute(
            sa.insert(station_groups).values(
                id=group, tenant_id=tenant, name=str(group)
            )
        )
        conn.execute(
            sa.insert(models).values(
                id=model,
                display_name="Daily test",
                artifact_scope="group",
                description="test",
            )
        )
        conn.execute(
            sa.insert(model_artifacts).values(
                id=artifact,
                model_id=model,
                group_id=group,
                status="active",
                artifact_path="fixture",
                sha256_hash="c" * 64,
                training_period_start=NOW,
                training_period_end=NOW,
                trained_at=NOW,
            )
        )
    return (
        DailyClaimKey(
            tenant_id=tenant,
            deployment="test",
            group_id=group,
            model_id=model,
            context=ScheduledDailyForecast(scheduled_tick=NOW),
        ),
        FrozenDailyAssignment(
            artifact_id=artifact,
            configuration_sha256="a" * 64,
            members=(
                DailyMemberOutcome(
                    station_id=station_id,
                    required_parameters=("discharge", "water_level"),
                    state=DailyMemberState.PENDING,
                ),
            ),
        ),
    )


def _later(key: DailyClaimKey, hour: int = 6) -> DailyClaimKey:
    return replace(
        key,
        context=ScheduledDailyForecast(
            scheduled_tick=ensure_utc(NOW.replace(hour=hour))
        ),
    )


class TestDailyClaims:
    def test_two_connections_cannot_claim_same_day(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        barrier = Barrier(2)

        def claim() -> DailyClaim | DailyAttemptDecision:
            barrier.wait(timeout=10)
            return PgDailyForecastStore(isolated_committed_db_engine.begin).claim(
                key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        assert sum(isinstance(result, DailyClaim) for result in results) == 1
        assert DailyAttemptDecision.OWNED in results

    def test_active_owner_is_not_reclaimed_by_a_late_tick(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        store.claim(key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW)
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

    def test_failure_permits_only_one_later_retry_with_frozen_identity(
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
        store.fail(
            first, reason=DailyFailureReason.SHARED_INPUT_FAILURE, finished_at=NOW
        )
        assert (
            store.claim(
                key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
            )
            is DailyAttemptDecision.NOT_LATER
        )
        retry = store.claim(
            _later(key),
            replace(frozen, configuration_sha256="b" * 64, members=()),
            attempt_id=uuid4(),
            owner_id=uuid4(),
            started_at=NOW,
        )
        assert isinstance(retry, DailyClaim)
        assert retry.ordinal is DailyAttemptOrdinal.RETRY
        assert retry.parent_id == first.id
        assert retry.frozen == first.frozen
        store.fail(
            retry, reason=DailyFailureReason.CONFIGURATION_CHANGED, finished_at=NOW
        )
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

    def test_wrong_owner_cannot_release_claim(
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
        with pytest.raises(ValueError, match="active owned claim"):
            store.fail(
                replace(claim, owner_id=uuid4()),
                reason=DailyFailureReason.SHARED_INPUT_FAILURE,
                finished_at=NOW,
            )
        assert (
            store.claim(
                _later(key),
                frozen,
                attempt_id=uuid4(),
                owner_id=uuid4(),
                started_at=NOW,
            )
            is DailyAttemptDecision.OWNED
        )

    def test_finalized_failure_cannot_be_rewritten(
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
        store.fail(
            claim, reason=DailyFailureReason.SHARED_INPUT_FAILURE, finished_at=NOW
        )
        with pytest.raises(ValueError, match="active owned claim"):
            store.fail(
                claim, reason=DailyFailureReason.CONFIGURATION_CHANGED, finished_at=NOW
            )

    def test_cross_tenant_member_is_refused_without_spending_attempt(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        foreign = make_station_config(
            station_id=StationId(uuid4()), code=f"foreign-{uuid4()}"
        )
        with isolated_committed_db_engine.begin() as conn:
            PgStationStore(conn).store_station(foreign)
        wrong = replace(
            frozen, members=(replace(frozen.members[0], station_id=foreign.id),)
        )
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        with pytest.raises(ValueError, match="belong to tenant"):
            store.claim(
                key, wrong, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
            )
        result = store.claim(
            key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(result, DailyClaim)
        assert result.ordinal is DailyAttemptOrdinal.INITIAL

    @pytest.mark.parametrize("change", ["delete", "roster", "terminal"])
    def test_database_refuses_claim_erasure_or_rewrite(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        change: str,
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        result = store.claim(
            key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(result, DailyClaim)
        if change == "terminal":
            store.fail(
                result, reason=DailyFailureReason.SHARED_INPUT_FAILURE, finished_at=NOW
            )
        statements = {
            "delete": "DELETE FROM daily_forecast_attempts WHERE id = :id",
            "roster": (
                "UPDATE daily_forecast_attempts SET roster = '[]'::jsonb WHERE id = :id"
            ),
            "terminal": (
                "UPDATE daily_forecast_attempts SET failure_reason = 'model_failure' "
                "WHERE id = :id"
            ),
        }
        with (
            pytest.raises(sa.exc.DBAPIError, match="cannot be deleted|immutable"),
            isolated_committed_db_engine.begin() as conn,
        ):
            conn.execute(sa.text(statements[change]), {"id": result.id})

    def test_second_attempt_race_still_has_one_owner(
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
        store.fail(
            first, reason=DailyFailureReason.SHARED_INPUT_FAILURE, finished_at=NOW
        )
        barrier = Barrier(2)

        def retry() -> DailyClaim | DailyAttemptDecision:
            barrier.wait(timeout=10)
            return store.claim(
                _later(key),
                frozen,
                attempt_id=uuid4(),
                owner_id=uuid4(),
                started_at=NOW,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: retry(), range(2)))
        claimed = [result for result in results if isinstance(result, DailyClaim)]
        assert len(claimed) == 1
        assert claimed[0].ordinal is DailyAttemptOrdinal.RETRY
        assert DailyAttemptDecision.OWNED in results
