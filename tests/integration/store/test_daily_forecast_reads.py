from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.daily_forecast import DailyAttemptState
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    DailyFailureReason,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberReason,
    DailyMemberState,
)
from sapphire_flow.types.ids import StationId, TenantId
from tests.conftest import make_station_config
from tests.integration.store.test_daily_forecast_claim_store import (
    NOW,
    _later,
)
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_daily_forecast_finalization import _prepare


class TestDailyReads:
    def test_missing_day_is_explicitly_unattempted(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, _ = assignment
        day = PgDailyForecastStore(isolated_committed_db_engine.begin).read_day(key)
        assert day.latest is None
        assert day.authoritative is None
        assert (
            PgDailyForecastStore(isolated_committed_db_engine.begin).read_days_bounded(
                (key,)
            )[key]
            == day
        )

    def test_active_retry_does_not_replace_prior_coherent_summary(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        sid = StationId(uuid4())
        with isolated_committed_db_engine.begin() as conn:
            PgStationStore(conn).store_station(
                make_station_config(
                    station_id=sid, code=f"read-{sid}", tenant_id=key.tenant_id
                )
            )
        sibling = replace(frozen.members[0], station_id=sid)
        frozen = replace(frozen, members=(*frozen.members, sibling))
        first, produced, evidence = _prepare(
            isolated_committed_db_engine, (key, frozen)
        )
        failed = replace(
            sibling,
            state=DailyMemberState.MODEL_FAILED,
            reason=DailyMemberReason.MODEL_FAILURE,
        )
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        store.finalize(
            first, outcomes=(produced, failed), evidence=evidence, finished_at=NOW
        )
        retry = store.claim(
            _later(key),
            first.frozen,
            attempt_id=uuid4(),
            owner_id=uuid4(),
            started_at=NOW,
        )
        assert isinstance(retry, DailyClaim)
        day = store.read_day(key)
        assert store.read_days_bounded((key,))[key] == day
        assert day.latest is not None and day.latest.claim.id == retry.id
        assert day.latest.state is DailyAttemptState.ACTIVE
        assert day.authoritative is not None and day.authoritative.claim.id == first.id
        assert day.authoritative.outcomes == (produced, failed)
        assert day.authoritative.claim.frozen == first.frozen
        # A finalized retry replaces the whole summary, not each member's best result.
        now_failed = replace(
            first.frozen.members[0],
            state=DailyMemberState.MODEL_FAILED,
            reason=DailyMemberReason.MODEL_FAILURE,
        )
        store.finalize(
            retry, outcomes=(now_failed, failed), evidence=(), finished_at=NOW
        )
        day = store.read_day(key)
        assert day.authoritative is not None and day.authoritative.claim.id == retry.id
        assert day.authoritative.outcomes == (now_failed, failed)
        assert store.read_days_bounded((key,))[key] == day

    def test_shared_failure_has_no_authoritative_output(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        claim, _, _ = _prepare(isolated_committed_db_engine, assignment)
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        store.fail(
            claim, reason=DailyFailureReason.SHARED_INPUT_FAILURE, finished_at=NOW
        )
        day = store.read_day(claim.key)
        assert day.authoritative is None
        assert (
            day.latest is not None
            and day.latest.failure_reason is DailyFailureReason.SHARED_INPUT_FAILURE
        )
        assert day.latest.outcomes is None

    def test_discovery_keeps_unfinished_and_retryable_records_without_assignments(
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
        assert store.pending_keys(
            key.tenant_id, key.deployment, _later(key).context
        ) == (_later(key),)
        store.fail(
            first, reason=DailyFailureReason.CONFIGURATION_CHANGED, finished_at=NOW
        )
        assert store.pending_keys(key.tenant_id, key.deployment, key.context) == ()
        assert store.pending_keys(
            key.tenant_id, key.deployment, _later(key).context
        ) == (_later(key),)
        retry = store.claim(
            _later(key), frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW
        )
        assert isinstance(retry, DailyClaim)
        store.fail(
            retry, reason=DailyFailureReason.CONFIGURATION_CHANGED, finished_at=NOW
        )
        assert (
            store.pending_keys(key.tenant_id, key.deployment, _later(key, 12).context)
            == ()
        )

    def test_read_and_discovery_do_not_cross_deployment_scope(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        store.claim(key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW)
        assert store.read_day(replace(key, deployment="other")).latest is None
        assert store.pending_keys(key.tenant_id, "other", _later(key).context) == ()

    def test_read_and_discovery_do_not_cross_tenant_scope(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    ) -> None:
        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        store.claim(key, frozen, attempt_id=uuid4(), owner_id=uuid4(), started_at=NOW)
        other_tenant = TenantId(uuid4())
        assert store.read_day(replace(key, tenant_id=other_tenant)).latest is None
        assert (
            store.pending_keys(other_tenant, key.deployment, _later(key).context) == ()
        )


class TestBoundedDailyRead:
    @pytest.mark.parametrize("oversize", ["bytes", "members"])
    def test_admission_precedes_snapshot_materialization(
        self,
        isolated_committed_db_engine: sa.Engine,
        assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
        monkeypatch: pytest.MonkeyPatch,
        oversize: str,
    ) -> None:
        from datetime import timedelta

        from sapphire_flow.db.metadata import daily_forecast_attempts
        from sapphire_flow.exceptions import DailyAuthorityIntegrityError
        from sapphire_flow.store import daily_forecast_store
        from sapphire_flow.types.daily_forecast import ScheduledDailyForecast
        from sapphire_flow.types.datetime import ensure_utc

        key, frozen = assignment
        store = PgDailyForecastStore(isolated_committed_db_engine.begin)
        attempt_id = uuid4()
        store.claim(
            key, frozen, attempt_id=attempt_id, owner_id=uuid4(), started_at=NOW
        )
        next_issue = ensure_utc(NOW + timedelta(days=1))
        with isolated_committed_db_engine.begin() as conn:
            row = dict(
                conn.execute(
                    sa.select(daily_forecast_attempts).where(
                        daily_forecast_attempts.c.id == attempt_id
                    )
                )
                .mappings()
                .one()
            )
            row.update(
                id=uuid4(),
                issued_at=next_issue,
                scheduled_tick=next_issue,
                started_at=next_issue,
                summary=sa.null(),
                recovery=sa.null(),
            )
            # Corrupt oversized persisted evidence must be refused before decoding.
            if oversize == "bytes":
                row["output_contracts"] = [{"parameter": "x" * (8 * 1024 * 1024)}]
            else:
                row["roster"] = row["roster"] * 25001
            conn.execute(sa.insert(daily_forecast_attempts).values(**row))

        def forbidden(row: object) -> None:
            pytest.fail("oversized daily evidence reached snapshot decoder")

        monkeypatch.setattr(daily_forecast_store, "_snapshot", forbidden)
        next_key = replace(
            key, context=ScheduledDailyForecast(scheduled_tick=next_issue)
        )
        with pytest.raises(DailyAuthorityIntegrityError, match="admission exceeded"):
            store.read_days_bounded((next_key,))
