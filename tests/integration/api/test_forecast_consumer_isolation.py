from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from sapphire_flow.api.publication_gate import PublicationGate
from sapphire_flow.api.routes.api_forecasts import get_forecast
from sapphire_flow.api.routes.api_stations import list_forecasts
from sapphire_flow.api.security import Principal
from sapphire_flow.services.forecast_lab.db_sources import (
    fetch_latest_forecast_for_model,
    fetch_latest_publication_cycle_time,
)
from sapphire_flow.types.enums import AccessTokenRole, ForecastStatus
from sapphire_flow.types.ids import AccessTokenId
from tests.integration.store.test_forecast_data_use import _pair, _stores
from tests.integration.store.test_forecast_data_use import (
    structural_connection as structural_connection,
)


def _principal() -> Principal:
    return Principal(
        token_id=AccessTokenId(uuid4()),
        role=AccessTokenRole.ADMIN,
        tenant_id=None,
        station_ids=frozenset(),
    )


def _page(store: object, ordinary: Any, offset: int = 0) -> Any:
    return list_forecasts(
        str(ordinary.station_id),
        model_id=None,
        parameter=None,
        start=ordinary.issued_at.isoformat(),
        end=(ordinary.issued_at + timedelta(days=2)).isoformat(),
        degraded_only=False,
        limit=1,
        offset=offset,
        stores={"forecast_store": store},
        principal=_principal(),
        gate=PublicationGate(),
    )


class TestPgConsumerIsolation:
    def test_modern_pages_and_detail_keep_only_standard_history(
        self, structural_connection: sa.Connection
    ) -> None:
        ordinary, test = _pair(structural_connection)
        standard_store, test_store = _stores(structural_connection)
        standard_store.store_forecast(ordinary)
        test_store.store_forecast(test)
        standard_store.transition_status(ordinary.id, 1, ForecastStatus.SUPERSEDED)
        page = _page(standard_store, ordinary)
        assert (page.total, [row.id for row in page.items]) == (1, [str(ordinary.id)])
        assert _page(standard_store, ordinary, offset=1).model_dump()["items"] == []
        assert _page(standard_store, ordinary, offset=1).total == 1
        detail = get_forecast(
            str(ordinary.id),
            stores={"forecast_store": standard_store},
            principal=_principal(),
            gate=PublicationGate(),
        )
        assert detail.status == "superseded"
        with pytest.raises(HTTPException, match="Forecast not found"):
            get_forecast(
                str(test.id),
                stores={"forecast_store": standard_store},
                principal=_principal(),
                gate=PublicationGate(),
            )

    def test_newer_test_row_cannot_move_lab_scalar_marker_or_latest(
        self, structural_connection: sa.Connection
    ) -> None:
        ordinary, test = _pair(structural_connection)
        test = replace(test, issued_at=ordinary.issued_at + timedelta(hours=1))
        standard_store, test_store = _stores(structural_connection)
        standard_store.store_forecast(ordinary)
        test_store.store_forecast(test)
        bundle: Any = SimpleNamespace(forecast_store=standard_store)
        assert (
            fetch_latest_publication_cycle_time(bundle, data_cutoff_at=test.issued_at)
            == ordinary.issued_at
        )
        result = fetch_latest_forecast_for_model(
            bundle, ordinary.station_id, ordinary.model_id
        )
        assert result is not None and result.id == ordinary.id

    def test_real_test_dependency_refuses_before_driver_queries(
        self, structural_connection: sa.Connection
    ) -> None:
        ordinary, _ = _pair(structural_connection)
        _, test_store = _stores(structural_connection)

        def forbid_query(*args: object, **kwargs: object) -> None:
            raise AssertionError("driver query before consumer purpose refusal")

        sa.event.listen(structural_connection, "before_execute", forbid_query)
        try:
            with pytest.raises(HTTPException, match="Ordinary forecast reads") as exc:
                _page(test_store, ordinary, offset=999)
            assert exc.value.status_code == 503
            with pytest.raises(HTTPException, match="Ordinary forecast reads") as exc:
                get_forecast(
                    str(ordinary.id),
                    stores={"forecast_store": test_store},
                    principal=_principal(),
                    gate=PublicationGate(),
                )
            assert exc.value.status_code == 503
        finally:
            sa.event.remove(structural_connection, "before_execute", forbid_query)


class TestPgRejectedConsumerIsolation:
    def test_test_purpose_empty_page_refuses_before_rejected_query(
        self, db_connection: sa.Connection
    ) -> None:
        from sapphire_flow.api.routes.api_rejected_forecasts import (
            get_rejected_forecasts,
        )
        from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
        from sapphire_flow.store.station_store import PgStationStore
        from sapphire_flow.types.enums import ForecastDataUse
        from tests.integration.store.test_rejected_forecast_store import _seed_station

        sid = _seed_station(db_connection)
        store = PgRejectedForecastStore(
            db_connection,
            transaction_factory=None,
            data_use=ForecastDataUse.EXPIRED_RATING_TEST,
        )
        statements: list[str] = []

        def record_query(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            statements.append(statement)
            if "rejected_forecasts" in statement:
                raise AssertionError("rejected query before purpose refusal")

        sa.event.listen(db_connection, "before_cursor_execute", record_query)
        try:
            with pytest.raises(HTTPException, match="Ordinary forecast reads") as exc:
                get_rejected_forecasts(
                    str(sid),
                    model_id=None,
                    start=None,
                    end=None,
                    limit=1,
                    offset=999,
                    stores={
                        "station_store": PgStationStore(db_connection),
                        "rejected_forecast_store": store,
                    },
                    principal=_principal(),
                    gate=PublicationGate(),
                )
            assert exc.value.status_code == 503
            assert any("stations" in statement for statement in statements)
            assert all(
                "rejected_forecasts" not in statement for statement in statements
            )
        finally:
            sa.event.remove(db_connection, "before_cursor_execute", record_query)
