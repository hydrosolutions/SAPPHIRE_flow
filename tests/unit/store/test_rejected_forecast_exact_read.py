from __future__ import annotations

import threading
import traceback
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.exceptions import StoreError
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.ids import ModelId, RejectedForecastId, StationId
from tests.fakes.fake_stores import FakeRejectedForecastStore
from tests.integration.store.test_rejected_forecast_store import _NOW, _entry


class TestFetchRejectedForecast:
    @pytest.mark.parametrize("purpose", list(ForecastDataUse))
    def test_fake_exact_missing_and_wrong_purpose(
        self, purpose: ForecastDataUse
    ) -> None:
        store = FakeRejectedForecastStore(data_use=purpose, clock=lambda: _NOW)
        entry = _entry(station_id=StationId(uuid4()), model_id=ModelId("model"))
        if purpose is not ForecastDataUse.STANDARD:
            entry = replace(
                entry,
                payload=replace(
                    entry.payload,
                    data_use=purpose,
                    input_lineage=ForecastInputLineage(
                        provisional_discharge_fingerprints=("a" * 64,),
                        transformation_versions=("v1",),
                    ),
                ),
            )
        store.write_batch([entry], abandon=threading.Event())
        row = store.fetch_rejected_forecasts(
            entry.payload.station_id,
            _NOW - timedelta(days=1),
            _NOW + timedelta(days=1),
        )[0][0]
        assert store.fetch_rejected_forecast(row.id) == row
        assert store.fetch_rejected_forecast(RejectedForecastId(uuid4())) is None
        # Seed a corrupt mixed-purpose backing collection to prove read isolation.
        other = (
            ForecastDataUse.EXPIRED_RATING_TEST
            if purpose is ForecastDataUse.STANDARD
            else ForecastDataUse.STANDARD
        )
        foreign = replace(row, id=RejectedForecastId(uuid4()), data_use=other)
        store._rows.append(foreign)
        assert store.fetch_rejected_forecast(foreign.id) is None

    def test_test_use_database_errors_are_protected(self) -> None:
        conn = Mock(spec=sa.Connection)
        conn.execute.side_effect = SQLAlchemyError("secret-lineage")
        store = PgRejectedForecastStore(
            conn, transaction_factory=None, data_use=ForecastDataUse.EXPIRED_RATING_TEST
        )
        with pytest.raises(StoreError, match="protected forecast") as caught:
            store.fetch_rejected_forecast(RejectedForecastId(uuid4()))
        assert "secret-lineage" not in "".join(traceback.format_exception(caught.value))

    def test_standard_database_errors_remain_visible(self) -> None:
        conn = Mock(spec=sa.Connection)
        conn.execute.side_effect = SQLAlchemyError("database unavailable")
        store = PgRejectedForecastStore(conn, transaction_factory=None)
        with pytest.raises(SQLAlchemyError, match="database unavailable"):
            store.fetch_rejected_forecast(RejectedForecastId(uuid4()))


class TestLatestRejectedForecast:
    def test_sql_is_scoped_bounded_and_uses_protected_projection(self) -> None:
        from sqlalchemy.dialects import postgresql

        conn = Mock(spec=sa.Connection)
        conn.execute.return_value.mappings.return_value.one_or_none.return_value = None
        store = PgRejectedForecastStore(conn, transaction_factory=None)
        assert (
            store.fetch_latest_rejected_forecast_at(
                StationId(uuid4()), ModelId("cmal"), _NOW, ()
            )
            is None
        )
        stmt = conn.execute.call_args.args[0]
        sql = str(
            stmt.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "LIMIT 1" in sql
        assert "rejected_forecasts.input_lineage" not in sql.split("FROM")[0]
        assert "data_use = 'standard'" in sql
        assert "station_id =" in sql and "model_id = 'cmal'" in sql
        assert "issued_at <=" in sql and "recorded_at <=" in sql
        assert "group_id IS NULL" in sql

    def test_fake_latest_respects_clock_and_model(self) -> None:
        store = FakeRejectedForecastStore(clock=lambda: _NOW)
        entry = _entry(station_id=StationId(uuid4()), model_id=ModelId("model"))
        store.write_batch([entry], abandon=threading.Event())
        row = store.fetch_latest_rejected_forecast_at(
            entry.payload.station_id, entry.payload.model_id, _NOW, ()
        )
        assert row is not None
        assert (
            store.fetch_latest_rejected_forecast_at(
                entry.payload.station_id,
                entry.payload.model_id,
                _NOW - timedelta(seconds=1),
                (),
            )
            is None
        )
        assert (
            store.fetch_latest_rejected_forecast_at(
                entry.payload.station_id, ModelId("other"), _NOW, ()
            )
            is None
        )
