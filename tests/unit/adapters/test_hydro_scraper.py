from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from sapphire_flow.adapters.hydro_scraper import HydroScraperAdapter
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.enums import StationKind

_ENDPOINT = "https://lindas.admin.ch/query"


def _sparql_body_with_bindings(count: int) -> dict[str, object]:
    return {
        "head": {"vars": ["predicate", "object"]},
        "results": {
            "bindings": [
                {
                    "predicate": {
                        "type": "uri",
                        "value": (
                            "https://environment.ld.admin.ch/foen/hydro"
                            "/dimension/measurementTime"
                        ),
                    },
                    "object": {
                        "type": "literal",
                        "value": "2026-04-17T00:00:00Z",
                    },
                }
            ]
            * count
        },
    }


def _make_adapter(handler: httpx.MockTransport) -> HydroScraperAdapter:
    client = httpx.Client(transport=handler)
    return HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)


class TestVerifyGaugeReachable:
    def test_returns_true_on_2xx_with_bindings(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_sparql_body_with_bindings(3),
            )

        adapter = _make_adapter(httpx.MockTransport(handler))
        assert adapter.verify_gauge_reachable("2091", StationKind.RIVER) is True

    def test_returns_false_on_2xx_with_empty_bindings(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_sparql_body_with_bindings(0))

        adapter = _make_adapter(httpx.MockTransport(handler))
        assert adapter.verify_gauge_reachable("9999", StationKind.RIVER) is False

    def test_returns_false_on_404(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        adapter = _make_adapter(httpx.MockTransport(handler))
        assert adapter.verify_gauge_reachable("2091", StationKind.RIVER) is False

    def test_returns_false_on_500(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        adapter = _make_adapter(httpx.MockTransport(handler))
        assert adapter.verify_gauge_reachable("2091", StationKind.RIVER) is False

    def test_raises_adapter_error_on_connect_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="LINDAS probe network failure"):
            adapter.verify_gauge_reachable("2091", StationKind.RIVER)

    def test_river_query_includes_discharge_predicate(self) -> None:
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode("utf-8")
            captured.append(body)
            return httpx.Response(200, json=_sparql_body_with_bindings(1))

        adapter = _make_adapter(httpx.MockTransport(handler))
        adapter.verify_gauge_reachable("2091", StationKind.RIVER)

        assert len(captured) == 1
        parsed = parse_qs(captured[0])
        query = parsed["query"][0]
        assert "discharge" in query
        assert "waterLevel" in query
        assert "waterTemperature" in query
        # Subject URI must use "river" kind path
        assert "/river/observation/2091" in query

    def test_lake_query_excludes_discharge_predicate(self) -> None:
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.content.decode("utf-8")
            captured.append(body)
            return httpx.Response(200, json=_sparql_body_with_bindings(1))

        adapter = _make_adapter(httpx.MockTransport(handler))
        adapter.verify_gauge_reachable("2208", StationKind.LAKE)

        assert len(captured) == 1
        parsed = parse_qs(captured[0])
        query = parsed["query"][0]
        assert "discharge" not in query
        assert "waterTemperature" not in query
        assert "waterLevel" in query
        # Subject URI must use "lake" kind path
        assert "/lake/observation/2208" in query

    def test_returns_false_on_malformed_json(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"not-json",
                headers={"content-type": "application/sparql-results+json"},
            )

        adapter = _make_adapter(httpx.MockTransport(handler))
        assert adapter.verify_gauge_reachable("2091", StationKind.RIVER) is False

    def test_accept_header_is_sparql_json(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("accept", ""))
            return httpx.Response(200, json=_sparql_body_with_bindings(1))

        adapter = _make_adapter(httpx.MockTransport(handler))
        adapter.verify_gauge_reachable("2091", StationKind.RIVER)

        assert seen == ["application/sparql-results+json"]

    def test_response_json_shape_with_missing_results_key_returns_false(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            # Well-formed JSON but missing "results" -> KeyError path
            return httpx.Response(200, content=json.dumps({"head": {}}).encode())

        adapter = _make_adapter(httpx.MockTransport(handler))
        assert adapter.verify_gauge_reachable("2091", StationKind.RIVER) is False
