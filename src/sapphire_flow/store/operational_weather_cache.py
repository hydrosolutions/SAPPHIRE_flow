# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.pool import NullPool

from sapphire_flow.db.metadata import operational_weather_cache as cache
from sapphire_flow.db.metadata import stations
from sapphire_flow.exceptions import (
    ConfigurationError,
    StoreError,
    TenantIsolationError,
)
from sapphire_flow.services.write_principal import enforce_tenant_isolation
from sapphire_flow.store._helpers import utc_from_row
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.operational_weather_codec import (
    completion_digest,
    decode_cache_entry,
    encode_cache_entry,
)
from sapphire_flow.types.enums import AuditEventType
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.operational_weather import (
    FutureWeatherAttempt,
    FutureWeatherReceipt,
    FutureWeatherRequest,
    FutureWeatherScope,
    WeatherAttemptState,
    WeatherCacheAttempt,
    WeatherCacheCompletion,
    WeatherCacheEntry,
    WeatherCacheKey,
    WeatherFailureReason,
    WeatherLegOutcome,
    WeatherLegState,
    require_utc,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager
    from uuid import UUID

    from sapphire_flow.protocols.stores import AuditLogStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.write_principal import WritePrincipal


class WeatherCacheConflictError(StoreError):
    pass


def _key_predicate(key: WeatherCacheKey) -> sa.ColumnElement[bool]:
    return sa.and_(
        cache.c.station_id == key.station_id,
        cache.c.binding_fingerprint == key.binding_fingerprint,
    )


class PgOperationalWeatherCache:
    def __init__(
        self,
        conn: sa.Connection,
        *,
        max_past_bytes: int = 2 * 1024 * 1024,
        max_receipts: int = 32,
        audit_log_store: AuditLogStore | None = None,
    ) -> None:
        if max_past_bytes <= 0 or not 1 <= max_receipts <= 32:
            raise ValueError("invalid operational cache bounds")
        if conn.get_execution_options().get("isolation_level") == "AUTOCOMMIT":
            raise ConfigurationError(
                "operational cache lifecycle requires transactions"
            )
        self._conn = conn
        self._max_past_bytes = max_past_bytes
        self._max_receipts = max_receipts
        self._audit_log_store = audit_log_store

    def _authorize(
        self, key: WeatherCacheKey, principal: WritePrincipal, now: UtcDatetime
    ) -> None:
        if principal is None:  # pyright: ignore[reportUnnecessaryComparison]
            raise ConfigurationError(
                "selected weather requires an explicit write principal"
            )
        owner = self._conn.execute(
            sa.select(stations.c.tenant_id).where(stations.c.id == key.station_id)
        ).scalar_one_or_none()
        if owner is None:
            raise ConfigurationError("operational weather station does not exist")
        enforce_tenant_isolation(
            principal=principal,
            target_tenant_id=TenantId(owner),
            audit_log_store=self._audit_log_store,
            event_type=AuditEventType.OPERATIONAL_WEATHER_REFRESH,
            target_type="station",
            target_id=str(key.station_id),
            detail=None,
            now=now,
        )

    def fetch(self, key: WeatherCacheKey) -> WeatherCacheEntry | None:
        row = (
            self._conn.execute(sa.select(cache).where(_key_predicate(key)))
            .mappings()
            .first()
        )
        return self._decode(row) if row is not None else None

    def _locked(self, key: WeatherCacheKey) -> WeatherCacheEntry | None:
        row = (
            self._conn.execute(
                sa.select(cache).where(_key_predicate(key)).with_for_update()
            )
            .mappings()
            .first()
        )
        return self._decode(row) if row is not None else None

    def begin(
        self,
        *,
        key: WeatherCacheKey,
        principal: WritePrincipal,
        attempt_id: UUID,
        issue_time: UtcDatetime,
        started_at: UtcDatetime,
        future_scopes: tuple[FutureWeatherRequest, ...] = (),
    ) -> WeatherCacheAttempt:
        self._authorize(key, principal, started_at)
        if any(
            scope.station_id != key.station_id or scope.issue_time != issue_time
            for scope in future_scopes
        ):
            raise ValueError("weather refresh scope does not match station/issue")
        pending = WeatherLegOutcome(state=WeatherLegState.PENDING)
        attempt = WeatherCacheAttempt(
            key=key,
            generation=1,
            id=attempt_id,
            issue_time=issue_time,
            started_at=started_at,
        )
        entry = WeatherCacheEntry(
            attempt=attempt,
            state=WeatherAttemptState.PENDING,
            past_outcome=pending,
            future_attempts=tuple(
                FutureWeatherAttempt(scope=scope, outcome=pending)
                for scope in future_scopes
            ),
        )
        created = self._conn.execute(
            insert(cache)
            .values(**self._values(entry))
            .on_conflict_do_nothing(
                index_elements=[cache.c.station_id, cache.c.binding_fingerprint]
            )
            .returning(cache.c.station_id)
        ).scalar_one_or_none()
        if created is not None:
            return attempt
        prior = self._locked(key)
        if prior is None:
            raise WeatherCacheConflictError("weather cache vanished during refresh")
        attempt = replace(attempt, generation=prior.attempt.generation + 1)
        entry = replace(
            entry,
            attempt=attempt,
            past_snapshot=prior.past_snapshot,
            future_receipts=prior.future_receipts,
        )
        self._conn.execute(
            sa.update(cache).where(_key_predicate(key)).values(**self._values(entry))
        )
        return attempt

    def finish(
        self,
        *,
        attempt: WeatherCacheAttempt,
        principal: WritePrincipal,
        completion: WeatherCacheCompletion,
        earliest_cycle: UtcDatetime,
    ) -> WeatherCacheEntry:
        self._authorize(attempt.key, principal, completion.finished_at)
        current = self._locked(attempt.key)
        entry = complete_weather_entry(
            current,
            attempt=attempt,
            completion=completion,
            earliest_cycle=earliest_cycle,
            max_past_bytes=self._max_past_bytes,
            max_receipts=self._max_receipts,
        )
        if current is not None and current.state is not WeatherAttemptState.PENDING:
            return entry
        changed = self._conn.execute(
            sa.update(cache)
            .where(
                _key_predicate(attempt.key),
                cache.c.attempt_generation == attempt.generation,
                cache.c.attempt_id == attempt.id,
                cache.c.outcome == "pending",
            )
            .values(**self._values(entry))
            .returning(cache.c.station_id)
        ).scalar_one_or_none()
        if changed is None:
            raise WeatherCacheConflictError("weather refresh lost pending-state CAS")
        return entry

    def _values(self, entry: WeatherCacheEntry) -> dict[str, object]:
        payload = encode_cache_entry(entry, max_past_bytes=self._max_past_bytes)
        fields = cast("dict[str, object]", payload["entry"])
        return {
            "station_id": entry.attempt.key.station_id,
            "binding_fingerprint": entry.attempt.key.binding_fingerprint,
            "payload_version": 1,
            "attempt_generation": entry.attempt.generation,
            "attempt_id": entry.attempt.id,
            "issue_time": entry.attempt.issue_time,
            "started_at": entry.attempt.started_at,
            "finished_at": entry.finished_at,
            "outcome": entry.state.value,
            "past_outcome": fields["past_outcome"],
            "future_attempts": fields["future_attempts"],
            "past_snapshot": fields["past_snapshot"],
            "future_receipts": fields["future_receipts"],
            "completion_sha256": entry.completion_sha256,
        }

    def _decode(self, row: sa.RowMapping) -> WeatherCacheEntry:
        return decode_cache_entry(
            {
                "version": row["payload_version"],
                "entry": {
                    "attempt": {
                        "key": {
                            "station_id": str(row["station_id"]),
                            "binding_fingerprint": row["binding_fingerprint"],
                        },
                        "generation": row["attempt_generation"],
                        "id": str(row["attempt_id"]),
                        "issue_time": utc_from_row(row["issue_time"]).isoformat(),
                        "started_at": utc_from_row(row["started_at"]).isoformat(),
                    },
                    "state": row["outcome"],
                    "past_outcome": row["past_outcome"],
                    "future_attempts": row["future_attempts"],
                    "past_snapshot": row["past_snapshot"],
                    "future_receipts": row["future_receipts"],
                    "finished_at": utc_from_row(row["finished_at"]).isoformat()
                    if row["finished_at"] is not None
                    else None,
                    "completion_sha256": row["completion_sha256"],
                },
            },
            max_past_bytes=self._max_past_bytes,
        )


def complete_weather_entry(
    current: WeatherCacheEntry | None,
    *,
    attempt: WeatherCacheAttempt,
    completion: WeatherCacheCompletion,
    earliest_cycle: UtcDatetime,
    max_past_bytes: int,
    max_receipts: int,
) -> WeatherCacheEntry:
    require_utc(earliest_cycle)
    if earliest_cycle > completion.finished_at:
        raise ValueError("receipt expiry boundary follows completion time")
    digest = completion_digest(completion, max_past_bytes=max_past_bytes)
    if current is None or current.attempt != attempt:
        raise WeatherCacheConflictError("weather refresh generation was superseded")
    if current.state is not WeatherAttemptState.PENDING:
        if current.completion_sha256 == digest:
            return current
        raise WeatherCacheConflictError("terminal weather refresh is immutable")
    if {future.scope for future in current.future_attempts} != {
        future.scope for future in completion.future_attempts
    }:
        raise ValueError("weather completion does not match declared future scopes")
    snapshot = completion.past_snapshot
    if snapshot is not None and (
        snapshot.window.issue_time != attempt.issue_time
        or not attempt.started_at <= snapshot.retrieved_at <= completion.finished_at
    ):
        raise ValueError("past snapshot does not belong to current weather attempt")
    if any(
        future.scope.station_id != attempt.key.station_id
        or future.scope.issue_time != attempt.issue_time
        for future in completion.future_attempts
    ):
        raise ValueError("future outcome does not belong to current weather attempt")
    for receipt in completion.future_receipts:
        if (
            receipt.scope.station_id != attempt.key.station_id
            or receipt.scope.issue_time != attempt.issue_time
            or not attempt.started_at <= receipt.validated_at <= completion.finished_at
            or receipt.cycle_time < earliest_cycle
        ):
            raise ValueError(
                "future receipt does not belong to current weather attempt"
            )
    receipts, refused = _retain_receipts(
        current.future_receipts,
        completion.future_receipts,
        earliest_cycle,
        max_receipts,
    )
    futures = tuple(
        replace(
            future,
            outcome=WeatherLegOutcome(
                state=WeatherLegState.UNAVAILABLE,
                reason=WeatherFailureReason.RECEIPT_CAPACITY,
            ),
        )
        if future.scope in {scope.request for scope in refused}
        else future
        for future in completion.future_attempts
    )
    effective = replace(
        completion,
        future_attempts=futures,
        future_receipts=tuple(
            receipt
            for receipt in completion.future_receipts
            if receipt.scope not in refused
        ),
    )
    entry = WeatherCacheEntry(
        attempt=attempt,
        state=effective.state,
        past_outcome=effective.past_outcome,
        future_attempts=futures,
        past_snapshot=snapshot or current.past_snapshot,
        future_receipts=receipts,
        finished_at=completion.finished_at,
        completion_sha256=digest,
    )
    return entry


def _retain_receipts(
    prior: tuple[FutureWeatherReceipt, ...],
    incoming: tuple[FutureWeatherReceipt, ...],
    earliest_cycle: UtcDatetime,
    max_receipts: int,
) -> tuple[tuple[FutureWeatherReceipt, ...], frozenset[FutureWeatherScope]]:
    retained = {
        (receipt.scope, receipt.cycle_time): receipt
        for receipt in prior
        if receipt.cycle_time >= earliest_cycle
    }
    refused: set[FutureWeatherScope] = set()
    for receipt in incoming:
        key = (receipt.scope, receipt.cycle_time)
        old = retained.get(key)
        if old is not None and (old.row_sha256, old.row_count) != (
            receipt.row_sha256,
            receipt.row_count,
        ):
            raise WeatherCacheConflictError("future receipt value proof conflicts")
        if old is None and len(retained) >= max_receipts:
            refused.add(receipt.scope)
            continue
        retained[key] = receipt
    return tuple(retained.values()), frozenset(refused)


def operational_weather_transaction_factory(
    url: sa.URL,
) -> Callable[[], AbstractContextManager[sa.Connection]]:
    engine = sa.create_engine(
        url, poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    return engine.begin


class PgCommittedOperationalWeatherCache:
    def __init__(
        self,
        transaction_factory: Callable[[], AbstractContextManager[sa.Connection]],
        *,
        max_past_bytes: int = 2 * 1024 * 1024,
        max_receipts: int = 32,
    ) -> None:
        if max_past_bytes <= 0 or not 1 <= max_receipts <= 32:
            raise ValueError("invalid operational cache bounds")
        self._begin = transaction_factory
        self._max_past_bytes = max_past_bytes
        self._max_receipts = max_receipts

    @contextmanager
    def _transaction(self) -> Iterator[PgOperationalWeatherCache]:
        denial: TenantIsolationError | None = None
        with self._begin() as conn:
            conn.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            conn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
            try:
                yield PgOperationalWeatherCache(
                    conn,
                    max_past_bytes=self._max_past_bytes,
                    max_receipts=self._max_receipts,
                    audit_log_store=PgAuditLogStore(conn),
                )
            except TenantIsolationError as exc:
                # Authorization runs before mutation. Commit only its rejection audit.
                denial = exc
        if denial is not None:
            raise denial

    def fetch(self, key: WeatherCacheKey) -> WeatherCacheEntry | None:
        with self._transaction() as store:
            return store.fetch(key)

    def begin(
        self,
        *,
        key: WeatherCacheKey,
        principal: WritePrincipal,
        attempt_id: UUID,
        issue_time: UtcDatetime,
        started_at: UtcDatetime,
        future_scopes: tuple[FutureWeatherRequest, ...] = (),
    ) -> WeatherCacheAttempt:
        with self._transaction() as store:
            return store.begin(
                key=key,
                principal=principal,
                attempt_id=attempt_id,
                issue_time=issue_time,
                started_at=started_at,
                future_scopes=future_scopes,
            )

    def finish(
        self,
        *,
        attempt: WeatherCacheAttempt,
        principal: WritePrincipal,
        completion: WeatherCacheCompletion,
        earliest_cycle: UtcDatetime,
    ) -> WeatherCacheEntry:
        with self._transaction() as store:
            return store.finish(
                attempt=attempt,
                principal=principal,
                completion=completion,
                earliest_cycle=earliest_cycle,
            )
