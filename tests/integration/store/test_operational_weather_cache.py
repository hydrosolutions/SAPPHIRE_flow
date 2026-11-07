from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.db.metadata import operational_weather_cache
from sapphire_flow.exceptions import ConfigurationError, TenantIsolationError
from sapphire_flow.store.operational_weather_cache import (
    PgCommittedOperationalWeatherCache,
    PgOperationalWeatherCache,
    WeatherCacheConflictError,
)
from sapphire_flow.store.operational_weather_codec import make_past_snapshot
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import StationId, TenantId
from sapphire_flow.types.operational_weather import (
    FutureWeatherAttempt,
    FutureWeatherReceipt,
    FutureWeatherRequirement,
    FutureWeatherScope,
    WeatherAttemptState,
    WeatherCacheAttempt,
    WeatherCacheCompletion,
    WeatherCacheEntry,
    WeatherCacheKey,
    WeatherFailureReason,
    WeatherLegOutcome,
    WeatherLegState,
)
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config
from tests.fakes.operational_weather import FINGERPRINT, ISSUE, make_operational_window
from tests.integration.committed_database import (
    isolated_committed_db_engine as isolated_committed_db_engine,
)


@pytest.fixture
def cache_key(db_connection: sa.Connection) -> WeatherCacheKey:
    station = make_station_config(
        station_id=StationId(uuid4()), code="CACHE-001", network="dhm"
    )
    PgStationStore(db_connection).store_station(station)
    return WeatherCacheKey(station_id=station.id, binding_fingerprint=FINGERPRINT)


@pytest.fixture
def principal(
    db_connection: sa.Connection, cache_key: WeatherCacheKey
) -> WritePrincipal:
    station = PgStationStore(db_connection).fetch_station(cache_key.station_id)
    assert station is not None
    return WritePrincipal(id=None, tenant_id=station.tenant_id)


def successful_completion(key: WeatherCacheKey) -> WeatherCacheCompletion:
    return WeatherCacheCompletion(
        finished_at=ISSUE,
        past_outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
        future_attempts=(),
        past_snapshot=make_past_snapshot(
            window=make_operational_window(key.station_id),
            retrieved_at=ISSUE,
            snapshot_id=uuid4(),
            max_bytes=2 * 1024 * 1024,
        ),
    )


class TestPgOperationalWeatherCache:
    def test_atomic_success_and_exact_terminal_retry(
        self,
        db_connection: sa.Connection,
        cache_key: WeatherCacheKey,
        principal: WritePrincipal,
    ) -> None:
        store = PgOperationalWeatherCache(db_connection)
        attempt = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
        completion = successful_completion(cache_key)
        entry = store.finish(
            attempt=attempt,
            principal=principal,
            completion=completion,
            earliest_cycle=ISSUE,
        )
        assert entry.state is WeatherAttemptState.COMPLETE
        assert store.fetch(cache_key) == entry
        assert (
            store.finish(
                attempt=attempt,
                principal=principal,
                completion=completion,
                earliest_cycle=ISSUE,
            )
            == entry
        )

    def test_contradictory_terminal_completion_is_refused(
        self,
        db_connection: sa.Connection,
        cache_key: WeatherCacheKey,
        principal: WritePrincipal,
    ) -> None:
        store = PgOperationalWeatherCache(db_connection)
        attempt = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
        entry = store.finish(
            attempt=attempt,
            principal=principal,
            completion=successful_completion(cache_key),
            earliest_cycle=ISSUE,
        )
        failure = WeatherCacheCompletion(
            finished_at=ISSUE,
            future_attempts=(),
            past_outcome=WeatherLegOutcome(
                state=WeatherLegState.UNAVAILABLE, reason=WeatherFailureReason.TRANSPORT
            ),
        )
        with pytest.raises(WeatherCacheConflictError, match="immutable"):
            store.finish(
                attempt=attempt,
                principal=principal,
                completion=failure,
                earliest_cycle=ISSUE,
            )
        assert store.fetch(cache_key) == entry

    def test_new_invocation_supersedes_pending_without_clock_abandonment(
        self,
        db_connection: sa.Connection,
        cache_key: WeatherCacheKey,
        principal: WritePrincipal,
    ) -> None:
        store = PgOperationalWeatherCache(db_connection)
        old = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
        assert store.fetch(cache_key).state is WeatherAttemptState.PENDING
        later = ensure_utc(ISSUE + timedelta(days=10))
        new = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=later,
            started_at=later,
        )
        assert new.generation == old.generation + 1
        with pytest.raises(WeatherCacheConflictError, match="superseded"):
            store.finish(
                attempt=old,
                principal=principal,
                completion=successful_completion(cache_key),
                earliest_cycle=ISSUE,
            )

    def test_failed_refresh_preserves_last_good_pair(
        self,
        db_connection: sa.Connection,
        cache_key: WeatherCacheKey,
        principal: WritePrincipal,
    ) -> None:
        store = PgOperationalWeatherCache(db_connection)
        first = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
        original = store.finish(
            attempt=first,
            principal=principal,
            completion=successful_completion(cache_key),
            earliest_cycle=ISSUE,
        )
        later = ensure_utc(ISSUE + timedelta(days=1))
        second = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=later,
            started_at=later,
        )
        failure = WeatherCacheCompletion(
            finished_at=later,
            future_attempts=(),
            past_outcome=WeatherLegOutcome(
                state=WeatherLegState.UNAVAILABLE,
                reason=WeatherFailureReason.MISSING_SUPPORT,
            ),
        )
        result = store.finish(
            attempt=second,
            principal=principal,
            completion=failure,
            earliest_cycle=ISSUE,
        )
        assert result.state is WeatherAttemptState.FAILED
        assert result.past_snapshot == original.past_snapshot

    def test_cross_tenant_principal_cannot_begin(
        self,
        db_connection: sa.Connection,
        cache_key: WeatherCacheKey,
        principal: WritePrincipal,
    ) -> None:
        store = PgOperationalWeatherCache(db_connection)
        with pytest.raises(TenantIsolationError, match="not authorized"):
            store.begin(
                key=cache_key,
                principal=replace(principal, tenant_id=TenantId(uuid4())),
                attempt_id=uuid4(),
                issue_time=ISSUE,
                started_at=ISSUE,
            )
        assert store.fetch(cache_key) is None

    def test_receipt_capacity_blocks_only_the_refused_scope(
        self,
        db_connection: sa.Connection,
        cache_key: WeatherCacheKey,
        principal: WritePrincipal,
    ) -> None:
        store = PgOperationalWeatherCache(db_connection, max_receipts=1)
        requirement = FutureWeatherRequirement(
            parameter="precipitation", valid_times=(ISSUE,), member_ids=(0,)
        )
        scopes = tuple(
            FutureWeatherScope(
                station_id=cache_key.station_id,
                binding_fingerprint=letter * 64,
                source="ifs",
                issue_time=ISSUE,
                requirements=(requirement,),
            )
            for letter in ("b", "c")
        )
        attempt = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
            future_scopes=tuple(scope.request for scope in scopes),
        )
        completion = WeatherCacheCompletion(
            finished_at=ISSUE,
            past_outcome=WeatherLegOutcome(state=WeatherLegState.UNUSED),
            future_attempts=tuple(
                FutureWeatherAttempt(
                    scope=scope.request,
                    outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
                    resolved_cycle=ISSUE,
                )
                for scope in scopes
            ),
            future_receipts=tuple(
                FutureWeatherReceipt(
                    scope=scope,
                    cycle_time=ISSUE,
                    validated_at=ISSUE,
                    row_sha256="d" * 64,
                    row_count=1,
                )
                for scope in scopes
            ),
        )
        entry = store.finish(
            attempt=attempt,
            principal=principal,
            completion=completion,
            earliest_cycle=ISSUE,
        )
        assert entry.future_attempts[0].outcome.state is WeatherLegState.SUCCESS
        assert (
            entry.future_attempts[1].outcome.reason
            is WeatherFailureReason.RECEIPT_CAPACITY
        )
        assert len(entry.future_receipts) == 1
        assert (
            store.finish(
                attempt=attempt,
                principal=principal,
                completion=completion,
                earliest_cycle=ISSUE,
            )
            == entry
        )


