from __future__ import annotations

import threading
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import event

from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.ids import RejectedForecastId
from tests.integration.store.test_rejected_forecast_store import (
    _NOW,
    _entry,
    _seed_model,
    _seed_station,
    savepoint_factory,
)


class TestFetchRejectedForecast:
    def test_exact_older_reference_and_missing(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        writer = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        writer.write_batch(
            [
                _entry(
                    station_id=sid, model_id=mid, issued_at=_NOW - timedelta(hours=i)
                )
                for i in range(25)
            ],
            abandon=threading.Event(),
        )
        rows, _ = writer.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=2), _NOW + timedelta(days=1), limit=25
        )
        reader = PgRejectedForecastStore(db_connection, transaction_factory=None)
        assert reader.fetch_rejected_forecast(rows[-1].id) == rows[-1]
        assert reader.fetch_rejected_forecast(RejectedForecastId(uuid4())) is None

    def test_purpose_isolation_and_protected_projection(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        entry = _entry(station_id=sid, model_id=mid)
        standard = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        restricted = PgRejectedForecastStore(
            db_connection,
            transaction_factory=savepoint_factory(db_connection),
            data_use=ForecastDataUse.EXPIRED_RATING_TEST,
        )
        lineage = ForecastInputLineage(
            provisional_discharge_fingerprints=("a" * 64,),
            transformation_versions=("v1",),
        )
        # Disposable transactional fixture only; production write refusal is unchanged.
        db_connection.execute(
            sa.text(
                "DROP TRIGGER trg_rejected_forecast_test_write_refused "
                "ON rejected_forecasts"
            )
        )
        standard.write_batch([entry], abandon=threading.Event())
        restricted.write_batch(
            [
                replace(
                    entry,
                    payload=replace(
                        entry.payload,
                        data_use=ForecastDataUse.EXPIRED_RATING_TEST,
                        input_lineage=lineage,
                    ),
                )
            ],
            abandon=threading.Event(),
        )
        window = (sid, _NOW - timedelta(days=1), _NOW + timedelta(days=1))
        ordinary = standard.fetch_rejected_forecasts(*window)[0][0]
        test = restricted.fetch_rejected_forecasts(*window)[0][0]
        assert standard.fetch_rejected_forecast(test.id) is None
        assert restricted.fetch_rejected_forecast(ordinary.id) is None
        assert restricted.fetch_rejected_forecast(test.id) == test
        assert test.input_lineage == lineage
        statements: list[str] = []

        def capture(
            conn: sa.Connection,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(db_connection, "before_cursor_execute", capture)
        try:
            assert standard.fetch_rejected_forecast(ordinary.id) == ordinary
        finally:
            event.remove(db_connection, "before_cursor_execute", capture)
        assert len(statements) == 1
        projection = statements[0].split("FROM")[0]
        assert "rejected_forecasts.input_lineage" not in projection
        assert "NULL" in projection and "AS input_lineage" in projection


class TestLatestRejectedForecast:
    def test_bounded_latest_ignores_future_and_other_model(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        other = _seed_model(db_connection, "other-diagnostic")
        writer = PgRejectedForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        writer.write_batch(
            [
                _entry(station_id=sid, model_id=mid, issued_at=_NOW - timedelta(days=i))
                for i in range(35)
            ],
            abandon=threading.Event(),
        )
        rows, _ = writer.fetch_rejected_forecasts(
            sid, _NOW - timedelta(days=40), _NOW + timedelta(days=1), limit=1
        )
        as_of = rows[0].recorded_at + timedelta(seconds=1)
        writer.write_batch(
            [
                _entry(
                    station_id=sid, model_id=mid, issued_at=as_of + timedelta(days=1)
                ),
                _entry(
                    station_id=sid, model_id=other, issued_at=_NOW + timedelta(days=1)
                ),
            ],
            abandon=threading.Event(),
        )
        reader = PgRejectedForecastStore(db_connection, transaction_factory=None)
        assert reader.fetch_latest_rejected_forecast_at(sid, mid, as_of, ()) == rows[0]
        assert (
            reader.fetch_latest_rejected_forecast_at(
                sid, mid, rows[0].recorded_at - timedelta(seconds=1), ()
            )
            is None
        )
