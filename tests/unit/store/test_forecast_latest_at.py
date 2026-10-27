from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

from sqlalchemy.dialects import postgresql

from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.enums import ForecastDataUse
from tests.fakes.fake_stores import FakeForecastStore
from tests.unit.fakes.test_fake_forecast_store_supersession import _forecast


def test_fake_latest_at_ignores_future_combined_and_superseded() -> None:
    from sapphire_flow.types.enums import ForecastStatus

    store = FakeForecastStore()
    row = _forecast()
    store.seed_pre_capture_forecast(row)
    future = replace(_forecast(), issued_at=row.issued_at + timedelta(days=1))
    store.seed_pre_capture_forecast(future)
    assert (
        store.fetch_latest_forecast_at(row.station_id, row.model_id, row.issued_at)
        == row
    )
    assert store.fetch_latest_forecast(row.station_id, row.model_id) == future
    store.transition_status(row.id, row.version, ForecastStatus.SUPERSEDED)
    assert (
        store.fetch_latest_forecast_at(row.station_id, row.model_id, row.issued_at)
        is None
    )


def test_pg_latest_at_selects_only_id_with_bound_and_purpose() -> None:
    conn = Mock()
    conn.execute.return_value.scalar_one_or_none.return_value = None
    store = PgForecastStore(conn, transaction_factory=lambda: None)
    row = _forecast()
    assert (
        store.fetch_latest_forecast_at(row.station_id, row.model_id, row.issued_at)
        is None
    )
    stmt = conn.execute.call_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    params = stmt.compile().params
    assert list(stmt.selected_columns.keys()) == ["id"]
    assert "forecasts.issued_at <=" in sql
    assert "forecasts.combination_strategy IS NULL" in sql
    assert "forecasts.data_use =" in sql
    assert "forecasts.status !=" in sql
    assert "LIMIT" in sql
    assert ForecastDataUse.STANDARD.value in params.values()
    assert row.issued_at in params.values()
