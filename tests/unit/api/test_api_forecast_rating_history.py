from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from sapphire_flow.api.cors import ScopedCorsMiddleware
from sapphire_flow.api.deps import get_connection
from sapphire_flow.api.errors import unhandled_exception_handler
from sapphire_flow.api.forecast_rating_history import ForecastRatingHistoryReader
from sapphire_flow.api.publication_cache import PublicationCacheMiddleware
from sapphire_flow.api.routes.api_forecast_rating_history import router
from sapphire_flow.api.security import Principal, require_principal
from sapphire_flow.types.enums import AccessTokenRole
from sapphire_flow.types.forecast_rating_history import RatingHistoryEvidence
from sapphire_flow.types.ids import AccessTokenId, ForecastId, StationId, TenantId

FORECAST = ForecastId(UUID(int=1))
STATION = StationId(UUID(int=2))
TENANT = TenantId(UUID(int=3))
ARTIFACT = UUID(int=4)
ISSUED = datetime(2026, 11, 9, 12, tzinfo=UTC)
END = ISSUED.replace(hour=0)
PATH = f"/api/v1/forecasts/{FORECAST}/rating-input-history"
ORIGIN = "https://example.invalid"
REVIEWER = Principal(
    token_id=AccessTokenId(UUID(int=5)),
    role=AccessTokenRole.REVIEWER,
    tenant_id=TENANT,
    station_ids=frozenset({STATION}),
)
EVIDENCE = RatingHistoryEvidence(
    forecast_id=FORECAST,
    station_id=STATION,
    tenant_id=TENANT,
    issued_at=ISSUED,
    artifact_id=ARTIFACT,
    start=END - timedelta(days=7),
    end=END,
    days=(),
    samples=(),
    omissions=(),
)
HEADER = (FORECAST, STATION, TENANT, ISSUED, ARTIFACT)


class Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def one_or_none(self) -> object:
        return self.value


class Connection:
    def __init__(
        self,
        header: object = HEADER,
        evidence: object = EVIDENCE,
        error: Exception | None = None,
    ) -> None:
        self.header = header
        self.evidence = evidence
        self.error = error
        self.calls: list[Any] = []
        self.protected_calls: list[Any] = []

    def execute(self, query: Any) -> Result:
        self.calls.append(query)
        return Result(self.header)

    def scalar(self, query: Any) -> object:
        self.protected_calls.append(query)
        if self.error is not None:
            raise self.error
        return self.evidence


def history_app(conn: Connection, principal: Principal | None = REVIEWER) -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(PublicationCacheMiddleware)
    app.add_middleware(
        ScopedCorsMiddleware,
        consumer_origins=[ORIGIN],
        human_dashboard_origin=None,
    )
    app.include_router(router)
    app.dependency_overrides[get_connection] = lambda: conn
    if principal is not None:
        app.dependency_overrides[require_principal] = lambda: principal
    return app


