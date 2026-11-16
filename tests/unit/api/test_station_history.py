from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from sapphire_flow.api.cors import ScopedCorsMiddleware
from sapphire_flow.api.deps import get_observation_clock
from sapphire_flow.api.errors import unhandled_exception_handler
from sapphire_flow.api.publication_cache import PublicationCacheMiddleware
from sapphire_flow.api.routes.api_station_history import router
from sapphire_flow.api.security import require_principal
from sapphire_flow.api.station_history import get_station_history_reader
from sapphire_flow.services.station_history import build_station_history

if TYPE_CHECKING:
    from sapphire_flow.types.station_history import (
        StationHistoryRequest,
        StationHistoryResponse,
    )


SID = uuid4()
PATH = f"/api/v1/stations/{SID}/observation-history"
QUERY = "parameter=discharge&start=2026-11-15T00:00:00Z&end=2026-11-16T00:00:00Z"


class Reader:
    def __init__(self) -> None:
        self.requests: list[StationHistoryRequest] = []

    def read(self, request: StationHistoryRequest) -> StationHistoryResponse:
        self.requests.append(request)
        return build_station_history(request, ())


@pytest.fixture
def api() -> SimpleNamespace:
    app = FastAPI()
    app.add_middleware(PublicationCacheMiddleware)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(
        ScopedCorsMiddleware,
        consumer_origins=["https://review.example"],
        human_dashboard_origin=None,
    )
    app.include_router(router, dependencies=[Depends(require_principal)])
    reader = Reader()
    app.dependency_overrides[get_station_history_reader] = lambda: reader
    app.dependency_overrides[require_principal] = lambda: object()
    app.dependency_overrides[get_observation_clock] = lambda: datetime(
        2026, 11, 16, tzinfo=UTC
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(app=app, client=client, reader=reader)


class TestStationHistoryRoute:
    def test_utc_normalization_overflow_returns_422(self, api: SimpleNamespace) -> None:
        result = api.client.get(
            PATH,
            params={
                "parameter": "discharge",
                "start": "0001-01-01T00:00:00+14:00",
                "end": "2026-11-16T00:00:00Z",
            },
        )
        assert result.status_code == 422
        assert result.headers["cache-control"] == "no-store"
        assert not api.reader.requests

    @pytest.mark.parametrize(
        "method,origin,status",
        [
            ("GET", "https://review.example", 200),
            ("POST", "https://review.example", 400),
            ("GET", "https://other.example", 400),
        ],
    )
    def test_preflight_no_store(
        self, api: SimpleNamespace, method: str, origin: str, status: int
    ) -> None:
        result = api.client.options(
            PATH, headers={"Origin": origin, "Access-Control-Request-Method": method}
        )
        assert result.status_code == status
        assert result.headers["cache-control"] == "no-store"

    def test_unexpected_failure_is_sanitized_and_no_store(
        self, api: SimpleNamespace
    ) -> None:
        def fail(request: StationHistoryRequest) -> None:
            raise RuntimeError("private diagnostic")

        api.reader.read = fail
        result = api.client.get(PATH + "?" + QUERY)
        assert result.status_code == 500
        assert "private diagnostic" not in result.text
        assert result.headers["cache-control"] == "no-store"

    def test_default_and_both_categories(self, api: SimpleNamespace) -> None:
        result = api.client.get(PATH + "?" + QUERY)
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        assert [s["source"] for s in result.json()["series"]] == [
            "measured",
            "supplied",
        ]
        result = api.client.get(
            PATH + "?" + QUERY + "&data_use=standard&data_use=provisional_rating"
        )
        assert result.status_code == 200
        assert len(result.json()["series"]) == 3

    @pytest.mark.parametrize(
        "suffix",
        [
            "&parameter=discharge",
            "&unknown=x",
            "&data_use=standard&data_use=standard",
            "&data_use=expired_rating_test",
            "&resolution=weekly",
            "&start=bad",
        ],
    )
    def test_invalid_queries_never_read(
        self, api: SimpleNamespace, suffix: str
    ) -> None:
        result = api.client.get(PATH + "?" + QUERY + suffix)
        assert result.status_code == 422
        assert result.headers["cache-control"] == "no-store"
        assert not api.reader.requests

    @pytest.mark.parametrize(
        "query",
        [
            "parameter=temperature&start=2026-11-15&end=2026-11-16",
            "parameter=discharge&start=2026-11-15T00:00:00&end=2026-11-16T00:00:00",
            "parameter=discharge&start=2026-11-01T00:00:00Z&end=2026-11-16T00:00:00Z",
            "parameter=discharge&start=2026-11-15T00:00:00Z&end=2026-11-17T00:00:00Z",
        ],
    )
    def test_bad_intervals_or_parameter(self, api: SimpleNamespace, query: str) -> None:
        assert api.client.get(PATH + "?" + query).status_code == 422
        assert not api.reader.requests

    def test_offsets_normalize(self, api: SimpleNamespace) -> None:
        result = api.client.get(
            PATH,
            params={
                "parameter": "discharge",
                "start": "2026-11-15T05:45:00+05:45",
                "end": "2026-11-16T05:45:00+05:45",
            },
        )
        assert result.status_code == 200
        assert result.json()["start"] == "2026-11-15T00:00:00Z"

    def test_auth_precedes_validation(self, api: SimpleNamespace) -> None:
        def deny() -> None:
            raise HTTPException(401, "unauthorized")

        api.app.dependency_overrides[require_principal] = deny
        result = api.client.get(PATH + "?start=bad")
        assert result.status_code == 401
        assert result.headers["cache-control"] == "no-store"
        assert not api.reader.requests

    def test_wrong_method_no_store(self, api: SimpleNamespace) -> None:
        result = api.client.post(PATH)
        assert result.status_code == 405
        assert result.headers["cache-control"] == "no-store"
