from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from sapphire_flow.adapters.hydro_scraper import HydroScraperAdapter
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import FetchOutcomeCause, StationKind
from tests.conftest import make_station_config

_ENDPOINT = "https://lindas.admin.ch/query"
_SINCE: UtcDatetime = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))


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


def _make_adapter(
    handler: httpx.MockTransport, *, max_retries: int = 2
) -> HydroScraperAdapter:
    client = httpx.Client(transport=handler)
    # A no-op sleeper: several tests below now exercise real limiter retries
    # (a 500/429 is retryable), and a unit test must never perform real
    # multi-second sleeps waiting for the retry cap.
    return HydroScraperAdapter(
        endpoint=_ENDPOINT,
        http_client=client,
        sleeper=lambda _seconds: None,
        max_retries=max_retries,
    )


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

    def test_top_level_response_is_a_list_returns_false(self) -> None:
        # Major fix (round 2): same wrong-shaped-envelope TypeError gap as
        # `_fetch_one` — `[]["results"]` raises TypeError, not KeyError.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        adapter = _make_adapter(httpx.MockTransport(handler))
        assert adapter.verify_gauge_reachable("2091", StationKind.RIVER) is False

    def test_results_key_is_null_returns_false(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": None})

        adapter = _make_adapter(httpx.MockTransport(handler))
        assert adapter.verify_gauge_reachable("2091", StationKind.RIVER) is False

    def test_429_then_200_is_paced_through_the_shared_limiter(self) -> None:
        """Major fix: this probe used to POST directly, bypassing the shared
        limiter — a transient 429 was reported straight back as
        'unreachable' instead of being retried like every other LINDAS
        caller. Exact attempt count proves it now goes through `call()`."""
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(429)
            return httpx.Response(200, json=_sparql_body_with_bindings(1))

        adapter = _make_adapter(httpx.MockTransport(handler))
        assert adapter.verify_gauge_reachable("2091", StationKind.RIVER) is True
        assert attempts == 2

    def test_429_exhaustion_returns_false_not_unreachable_on_first_429(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        adapter = _make_adapter(httpx.MockTransport(handler), max_retries=1)
        assert adapter.verify_gauge_reachable("2091", StationKind.RIVER) is False

    def test_persistent_transport_error_raises_adapter_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        adapter = _make_adapter(httpx.MockTransport(handler), max_retries=1)
        with pytest.raises(AdapterError, match="LINDAS probe network failure"):
            adapter.verify_gauge_reachable("2091", StationKind.RIVER)


def _batch_adapter(
    handler: httpx.MockTransport, *, max_retries: int = 1
) -> HydroScraperAdapter:
    client = httpx.Client(transport=handler)
    return HydroScraperAdapter(
        endpoint=_ENDPOINT,
        http_client=client,
        sleeper=lambda _seconds: None,
        max_retries=max_retries,
    )


def _fetch_one_outcome(adapter: HydroScraperAdapter, code: str = "2044"):  # type: ignore[no-untyped-def]
    station = make_station_config(code=code)
    result = adapter.fetch_observations_batch([station], {station.id: _SINCE})
    assert len(result.outcomes) == 1
    return result.outcomes[0]


class TestBatchFailureCauseMapping:
    """Major fix (D9 taxonomy): exercises the REAL `_fetch_one` cause
    mapping through `fetch_observations_batch` with a mocked HTTP transport
    — the pre-existing tests only asserted on the `fetch_observations` list
    façade (`obs == []`), which cannot distinguish WHY a station failed.
    Verified by mutation: swapping RATE_LIMITED/HTTP_STATUS_ERROR in
    `hydro_scraper.py`'s cause ternary must break one of these."""

    def test_exhausted_429_maps_to_rate_limited(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.RATE_LIMITED
        assert outcome.observations == ()

    def test_exhausted_503_maps_to_http_status_error_and_carries_the_code(
        self,
    ) -> None:
        # Also locks the minor fix: the exhausted failure_detail must carry
        # the last HTTP status, not just a generic "exhausted" message.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.HTTP_STATUS_ERROR
        assert outcome.failure_detail is not None
        assert "503" in outcome.failure_detail

    def test_non_retryable_403_maps_to_http_status_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.HTTP_STATUS_ERROR

    def test_persistent_transport_error_maps_to_transport_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.TRANSPORT_ERROR

    def test_empty_bindings_maps_to_no_data(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": {"bindings": []}})

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.NO_DATA
        assert outcome.observations == ()

    def test_unparseable_timestamp_maps_to_malformed_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "predicate": {
                                    "value": (
                                        "https://environment.ld.admin.ch/foen/hydro"
                                        "/dimension/measurementTime"
                                    )
                                },
                                "object": {"value": "not-a-timestamp"},
                            },
                            {
                                "predicate": {
                                    "value": (
                                        "https://environment.ld.admin.ch/foen/hydro"
                                        "/dimension/discharge"
                                    )
                                },
                                "object": {"value": "42.0"},
                            },
                        ]
                    }
                },
            )

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE


class TestMalformedBindingsSurviveAsTypedFailures:
    """Major fix: malformed bindings used to escape `_parse_bindings_typed`
    as an uncaught KeyError/TypeError/ValueError, aborting the whole batch
    (and every other station in it) before any health record was written."""

    def test_bindings_missing_predicate_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"results": {"bindings": [{"object": {"value": "x"}}]}}
            )

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE

    def test_bindings_is_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": {"bindings": None}})

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE

    def test_bindings_is_not_a_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": {"bindings": {"oops": 1}}})

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE

    def test_nonnumeric_param_value(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            dim = "https://environment.ld.admin.ch/foen/hydro/dimension"
            return httpx.Response(
                200,
                json={
                    "results": {
                        "bindings": [
                            {
                                "predicate": {"value": f"{dim}/measurementTime"},
                                "object": {"value": "2024-06-15T10:00:00+00:00"},
                            },
                            {
                                "predicate": {"value": f"{dim}/discharge"},
                                "object": {"value": "not-a-number"},
                            },
                        ]
                    }
                },
            )

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE

    def test_binding_item_is_not_a_dict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": {"bindings": ["not-a-dict"]}})

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE

    def test_top_level_response_is_a_list(self) -> None:
        # Major fix (round 2): `response.json()` returning a top-level LIST
        # is valid JSON but `[]["results"]` raises TypeError (list indices
        # must be integers), not KeyError/ValueError — previously escaped
        # the `except (ValueError, KeyError)` guard entirely.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE

    def test_results_key_is_null(self) -> None:
        # Major fix (round 2): `{"results": null}` extracts cleanly to
        # `None`, then `None["bindings"]` raises TypeError.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": None})

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE

    def test_results_key_is_a_list(self) -> None:
        # Major fix (round 2): `{"results": []}` extracts to a LIST, then
        # `[]["bindings"]` raises TypeError (same class of bug, one level
        # deeper than the top-level-list case above).
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": []})

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE
