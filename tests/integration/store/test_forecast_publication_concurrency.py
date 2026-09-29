# pyright: reportPrivateUsage=false
from __future__ import annotations

import os
from contextlib import contextmanager
from queue import Queue
from threading import Event, Thread
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import OperationalError
from testcontainers.postgres import PostgresContainer

from alembic import command
from sapphire_flow.db.metadata import (
    forecast_publication_events,
    forecast_publication_selections,
    forecasts,
    human_station_grants,
)
from sapphire_flow.store.forecast_publication_store import (
    PgForecastPublicationStore,
    PublicationConflictError,
)
from sapphire_flow.types.forecast_publication import (
    WithdrawalReasonCode,
    WithdrawRequest,
)
from sapphire_flow.types.ids import PublicationDecisionId
from tests.integration.store.test_forecast_publication_store import (
    _add_candidate,
    _publish,
    _seed,
    _transaction,
)
from tests.integration.store.test_forecast_store import _ISSUED_B

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Connection, Engine


@pytest.fixture
def publication_engine() -> Iterator[Engine]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_publication_concurrency_test",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        try:
            yield engine
        finally:
            engine.dispose()
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous


def test_same_key_publish_serializes_and_grant_revoke_waits(
    publication_engine: Engine,
) -> None:
    with publication_engine.begin() as setup:
        _, principal, forecast_id, station_id = _seed(setup)

    ready = Event()
    release = Event()
    results: Queue[tuple[str, object]] = Queue()

    def first_publisher() -> None:
        @contextmanager
        def hold(connection: Connection) -> Iterator[Connection]:
            with connection.begin():
                yield connection
                ready.set()
                if not release.wait(10):
                    raise TimeoutError("first publication was not released")

        with publication_engine.connect() as connection:
            store = PgForecastPublicationStore(
                connection, transaction_factory=lambda: hold(connection)
            )
            try:
                decision = _publish(store, principal, forecast_id)
                results.put(("first", decision.id))
            except Exception as exc:
                results.put(("first_error", exc))

    first = Thread(target=first_publisher)
    first.start()
    assert ready.wait(10)
    try:
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            publication_engine.connect() as second_connection,
            second_connection.begin(),
        ):
            second_connection.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
            second_store = PgForecastPublicationStore(
                second_connection,
                transaction_factory=lambda: _transaction(second_connection),
            )
            _publish(second_store, principal, forecast_id, idempotency_key="second")
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            publication_engine.begin() as revoker,
        ):
            revoker.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
            revoker.execute(
                sa.delete(human_station_grants).where(
                    human_station_grants.c.user_id == principal.user_id,
                    human_station_grants.c.station_id == station_id,
                    human_station_grants.c.permission == "publish",
                )
            )
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            publication_engine.begin() as retry,
        ):
            retry.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
            retry.execute(
                sa.update(forecasts)
                .where(forecasts.c.id == forecast_id)
                .values(status="superseded", version=2)
            )
    finally:
        release.set()
    first.join(timeout=10)
    assert not first.is_alive()
    outcomes = dict(results.get_nowait() for _ in range(1))
    assert "first" in outcomes
    with publication_engine.connect() as connection:
        second_store = PgForecastPublicationStore(connection)
        with pytest.raises(PublicationConflictError):
            _publish(second_store, principal, forecast_id, idempotency_key="second")
    with publication_engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count()).select_from(forecast_publication_events)
            )
            == 1
        )
        assert (
            connection.scalar(sa.select(forecast_publication_selections.c.version)) == 1
        )


def test_retry_lock_serializes_with_publish(publication_engine: Engine) -> None:
    with publication_engine.begin() as setup:
        _, principal, forecast_id, _ = _seed(setup)

    with publication_engine.connect() as retry:
        transaction = retry.begin()
        retry.execute(
            sa.select(forecasts.c.id)
            .where(forecasts.c.id == forecast_id)
            .with_for_update()
        )
        retry.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == forecast_id)
            .values(status="superseded", version=2)
        )
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            publication_engine.connect() as publisher,
            publisher.begin(),
        ):
            publisher.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
            store = PgForecastPublicationStore(
                publisher, transaction_factory=lambda: _transaction(publisher)
            )
            _publish(store, principal, forecast_id)
        transaction.commit()
    with publication_engine.connect() as connection:
        store = PgForecastPublicationStore(connection)
        with pytest.raises(PublicationConflictError):
            _publish(store, principal, forecast_id)