class TestForecastRatingHistoryReader:
    def test_consumer_denied_before_any_query(self) -> None:
        conn = Connection()
        reader = ForecastRatingHistoryReader(
            conn, replace(REVIEWER, role=AccessTokenRole.CONSUMER)
        )
        with pytest.raises(HTTPException, match="403"):
            reader.read(FORECAST)
        assert conn.calls == []
        assert conn.protected_calls == []

    @pytest.mark.parametrize("scope", ["other_tenant", "other_station"])
    def test_out_of_scope_header_refuses_before_protected_function(
        self, scope: str
    ) -> None:
        principal = replace(
            REVIEWER,
            tenant_id=TenantId(UUID(int=20)) if scope == "other_tenant" else TENANT,
            station_ids=frozenset({StationId(UUID(int=21))})
            if scope == "other_station"
            else REVIEWER.station_ids,
        )
        conn = Connection(header=None)
        with TestClient(history_app(conn, principal)) as client:
            response = client.get(PATH)
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        assert len(conn.calls) == 1
        query = conn.calls[0]
        params = query.compile().params
        assert principal.tenant_id in params.values()
        assert list(principal.station_ids) in params.values()
        assert FORECAST in params.values()
        assert "stations.tenant_id =" in str(query)
        assert "stations.id IN" in str(query)
        assert "provisional_rating" in params.values()
        assert "forecasts.rating_curve_id IS NULL" in str(query)
        assert "input_lineage" not in str(query)
        assert conn.protected_calls == []

    @pytest.mark.parametrize(
        "field", ["forecast_id", "station_id", "tenant_id", "issued_at", "artifact_id"]
    )
    def test_parent_projection_mismatch_is_sanitized_503(self, field: str) -> None:
        # Change the authorized header, leaving evidence internally valid.
        fields = ["forecast_id", "station_id", "tenant_id", "issued_at", "artifact_id"]
        header = list(HEADER)
        header[fields.index(field)] = (
            ISSUED + timedelta(hours=1) if field == "issued_at" else UUID(int=99)
        )
        conn = Connection(header=tuple(header))
        with TestClient(history_app(conn)) as client:
            response = client.get(PATH)
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert str(UUID(int=99)) not in response.text
        assert "disagrees" not in response.text
        assert len(conn.protected_calls) == 1

    def test_success_uses_bounded_function_after_header(self) -> None:
        conn = Connection(evidence=asdict(EVIDENCE))
        with TestClient(history_app(conn)) as client:
            response = client.get(PATH)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["forecast_id"] == str(FORECAST)
        assert body["station_id"] == str(STATION)
        assert body["final_model_tensor"] == "not_retained"
        assert len(body["days"]) == 7
        assert all(day["status"] == "no_retained_derived_day" for day in body["days"])
        assert "tenant_id" not in body
        assert len(conn.calls) == 2
        assert str(conn.calls[1]) == "SET LOCAL statement_timeout = '5s'"
        assert len(conn.protected_calls) == 1
        query = conn.protected_calls[0]
        assert "forecast_rating_input_history" in str(query)
        assert FORECAST in query.compile().params.values()


class TestForecastRatingHistoryCache:
    def test_unauthenticated_no_store(self) -> None:
        conn = Connection()
        with TestClient(history_app(conn, None)) as client:
            response = client.get(PATH)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert conn.protected_calls == []

    def test_consumer_no_store(self) -> None:
        conn = Connection()
        with TestClient(
            history_app(conn, replace(REVIEWER, role=AccessTokenRole.CONSUMER))
        ) as client:
            response = client.get(PATH)
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
        assert conn.calls == conn.protected_calls == []

    def test_invalid_uuid_no_store(self) -> None:
        conn = Connection()
        with TestClient(history_app(conn)) as client:
            response = client.get("/api/v1/forecasts/not-a-uuid/rating-input-history")
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert conn.calls == conn.protected_calls == []

    @pytest.mark.parametrize(
        "error,status",
        [
            (
                OperationalError("private-evidence", {}, Exception("private-evidence")),
                503,
            ),
            (RuntimeError("private-evidence"), 500),
        ],
    )
    def test_errors_sanitized_and_no_store(self, error: Exception, status: int) -> None:
        with TestClient(
            history_app(Connection(error=error)), raise_server_exceptions=False
        ) as client:
            response = client.get(PATH)
        assert response.status_code == status
        assert "private-evidence" not in response.text
        assert response.headers["cache-control"] == "no-store"

    def test_trailing_slash_redirect_no_store(self) -> None:
        conn = Connection()
        with TestClient(history_app(conn), follow_redirects=False) as client:
            response = client.get(PATH + "/")
        assert response.status_code == 307
        assert response.headers["cache-control"] == "no-store"
        assert conn.calls == conn.protected_calls == []

    @pytest.mark.parametrize("suffix", ["", "/"])
    def test_preflight_no_store(self, suffix: str) -> None:
        conn = Connection()
        with TestClient(history_app(conn)) as client:
            response = client.options(
                PATH + suffix,
                headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"},
            )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-methods"] == "GET"
        assert conn.calls == conn.protected_calls == []
