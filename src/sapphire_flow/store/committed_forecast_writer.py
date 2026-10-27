from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING

import sqlalchemy as sa

from sapphire_flow.exceptions import DailyOutputRolledBackError
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.forecast_reference import ForecastOutputKey

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.ids import ForecastId


class PgCommittedForecastWriter:
    def __init__(
        self, transaction_factory: Callable[[], AbstractContextManager[sa.Connection]]
    ) -> None:
        self._transaction_factory = transaction_factory

    def write_committed(
        self, forecasts: tuple[OperationalForecast, ...]
    ) -> dict[ForecastOutputKey, ForecastId]:
        keys = tuple(
            ForecastOutputKey(
                station_id=f.station_id,
                model_id=f.model_id,
                issued_at=f.issued_at,
                parameter=f.ensemble.parameter,
            )
            for f in forecasts
        )
        if not keys or len(set(keys)) != len(keys):
            raise ValueError("committed forecasts must have nonempty unique keys")
        failure: Exception | None = None
        result: dict[ForecastOutputKey, ForecastId] = {}
        with self._transaction_factory() as txn:
            try:
                txn.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
                txn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
                store = PgForecastStore(
                    txn, transaction_factory=lambda: nullcontext(txn)
                )
                result = {
                    key: store.store_forecast(forecast)
                    for key, forecast in zip(keys, forecasts, strict=True)
                }
            except Exception as exc:
                # Only a successful rollback AND clean context exit are proof.
                # Rollback/exit/commit errors themselves remain ambiguous.
                txn.rollback()
                failure = exc
        if failure is not None:
            raise DailyOutputRolledBackError(
                "daily output transaction rolled back"
            ) from failure
        return result