def test_change_feed_sequence_follows_commit_order_for_equal_timestamps(
    publication_engine: Engine,
) -> None:
    with publication_engine.begin() as setup:
        _, principal, first_id, station_id = _seed(setup)
        second_id = _add_candidate(setup, station_id, issued_at=_ISSUED_B)

    first_ready = Event()
    release_first = Event()
    outcomes: Queue[object] = Queue()

    def first_publisher() -> None:
        @contextmanager
        def hold(connection: Connection) -> Iterator[Connection]:
            with connection.begin():
                yield connection
                first_ready.set()
                if not release_first.wait(10):
                    raise TimeoutError("first event was not released")

        with publication_engine.connect() as connection:
            store = PgForecastPublicationStore(
                connection, transaction_factory=lambda: hold(connection)
            )
            outcomes.put(_publish(store, principal, first_id).id)

    first = Thread(target=first_publisher)
    first.start()
    assert first_ready.wait(10)
    try:
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            publication_engine.connect() as second_connection,
            second_connection.begin(),
        ):
            second_connection.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
            second_store = PgForecastPublicationStore(
                second_connection,
                transaction_factory=lambda: _transaction(second_connection),
            )
            _publish(second_store, principal, second_id, idempotency_key="other")
    finally:
        release_first.set()
    first.join(timeout=10)
    assert not first.is_alive()
    assert outcomes.qsize() == 1
    with publication_engine.connect() as connection:
        second_store = PgForecastPublicationStore(connection)
        _publish(second_store, principal, second_id, idempotency_key="other")
    with publication_engine.connect() as connection:
        events = connection.execute(
            sa.select(
                forecast_publication_events.c.sequence,
                forecast_publication_events.c.forecast_id,
            ).order_by(forecast_publication_events.c.sequence)
        ).all()
        assert [(row.sequence, row.forecast_id) for row in events] == [
            (1, first_id),
            (2, second_id),
        ]


def test_reader_snapshot_lock_keeps_withdrawal_from_committing_mid_response(
    publication_engine: Engine,
) -> None:
    from uuid import uuid4

    from sapphire_flow.types.forecast_publication import PublicationKey
    from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
    from tests.integration.store.test_forecast_publication_store import _NOW
    from tests.integration.store.test_forecast_store import _ISSUED_A

    with publication_engine.begin() as setup:
        _, principal, forecast_id, station_id = _seed(setup)
    with publication_engine.connect() as writer:
        _publish(PgForecastPublicationStore(writer), principal, forecast_id)

    request = WithdrawRequest(
        forecast_id=forecast_id,
        expected_selection_version=1,
        reason_code=WithdrawalReasonCode.DATA_ERROR,
        reason_text="corrected observations",
        idempotency_key="snapshot-withdraw",
    )
    with publication_engine.begin() as reader:
        read_store = PgForecastPublicationStore(reader)
        read_store.lock_read_snapshot()
        assert (
            read_store.fetch_selection(
                PublicationKey(
                    tenant_id=DEFAULT_TENANT_ID,
                    station_id=station_id,
                    parameter="discharge",
                    issued_at=_ISSUED_A,
                )
            ).selected_forecast_id
            == forecast_id
        )
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            publication_engine.connect() as writer,
        ):
            writer.execute(sa.text("SET lock_timeout = '100ms'"))
            PgForecastPublicationStore(
                writer, transaction_factory=lambda: _transaction(writer)
            ).withdraw(
                request,
                principal,
                decision_id=PublicationDecisionId(uuid4()),
                now=_NOW,
            )
        assert len(read_store.fetch_decisions(forecast_id)) == 1

    with publication_engine.connect() as writer:
        PgForecastPublicationStore(writer).withdraw(
            request,
            principal,
            decision_id=PublicationDecisionId(uuid4()),
            now=_NOW,
        )