class TestOperationalWeatherConcurrentTransactions:
    def test_concurrent_begins_serialize_and_only_latest_can_finish(
        self,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        engine = isolated_committed_db_engine
        station = make_station_config(
            station_id=StationId(uuid4()),
            code="cache-" + uuid4().hex[:8],
            network="dhm",
        )
        with engine.begin() as conn:
            PgStationStore(conn).store_station(station)
        key = WeatherCacheKey(station_id=station.id, binding_fingerprint=FINGERPRINT)
        principal = WritePrincipal(id=None, tenant_id=station.tenant_id)
        barrier = Barrier(2, timeout=10)

        def begin() -> WeatherCacheAttempt:
            barrier.wait()
            with engine.begin() as conn:
                return PgOperationalWeatherCache(conn).begin(
                    key=key,
                    principal=principal,
                    attempt_id=uuid4(),
                    issue_time=ISSUE,
                    started_at=ISSUE,
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(begin), pool.submit(begin)]
            attempts = sorted(
                (task.result() for task in pending), key=lambda item: item.generation
            )
        assert [attempt.generation for attempt in attempts] == [1, 2]
        completion = successful_completion(key)
        with (
            engine.begin() as conn,
            pytest.raises(WeatherCacheConflictError, match="superseded"),
        ):
            PgOperationalWeatherCache(conn).finish(
                attempt=attempts[0],
                principal=principal,
                completion=completion,
                earliest_cycle=ISSUE,
            )
        with engine.begin() as conn:
            latest = PgOperationalWeatherCache(conn).finish(
                attempt=attempts[1],
                principal=principal,
                completion=completion,
                earliest_cycle=ISSUE,
            )
        assert latest.state is WeatherAttemptState.COMPLETE

        def retry() -> WeatherCacheEntry:
            barrier.wait()
            with engine.begin() as conn:
                return PgOperationalWeatherCache(conn).finish(
                    attempt=attempts[1],
                    principal=principal,
                    completion=completion,
                    earliest_cycle=ISSUE,
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            pending_retries = [pool.submit(retry), pool.submit(retry)]
            assert [task.result() for task in pending_retries] == [latest, latest]


class TestOperationalWeatherReceiptExpiry:
    def test_only_explicit_cycle_cutoff_expires_old_receipts(
        self,
        db_connection: sa.Connection,
        cache_key: WeatherCacheKey,
        principal: WritePrincipal,
    ) -> None:
        store = PgOperationalWeatherCache(db_connection)
        scope = FutureWeatherScope(
            station_id=cache_key.station_id,
            binding_fingerprint="b" * 64,
            source="ifs",
            issue_time=ISSUE,
            requirements=(
                FutureWeatherRequirement(
                    parameter="precipitation",
                    valid_times=(ISSUE,),
                    member_ids=(0,),
                ),
            ),
        )
        attempt = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
            future_scopes=tuple(scope.request for scope in (scope,)),
        )
        receipt = FutureWeatherReceipt(
            scope=scope,
            cycle_time=ISSUE,
            validated_at=ISSUE,
            row_sha256="d" * 64,
            row_count=1,
        )
        completion = WeatherCacheCompletion(
            finished_at=ISSUE,
            past_outcome=WeatherLegOutcome(state=WeatherLegState.UNUSED),
            future_attempts=(
                FutureWeatherAttempt(
                    scope=scope.request,
                    outcome=WeatherLegOutcome(state=WeatherLegState.SUCCESS),
                    resolved_cycle=ISSUE,
                ),
            ),
            future_receipts=(receipt,),
        )
        store.finish(
            attempt=attempt,
            principal=principal,
            completion=completion,
            earliest_cycle=ISSUE,
        )
        later = ensure_utc(ISSUE + timedelta(days=1))
        second = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=later,
            started_at=later,
        )
        pending = store.fetch(cache_key)
        assert pending is not None and pending.future_receipts == (receipt,)
        result = store.finish(
            attempt=second,
            principal=principal,
            earliest_cycle=later,
            completion=WeatherCacheCompletion(
                finished_at=later,
                future_attempts=(),
                past_outcome=WeatherLegOutcome(
                    state=WeatherLegState.UNAVAILABLE,
                    reason=WeatherFailureReason.TRANSPORT,
                ),
            ),
        )
        assert result.future_receipts == ()


