from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sapphire_flow.api.cors import ScopedCorsMiddleware
from sapphire_flow.api.errors import unhandled_exception_handler
from sapphire_flow.api.publication_cache import PublicationCacheMiddleware

_PATHS = [
    "/api/v1/stations",
    "/api/v1/stations/example",
    "/api/v1/stations/example/forecasts",
    "/api/v1/forecasts/example",
]


def cache_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(PublicationCacheMiddleware)
    app.add_middleware(
        ScopedCorsMiddleware,
        consumer_origins=["https://example.invalid"],
        human_dashboard_origin=None,
    )

    def route(fail: bool = False) -> dict:
        if fail:
            raise RuntimeError("private")
        return {"ok": True}

    for path in [
        *_PATHS,
        "/api/v1/forecast-publications",
        "/api/v1/stations/example/forecasts/latest-published",
        "/api/v1/forecasts/example/deeper",
        "/api/v1/stations/example/unrelated",
    ]:
        app.get(path)(route)
    return app


class TestSharedForecastCache:
    @pytest.mark.parametrize("path", _PATHS)
    @pytest.mark.parametrize(
        "suffix, status", [("", 200), ("?fail=true", 500), ("/", 307)]
    )
    def test_success_outer_error_and_slash_redirect(
        self, path: str, suffix: str, status: int
    ) -> None:
        with TestClient(
            cache_app(), raise_server_exceptions=False, follow_redirects=False
        ) as client:
            response = client.get(path + suffix)
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
        assert "private" not in response.text

    @pytest.mark.parametrize("path", _PATHS)
    @pytest.mark.parametrize("suffix", ["", "/"])
    def test_configured_preflight_including_slash(self, path: str, suffix: str) -> None:
        with TestClient(cache_app()) as client:
            response = client.options(
                path + suffix,
                headers={
                    "Origin": "https://example.invalid",
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert (
            response.headers["access-control-allow-origin"] == "https://example.invalid"
        )
        assert response.headers["access-control-allow-methods"] == "GET"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/forecast-publications",
            "/api/v1/forecasts/example/deeper",
            "/api/v1/stations/example/forecasts/latest-published",
        ],
    )
    def test_existing_classification_is_retained(self, path: str) -> None:
        with TestClient(cache_app()) as client:
            assert client.get(path).headers["cache-control"] == "no-store"

    def test_unrelated_deeper_station_route_unchanged(self) -> None:
        with TestClient(cache_app()) as client:
            assert (
                "cache-control"
                not in client.get("/api/v1/stations/example/unrelated").headers
            )
