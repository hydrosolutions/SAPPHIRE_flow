import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from sapphire_flow.db.metadata import rejected_forecasts
from sapphire_flow.exceptions import (
    ConfigurationError,
    DailyOutputRolledBackError,
    StoreError,
)
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.ids import StationId
from tests.conftest import make_station_config
from tests.integration.store.test_rejected_forecast_store import (
    _NOW,
    _entry,
    _seed_model,
)


def _seed_station(conn: sa.Connection) -> StationId:
    station = make_station_config(
        station_id=StationId(uuid4()), code=f"committed-{uuid4()}"
    )
    PgStationStore(conn).store_station(station)
    return station.id


class TestCommittedRejectedWrite:
    def test_returns_exact_parameter_ids_after_commit(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        with isolated_committed_db_engine.begin() as conn:
            sid = _seed_station(conn)
            mid = _seed_model(conn, f"committed-{uuid4()}")
        entry = _entry(station_id=sid, model_id=mid)
        first = entry.payload.parameters[0]
        second = replace(
            first, ensemble=replace(first.ensemble, parameter="water_level")
        )
        entry = replace(
            entry, payload=replace(entry.payload, parameters=(first, second))
        )

        with isolated_committed_db_engine.connect() as conn:
            store = PgRejectedForecastStore(
                conn, transaction_factory=isolated_committed_db_engine.begin
            )
            references = store.write_committed(entry)

        # A fresh connection proves these are committed, not pending transaction IDs.
        with isolated_committed_db_engine.connect() as reader:
            store = PgRejectedForecastStore(reader, transaction_factory=None)
            rows, count = store.fetch_rejected_forecasts(
                sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1), model_id=mid
            )
        assert count == 2
        assert {key.parameter: value for key, value in references.items()} == {
            row.parameter: row.id for row in rows
        }
        assert {
            (key.station_id, key.model_id, key.issued_at) for key in references
        } == {(sid, mid, _NOW)}
        assert {row.attempt_id for row in rows} == {entry.attempt_id}

    def test_failure_at_commit_does_not_return_references(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        with isolated_committed_db_engine.begin() as conn:
            sid = _seed_station(conn)
            mid = _seed_model(conn, f"failed-commit-{uuid4()}")
        entry = _entry(station_id=sid, model_id=mid)

        @contextmanager
        def fail_commit() -> Iterator[sa.Connection]:
            with isolated_committed_db_engine.begin() as txn:
                yield txn
                raise RuntimeError("fixture commit refused")

        with isolated_committed_db_engine.connect() as conn:
            store = PgRejectedForecastStore(conn, transaction_factory=fail_commit)
            with pytest.raises(RuntimeError, match="commit refused"):
                store.write_committed(entry)
        with isolated_committed_db_engine.connect() as reader:
            count = reader.scalar(
                sa.select(sa.func.count())
                .select_from(rejected_forecasts)
                .where(rejected_forecasts.c.attempt_id == entry.attempt_id)
            )
        assert count == 0

    def test_duplicate_parameter_cannot_overwrite_a_reference(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection, f"duplicate-{uuid4()}")
        entry = _entry(station_id=sid, model_id=mid)
        entry = replace(
            entry,
            payload=replace(entry.payload, parameters=entry.payload.parameters * 2),
        )
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=db_connection.begin_nested
        )
        with pytest.raises(ValueError, match="duplicate parameter"):
            store.write_committed(entry)
        assert (
            db_connection.scalar(
                sa.select(sa.func.count())
                .select_from(rejected_forecasts)
                .where(rejected_forecasts.c.attempt_id == entry.attempt_id)
            )
            == 0
        )

    def test_read_only_store_cannot_write(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection, f"readonly-{uuid4()}")
        store = PgRejectedForecastStore(db_connection, transaction_factory=None)
        with pytest.raises(ConfigurationError, match="transaction_factory"):
            store.write_committed(_entry(station_id=sid, model_id=mid))

    @pytest.mark.parametrize("invalid", ["empty", "wrong-purpose"])
    def test_invalid_payload_is_refused_before_writing(
        self, db_connection: sa.Connection, invalid: str
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection, f"invalid-{uuid4()}")
        entry = _entry(station_id=sid, model_id=mid)
        if invalid == "empty":
            entry = replace(entry, payload=replace(entry.payload, parameters=()))
            message = "at least one parameter"
        else:
            entry = replace(
                entry,
                payload=replace(
                    entry.payload, data_use=ForecastDataUse.EXPIRED_RATING_TEST
                ),
            )
            message = "store purpose"
        store = PgRejectedForecastStore(
            db_connection, transaction_factory=db_connection.begin_nested
        )
        with pytest.raises(ValueError, match=message):
            store.write_committed(entry)
        assert (
            db_connection.scalar(
                sa.select(sa.func.count())
                .select_from(rejected_forecasts)
                .where(rejected_forecasts.c.attempt_id == entry.attempt_id)
            )
            == 0
        )

    @pytest.mark.parametrize("exit_failure", [False, True])
    def test_body_failure_requires_clean_rollback_and_context_exit(
        self,
        isolated_committed_db_engine: sa.Engine,
        exit_failure: bool,
    ) -> None:
        with isolated_committed_db_engine.begin() as conn:
            sid = _seed_station(conn)
            mid = _seed_model(conn, f"body-rollback-{uuid4()}")
        entry = _entry(station_id=sid, model_id=mid)

        def fail_after_insert(
            conn: sa.Connection,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            if statement.startswith("INSERT INTO rejected_forecasts"):
                raise RuntimeError("fixture body failed after insert")

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with isolated_committed_db_engine.begin() as txn:
                event.listen(txn, "after_cursor_execute", fail_after_insert)
                yield txn
                if exit_failure:
                    raise RuntimeError("fixture context exit failed")

        with isolated_committed_db_engine.connect() as conn:
            store = PgRejectedForecastStore(conn, transaction_factory=transaction)
            expected = RuntimeError if exit_failure else DailyOutputRolledBackError
            message = "context exit failed" if exit_failure else "rolled back"
            with pytest.raises(expected, match=message) as failure:
                store.write_committed(entry)
            if exit_failure:
                assert not isinstance(failure.value, DailyOutputRolledBackError)
            else:
                assert isinstance(failure.value.__cause__, RuntimeError)
                assert "body failed after insert" in str(failure.value.__cause__)
        with isolated_committed_db_engine.connect() as reader:
            assert (
                reader.scalar(
                    sa.select(sa.func.count())
                    .select_from(rejected_forecasts)
                    .where(rejected_forecasts.c.attempt_id == entry.attempt_id)
                )
                == 0
            )

    def test_rollback_failure_is_not_proof(
        self,
        isolated_committed_db_engine: sa.Engine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with isolated_committed_db_engine.begin() as conn:
            sid = _seed_station(conn)
            mid = _seed_model(conn, f"rollback-failure-{uuid4()}")
        entry = _entry(station_id=sid, model_id=mid)

        def fail_execute(*args: object, **kwargs: object) -> None:
            raise RuntimeError("fixture body failed")

        def fail_rollback() -> None:
            raise RuntimeError("fixture rollback failed")

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with isolated_committed_db_engine.begin() as txn:
                monkeypatch.setattr(txn, "execute", fail_execute)
                monkeypatch.setattr(txn, "rollback", fail_rollback)
                yield txn

        with isolated_committed_db_engine.connect() as conn:
            store = PgRejectedForecastStore(conn, transaction_factory=transaction)
            with pytest.raises(RuntimeError, match="rollback failed") as failure:
                store.write_committed(entry)
        assert not isinstance(failure.value, DailyOutputRolledBackError)

    def test_lost_commit_acknowledgement_is_not_rollback_proof(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        with isolated_committed_db_engine.begin() as conn:
            sid = _seed_station(conn)
            mid = _seed_model(conn, f"lost-ack-{uuid4()}")
        entry = _entry(station_id=sid, model_id=mid)

        @contextmanager
        def transaction() -> Iterator[sa.Connection]:
            with isolated_committed_db_engine.begin() as txn:
                yield txn
            raise RuntimeError("fixture commit acknowledgement lost")

        with isolated_committed_db_engine.connect() as conn:
            store = PgRejectedForecastStore(conn, transaction_factory=transaction)
            with pytest.raises(RuntimeError, match="acknowledgement lost") as failure:
                store.write_committed(entry)
        assert not isinstance(failure.value, DailyOutputRolledBackError)
        with isolated_committed_db_engine.connect() as reader:
            assert (
                reader.scalar(
                    sa.select(sa.func.count())
                    .select_from(rejected_forecasts)
                    .where(rejected_forecasts.c.attempt_id == entry.attempt_id)
                )
                == 1
            )

    def test_protected_write_failure_retains_redaction_and_cannot_commit(
        self, isolated_committed_db_engine: sa.Engine
    ) -> None:
        with isolated_committed_db_engine.begin() as conn:
            sid = _seed_station(conn)
            mid = _seed_model(conn, f"protected-rollback-{uuid4()}")
        entry = _entry(station_id=sid, model_id=mid)
        entry = replace(
            entry,
            payload=replace(
                entry.payload,
                data_use=ForecastDataUse.EXPIRED_RATING_TEST,
                input_lineage=ForecastInputLineage(
                    provisional_discharge_fingerprints=("a" * 64,),
                    transformation_versions=("protected-canary",),
                ),
            ),
        )
        with isolated_committed_db_engine.connect() as conn:
            store = PgRejectedForecastStore(
                conn,
                transaction_factory=isolated_committed_db_engine.begin,
                data_use=ForecastDataUse.EXPIRED_RATING_TEST,
            )
            with pytest.raises(StoreError, match="protected forecast") as failure:
                store.write_committed(entry)
        rendered = "".join(traceback.format_exception(failure.value))
        assert "provisional_discharge_fingerprints" not in rendered
        assert "protected-canary" not in rendered
        assert "a" * 64 not in rendered
        with isolated_committed_db_engine.connect() as reader:
            assert (
                reader.scalar(
                    sa.select(sa.func.count())
                    .select_from(rejected_forecasts)
                    .where(rejected_forecasts.c.attempt_id == entry.attempt_id)
                )
                == 0
            )
