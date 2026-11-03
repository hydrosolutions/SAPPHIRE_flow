from unittest.mock import Mock
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from sapphire_flow.exceptions import StoredForecastDecodeError, StoreError
from sapphire_flow.store import forecast_store, rejected_forecast_store
from sapphire_flow.types.enums import ForecastDataUse


class TestExactDecodeFailureIsolation:
    @pytest.mark.parametrize("rejected", [False, True])
    @pytest.mark.parametrize("purpose", list(ForecastDataUse))
    @pytest.mark.parametrize(
        "failure", [AttributeError, TypeError, KeyError, IndexError]
    )
    def test_programmer_fault_is_not_a_decode_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        rejected: bool,
        purpose: ForecastDataUse,
        failure: type[Exception],
    ) -> None:
        conn = Mock(spec=sa.Connection)
        conn.engine = Mock(spec=sa.Engine)
        record_id = uuid4()
        conn.execute.return_value.mappings.return_value.all.return_value = [
            {"id": record_id}
        ]
        module = rejected_forecast_store if rejected else forecast_store
        decoder = "_row_to_domain" if rejected else "_rows_to_domain"
        monkeypatch.setattr(
            module, decoder, Mock(side_effect=failure("programmer fault"))
        )
        store = (
            rejected_forecast_store.PgRejectedForecastStore
            if rejected
            else forecast_store.PgForecastStore
        )(conn, transaction_factory=None, data_use=purpose)
        reader = (
            store.fetch_rejected_forecasts_by_ids
            if rejected
            else store.fetch_forecasts_by_ids
        )
        with pytest.raises(failure, match="programmer fault"):
            reader([record_id])

    @pytest.mark.parametrize("rejected", [False, True])
    @pytest.mark.parametrize("purpose", list(ForecastDataUse))
    @pytest.mark.parametrize("stage", ["execute", "fetch"])
    def test_sql_failure_retains_purpose_boundary(
        self,
        rejected: bool,
        purpose: ForecastDataUse,
        stage: str,
    ) -> None:
        conn = Mock(spec=sa.Connection)
        conn.engine = Mock(spec=sa.Engine)
        if stage == "execute":
            conn.execute.side_effect = SQLAlchemyError("query failed")
        else:
            conn.execute.return_value.mappings.return_value.all.side_effect = (
                SQLAlchemyError("query failed")
            )
        store = (
            rejected_forecast_store.PgRejectedForecastStore
            if rejected
            else forecast_store.PgForecastStore
        )(conn, transaction_factory=None, data_use=purpose)
        reader = (
            store.fetch_rejected_forecasts_by_ids
            if rejected
            else store.fetch_forecasts_by_ids
        )
        expected = (
            SQLAlchemyError if purpose is ForecastDataUse.STANDARD else StoreError
        )
        message = (
            "query failed"
            if purpose is ForecastDataUse.STANDARD
            else "protected forecast"
        )
        with pytest.raises(expected, match=message) as caught:
            reader([uuid4()])
        assert not isinstance(caught.value, StoredForecastDecodeError)

    @pytest.mark.parametrize("purpose", list(ForecastDataUse))
    def test_invalid_frame_cast_is_typed_only_for_standard(
        self,
        purpose: ForecastDataUse,
    ) -> None:
        conn = Mock(spec=sa.Connection)
        conn.engine = Mock(spec=sa.Engine)
        record_id = uuid4()
        conn.execute.return_value.mappings.return_value.all.return_value = [
            {
                "id": record_id,
                "representation": "members",
                "valid_time": "bad-time",
                "member_id": 0,
                "value": 1.0,
            }
        ]
        store = forecast_store.PgForecastStore(
            conn, transaction_factory=None, data_use=purpose
        )
        with pytest.raises(ValueError, match="invalid stored") as caught:
            store.fetch_forecasts_by_ids([record_id])
        assert isinstance(caught.value, StoredForecastDecodeError) == (
            purpose is ForecastDataUse.STANDARD
        )
        underlying = (
            caught.value.__cause__
            if purpose is ForecastDataUse.STANDARD
            else caught.value
        )
        assert isinstance(underlying.__cause__, pl.exceptions.InvalidOperationError)