class TestOperationalWeatherCorruption:
    def test_changed_persisted_payload_cannot_be_restored(
        self,
        db_connection: sa.Connection,
        cache_key: WeatherCacheKey,
        principal: WritePrincipal,
    ) -> None:
        store = PgOperationalWeatherCache(db_connection)
        attempt = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
        store.finish(
            attempt=attempt,
            principal=principal,
            completion=successful_completion(cache_key),
            earliest_cycle=ISSUE,
        )
        db_connection.execute(
            sa.update(operational_weather_cache)
            .where(operational_weather_cache.c.station_id == cache_key.station_id)
            .values(
                past_snapshot=sa.func.jsonb_set(
                    operational_weather_cache.c.past_snapshot,
                    sa.cast(["payload_sha256"], sa.ARRAY(sa.Text)),
                    sa.func.to_jsonb(sa.cast("0" * 64, sa.Text)),
                )
            )
        )
        with pytest.raises(ValueError, match="hash mismatch"):
            store.fetch(cache_key)
        with pytest.raises(ValueError, match="hash mismatch"):
            store.begin(
                key=cache_key,
                principal=principal,
                attempt_id=uuid4(),
                issue_time=ISSUE,
                started_at=ISSUE,
            )


class TestCommittedOperationalWeatherCache:
    def test_pending_is_committed_and_cross_tenant_rejection_audit_survives(
        self,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        engine = isolated_committed_db_engine
        station = make_station_config(
            station_id=StationId(uuid4()),
            code="committed-" + uuid4().hex[:8],
            network="dhm",
        )
        with engine.begin() as conn:
            PgStationStore(conn).store_station(station)
        key = WeatherCacheKey(station_id=station.id, binding_fingerprint=FINGERPRINT)
        principal = WritePrincipal(id=None, tenant_id=station.tenant_id)
        store = PgCommittedOperationalWeatherCache(engine.begin)
        attempt = store.begin(
            key=key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
        with engine.begin() as conn:
            pending = PgOperationalWeatherCache(conn).fetch(key)
            assert pending is not None and pending.attempt == attempt
        with pytest.raises(TenantIsolationError, match="not authorized"):
            store.begin(
                key=key,
                principal=replace(principal, tenant_id=TenantId(uuid4())),
                attempt_id=uuid4(),
                issue_time=ISSUE,
                started_at=ISSUE,
            )
        with engine.begin() as conn:
            count = conn.execute(
                sa.text(
                    "SELECT count(*) FROM audit_log "
                    "WHERE event_type = 'operational_weather_refresh' "
                    "AND target_id = :station_id"
                ),
                {"station_id": str(station.id)},
            ).scalar_one()
            assert count == 1
        persisted = store.fetch(key)
        assert persisted is not None and persisted.attempt == attempt

    def test_shared_autocommit_connection_is_refused(
        self,
        isolated_committed_db_engine: sa.Engine,
    ) -> None:
        with (
            isolated_committed_db_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as conn,
            pytest.raises(ConfigurationError, match="requires transactions"),
        ):
            PgOperationalWeatherCache(conn)


def test_cache_round_trip_normalizes_non_utc_session_timestamps(
    db_connection: sa.Connection,
    cache_key: WeatherCacheKey,
    principal: WritePrincipal,
) -> None:
    original_zone = db_connection.execute(sa.text("SHOW TIME ZONE")).scalar_one()
    try:
        db_connection.execute(sa.text("SET LOCAL TIME ZONE 'Asia/Kathmandu'"))
        store = PgOperationalWeatherCache(db_connection)
        attempt = store.begin(
            key=cache_key,
            principal=principal,
            attempt_id=uuid4(),
            issue_time=ISSUE,
            started_at=ISSUE,
        )
        assert (
            attempt.issue_time == ISSUE
            and attempt.issue_time.utcoffset() == timedelta(0)
        )
        completion = successful_completion(cache_key)
        entry = store.finish(
            attempt=attempt,
            principal=principal,
            completion=completion,
            earliest_cycle=ISSUE,
        )
        assert store.fetch(cache_key) == entry
        assert (
            entry.finished_at == ISSUE and entry.finished_at.utcoffset() == timedelta(0)
        )
        assert (
            store.finish(
                attempt=attempt,
                principal=principal,
                completion=completion,
                earliest_cycle=ISSUE,
            )
            == entry
        )
    finally:
        db_connection.execute(
            sa.text("SELECT set_config('TimeZone', :zone, true)"),
            {"zone": original_zone},
        )
