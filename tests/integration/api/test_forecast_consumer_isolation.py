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


class TestPgPublicationConsumerIsolation:
    @pytest.mark.parametrize(
        "route", ["review", "publish", "withdraw", "latest", "history", "list"]
    )
    def test_test_dependency_refuses_before_forecast_or_publication_queries(
        self,
        db_connection: sa.Connection,
        route: str,
    ) -> None:
        from sapphire_flow.api.routes.forecast_publication import (
            get_review_forecast,
            latest_published_forecast,
            list_review_forecasts,
            publication_history,
            publish_forecast,
            withdraw_forecast,
        )
        from sapphire_flow.api.schemas import (
            PublishForecastRequest,
            WithdrawForecastRequest,
        )
        from sapphire_flow.store.forecast_publication_store import (
            PgForecastPublicationStore,
        )
        from sapphire_flow.store.forecast_store import PgForecastStore
        from sapphire_flow.store.station_store import PgStationStore
        from sapphire_flow.types.enums import ForecastDataUse
        from sapphire_flow.types.human_auth import (
            HumanPermission,
            HumanPrincipal,
            StationGrant,
        )
        from sapphire_flow.types.ids import ForecastId, PublicationDecisionId, UserId
        from tests.integration.store.test_rejected_forecast_store import _seed_station
        from tests.unit.api.test_forecast_publication_api import ISSUED

        sid = _seed_station(db_connection)
        station_store = PgStationStore(db_connection)
        station = station_store.fetch_station(sid)
        assert station is not None
        human = HumanPrincipal(
            user_id=UserId(uuid4()),
            tenant_id=station.tenant_id,
            grants=frozenset(
                StationGrant(station_id=sid, permission=p)
                for p in (HumanPermission.REVIEW, HumanPermission.PUBLISH)
            ),
        )
        stores = {
            "forecast_store": PgForecastStore(
                db_connection, data_use=ForecastDataUse.EXPIRED_RATING_TEST
            ),
            "station_store": station_store,
            "publication_store": PgForecastPublicationStore(db_connection),
        }
        gate = PublicationGate(active_tenant_ids=frozenset({station.tenant_id}))
        fid = ForecastId(uuid4())
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
            if "forecasts" in statement or "forecast_publication" in statement:
                raise AssertionError("forecast/publication SQL before purpose refusal")

        sa.event.listen(db_connection, "before_cursor_execute", record_query)
        try:
            with pytest.raises(
                HTTPException,
                match="Forecast review unavailable|Ordinary forecast reads",
            ) as exc:
                if route == "review":
                    get_review_forecast(
                        fid, stores=stores, principal=human, clock=lambda: ISSUED
                    )
                elif route == "publish":
                    publish_forecast(
                        fid,
                        PublishForecastRequest(
                            expected_forecast_version=1, idempotency_key="test"
                        ),
                        stores=stores,
                        principal=human,
                        gate=gate,
                        clock=lambda: ISSUED,
                        decision_id=PublicationDecisionId(uuid4()),
                    )
                elif route == "withdraw":
                    withdraw_forecast(
                        fid,
                        WithdrawForecastRequest(
                            expected_selection_version=1,
                            reason_code="data_error",
                            reason_text="test",
                            idempotency_key="test",
                        ),
                        stores=stores,
                        principal=human,
                        gate=gate,
                        clock=lambda: ISSUED,
                        decision_id=PublicationDecisionId(uuid4()),
                    )
                elif route == "latest":
                    latest_published_forecast(
                        sid,
                        "discharge",
                        stores=stores,
                        principal=_principal(),
                        gate=gate,
                    )
                elif route == "history":
                    publication_history(
                        sid,
                        cursor=None,
                        limit=50,
                        stores=stores,
                        principal=_principal(),
                        gate=gate,
                    )
                else:
                    list_review_forecasts(
                        sid,
                        ISSUED.isoformat(),
                        (ISSUED + timedelta(days=1)).isoformat(),
                        limit=1,
                        offset=999,
                        stores=stores,
                        principal=human,
                        clock=lambda: ISSUED,
                    )
            assert exc.value.status_code == 503
            if route in {"latest", "history", "list"}:
                assert any("stations" in statement for statement in statements)
            else:
                assert statements == []
        finally:
            sa.event.remove(db_connection, "before_cursor_execute", record_query)
