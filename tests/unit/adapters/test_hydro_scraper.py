from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

import httpx
import pytest
import structlog.testing

from sapphire_flow.adapters.hydro_scraper import HydroScraperAdapter
from sapphire_flow.adapters.lindas_rate_limiter import (
    LindasLimiterConfig,
    TokenBucketLindasLimiter,
)
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import FetchOutcomeCause, StationKind
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from sapphire_flow.types.observation import StationFetchOutcome

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


def _coherent_limiter(*, max_retries: int) -> TokenBucketLindasLimiter:
    """Limiter whose injected sleeper ADVANCES its injected clock.

    A no-op sleeper paired with the real clock is an incoherent time source:
    the limiter's wait budget drains while the bucket never refills, so a
    post-429 drain can never be waited out. Production never sees this
    (`time.sleep` and `datetime.now` always agree); only the DI seam can. Here
    the sleep costs no real time but time still MOVES, which is what the
    limiter's arithmetic assumes.
    """
    now = [ensure_utc(datetime(2026, 8, 17, 8, 0, 0, tzinfo=UTC))]

    def clock() -> UtcDatetime:
        return now[0]

    def sleeper(seconds: float) -> None:
        now[0] = ensure_utc(now[0] + timedelta(seconds=seconds))

    return TokenBucketLindasLimiter(
        config=LindasLimiterConfig(max_attempts=max_retries + 1),
        clock=clock,
        sleeper=sleeper,
    )


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
        max_retries=max_retries,
        limiter=_coherent_limiter(max_retries=max_retries),
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
        max_retries=max_retries,
        limiter=_coherent_limiter(max_retries=max_retries),
    )


# Plan 186 T2 — the whole-graph response shape: every triple now carries its
# own ?subject (unlike the old per-station response, which BIND-ed one
# subject and never projected it).
_DIM = "https://environment.ld.admin.ch/foen/hydro/dimension"
_RIVER_BASE = "https://environment.ld.admin.ch/foen/hydro/river/observation"
_LAKE_BASE = "https://environment.ld.admin.ch/foen/hydro/lake/observation"


def _triple(subject: str, predicate: str, obj: str) -> dict[str, object]:
    return {
        "subject": {"type": "uri", "value": subject},
        "predicate": {"type": "uri", "value": predicate},
        "object": {"type": "literal", "value": obj},
    }


def _river_triples(
    code: str,
    ts: str = "2026-04-17T00:00:00Z",
    *,
    discharge: str | None = "42.0",
    water_level: str | None = None,
    water_temperature: str | None = None,
) -> list[dict[str, object]]:
    subject = f"{_RIVER_BASE}/{code}"
    triples = [_triple(subject, f"{_DIM}/measurementTime", ts)]
    if discharge is not None:
        triples.append(_triple(subject, f"{_DIM}/discharge", discharge))
    if water_level is not None:
        triples.append(_triple(subject, f"{_DIM}/waterLevel", water_level))
    if water_temperature is not None:
        triples.append(_triple(subject, f"{_DIM}/waterTemperature", water_temperature))
    return triples


def _lake_triples(
    code: str,
    ts: str = "2026-04-17T00:00:00Z",
    *,
    water_level: str = "394.2",
) -> list[dict[str, object]]:
    subject = f"{_LAKE_BASE}/{code}"
    return [
        _triple(subject, f"{_DIM}/measurementTime", ts),
        _triple(subject, f"{_DIM}/waterLevel", water_level),
    ]


def _whole_graph_response(bindings: list[dict[str, object]]) -> dict[str, object]:
    return {"results": {"bindings": bindings}}


def _fetch_one_outcome(
    adapter: HydroScraperAdapter, code: str = "2044"
) -> StationFetchOutcome:
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
        bindings = _river_triples("2044", ts="not-a-timestamp", discharge="42.0")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(bindings))

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE


class TestTypedFailuresLogAsFetchFailedNotCompleted:
    """Minor fix: a NO_DATA or MALFORMED_RESPONSE outcome from
    `_parse_bindings_typed` used to still log `observation.fetch_completed`
    unconditionally — telling an operator (and the list-façade callers, e.g.
    `record_fixtures.py`, that only ever see `[]`) that the fetch succeeded
    while the typed outcome, `HydroScraperBatchResult.failed`, and
    `ingest_observations_flow`'s `stations_fetch_failed` count all
    simultaneously treat it as a failure. `_fetch_one` must emit
    `observation.fetch_failed` (carrying `failure_cause`) for BOTH typed
    failure causes and never `fetch_completed` for either."""

    def test_no_data_logs_fetch_failed_not_fetch_completed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": {"bindings": []}})

        adapter = _batch_adapter(httpx.MockTransport(handler))
        with structlog.testing.capture_logs() as captured:
            outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.NO_DATA
        events = [e["event"] for e in captured]
        assert "observation.fetch_failed" in events
        assert "observation.fetch_completed" not in events
        (failed,) = [e for e in captured if e["event"] == "observation.fetch_failed"]
        assert failed["log_level"] == "warning"
        assert failed["failure_cause"] == FetchOutcomeCause.NO_DATA.value

    def test_malformed_response_logs_fetch_failed_not_fetch_completed(self) -> None:
        bindings = _river_triples("2044", ts="not-a-timestamp", discharge="42.0")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(bindings))

        adapter = _batch_adapter(httpx.MockTransport(handler))
        with structlog.testing.capture_logs() as captured:
            outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE
        events = [e["event"] for e in captured]
        assert "observation.fetch_failed" in events
        assert "observation.fetch_completed" not in events
        (failed,) = [e for e in captured if e["event"] == "observation.fetch_failed"]
        assert failed["log_level"] == "warning"
        assert failed["failure_cause"] == FetchOutcomeCause.MALFORMED_RESPONSE.value

    def test_clean_fetch_still_logs_fetch_completed_not_fetch_failed(self) -> None:
        # Guards the other direction: the fix must not turn a genuinely
        # successful fetch into a `fetch_failed` too.
        bindings = _river_triples("2044", ts="2026-04-17T00:00:00Z", discharge="42.0")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(bindings))

        adapter = _batch_adapter(httpx.MockTransport(handler))
        with structlog.testing.capture_logs() as captured:
            outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is None
        assert len(outcome.observations) == 1
        events = [e["event"] for e in captured]
        assert "observation.fetch_completed" in events
        assert "observation.fetch_failed" not in events


class TestMalformedBindingsSurviveAsTypedFailures:
    """Major fix: malformed bindings used to escape `_parse_bindings_typed`
    as an uncaught KeyError/TypeError/ValueError, aborting the whole batch
    (and every other station in it) before any health record was written."""

    def test_bindings_missing_predicate_key(self) -> None:
        # A binding attributable to a requested subject (has "subject") but
        # missing "predicate" — subject-local malformed, not NO_DATA.
        subject = f"{_RIVER_BASE}/2044"
        bindings = [{"subject": {"value": subject}, "object": {"value": "x"}}]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(bindings))

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
        bindings = _river_triples(
            "2044", ts="2024-06-15T10:00:00+00:00", discharge="not-a-number"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(bindings))

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE

    def test_binding_item_not_attributable_to_any_subject_is_ignored(self) -> None:
        # Plan 186 D2: a raw item that cannot be attributed to a subject (not
        # even a dict) is not a per-station fault — there is no station to
        # blame it on — so `_group_by_requested_subject` never groups it and
        # it is simply never looked at. It must not corrupt or fail the
        # station whose OWN triples are interleaved right next to it.
        bindings = ["not-a-dict", *_river_triples("2044")]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(bindings))

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter)

        assert outcome.failure_cause is None
        assert len(outcome.observations) == 1

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


def _river_stations(n: int, *, prefix: str = "9") -> list:  # type: ignore[type-arg]
    """`n` distinct river StationConfigs — `make_station_config` defaults to
    a FIXED rng seed, so a distinct `rng` per call is required or every
    station collapses onto the same id."""
    return [
        make_station_config(code=f"{prefix}{i:03d}", rng=random.Random(i))
        for i in range(n)
    ]


class TestFlatInStationCount:
    """Plan 186 D1/T2/T4 — the plan's headline acceptance criterion: ingest
    cost is ONE logical batch fetch per run, independent of station count."""

    def test_flat_in_station_count_exactly_one_request_for_many_stations(
        self,
    ) -> None:
        request_count = 0
        stations = _river_stations(10)
        bindings = [t for s in stations for t in _river_triples(s.code)]

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_whole_graph_response(bindings))

        # max_retries=0 isolates the invariant from the retry contract: this
        # locks "one logical batch fetch", not "one HTTP attempt ever" (a
        # persistent 429 legitimately costs more attempts — see below).
        adapter = _batch_adapter(httpx.MockTransport(handler), max_retries=0)
        since = {s.id: _SINCE for s in stations}

        result = adapter.fetch_observations_batch(stations, since)

        assert request_count == 1
        assert len(result.outcomes) == len(stations)
        assert all(o.failure_cause is None for o in result.outcomes)

    def test_retry_attempts_constant_in_station_count_not_multiplied(self) -> None:
        # T2: the retry contract is RETAINED — a persistent 429 still costs
        # `max_retries + 1` HTTP attempts, but that count must not scale
        # with the number of stations in the batch (a per-station retry
        # loop would multiply it).
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(429)

        max_retries = 2
        stations = _river_stations(5)
        adapter = _batch_adapter(httpx.MockTransport(handler), max_retries=max_retries)
        since = {s.id: _SINCE for s in stations}

        result = adapter.fetch_observations_batch(stations, since)

        assert attempts == max_retries + 1
        assert len(result.outcomes) == len(stations)
        assert all(
            o.failure_cause is FetchOutcomeCause.RATE_LIMITED for o in result.outcomes
        )


class TestWholeBatchCausesMultiStation:
    """D4: transport/HTTP causes are now WHOLE-BATCH — every eligible
    station gets the same cause from one failed request, a real change in
    kind from the old per-station system."""

    @pytest.mark.parametrize(
        ("status", "expected_cause"),
        [
            (403, FetchOutcomeCause.HTTP_STATUS_ERROR),
            (503, FetchOutcomeCause.HTTP_STATUS_ERROR),
        ],
    )
    def test_http_status_failure_broadcasts_to_every_eligible_station(
        self, status: int, expected_cause: FetchOutcomeCause
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status)

        stations = _river_stations(4)
        adapter = _batch_adapter(httpx.MockTransport(handler), max_retries=1)
        since = {s.id: _SINCE for s in stations}

        result = adapter.fetch_observations_batch(stations, since)

        assert len(result.outcomes) == len(stations)
        assert all(o.failure_cause is expected_cause for o in result.outcomes)
        assert all(o.observations == () for o in result.outcomes)

    def test_transport_exhaustion_broadcasts_to_every_eligible_station(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        stations = _river_stations(4)
        adapter = _batch_adapter(httpx.MockTransport(handler), max_retries=1)
        since = {s.id: _SINCE for s in stations}

        result = adapter.fetch_observations_batch(stations, since)

        assert len(result.outcomes) == len(stations)
        assert all(
            o.failure_cause is FetchOutcomeCause.TRANSPORT_ERROR
            for o in result.outcomes
        )


class TestSubjectLocalIsolation:
    """D4: NO_DATA and MALFORMED_RESPONSE stay strictly subject-local — the
    mutant most likely to survive a thinner suite is one that broadcasts a
    subject-local failure to every station instead of isolating it."""

    def test_malformed_binding_for_one_gauge_leaves_the_other_successful(
        self,
    ) -> None:
        station_a = make_station_config(code="5001", rng=random.Random(1))
        station_b = make_station_config(code="5002", rng=random.Random(2))
        bindings = [
            *_river_triples("5001", ts="not-a-timestamp", discharge="42.0"),
            *_river_triples("5002", ts="2026-04-17T00:00:00Z", discharge="10.0"),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(bindings))

        adapter = _batch_adapter(httpx.MockTransport(handler))
        since = {station_a.id: _SINCE, station_b.id: _SINCE}

        result = adapter.fetch_observations_batch([station_a, station_b], since)

        outcomes_by_station = {o.station_id: o for o in result.outcomes}
        assert (
            outcomes_by_station[station_a.id].failure_cause
            is FetchOutcomeCause.MALFORMED_RESPONSE
        )
        assert outcomes_by_station[station_b.id].failure_cause is None
        assert len(outcomes_by_station[station_b.id].observations) == 1

    def test_nonnumeric_value_for_one_gauge_leaves_the_other_successful(self) -> None:
        station_a = make_station_config(code="5003", rng=random.Random(3))
        station_b = make_station_config(code="5004", rng=random.Random(4))
        bindings = [
            *_river_triples("5003", discharge="not-a-number"),
            *_river_triples("5004", discharge="10.0"),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(bindings))

        adapter = _batch_adapter(httpx.MockTransport(handler))
        since = {station_a.id: _SINCE, station_b.id: _SINCE}

        result = adapter.fetch_observations_batch([station_a, station_b], since)

        outcomes_by_station = {o.station_id: o for o in result.outcomes}
        assert (
            outcomes_by_station[station_a.id].failure_cause
            is FetchOutcomeCause.MALFORMED_RESPONSE
        )
        assert outcomes_by_station[station_b.id].failure_cause is None


class TestNoDataShapes:
    def test_subject_absent_from_graph_yields_no_data(self) -> None:
        # A code that matches no gauge in the response (D4 step 2) — the
        # graph has data, just not for this subject.
        other_bindings = _river_triples("6002")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(other_bindings))

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter, code="6001")

        assert outcome.failure_cause is FetchOutcomeCause.NO_DATA

    def test_subject_present_with_measurement_time_only_yields_no_data(self) -> None:
        bindings = _river_triples("6003", discharge=None)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(bindings))

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter, code="6003")

        assert outcome.failure_cause is FetchOutcomeCause.NO_DATA


class TestToleratedUnrecognizedPredicate:
    def test_unrecognized_predicate_alongside_valid_parameter_still_succeeds(
        self,
    ) -> None:
        subject = f"{_RIVER_BASE}/7001"
        bindings = [
            *_river_triples("7001", discharge="42.0"),
            _triple(subject, f"{_DIM}/someNewPredicate", "1.0"),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_whole_graph_response(bindings))

        adapter = _batch_adapter(httpx.MockTransport(handler))
        outcome = _fetch_one_outcome(adapter, code="7001")

        assert outcome.failure_cause is None
        assert {o.parameter for o in outcome.observations} == {"discharge"}


class TestPrecedence:
    """D4/Q2 — weather is skipped entirely (no outcome, no request); an
    unmatched code is now a lookup MISS (`NO_DATA`) rather than a
    pre-request `MALFORMED_RESPONSE`, since ingest no longer interpolates
    the code into SPARQL at all."""

    def test_mixed_batch_under_429_weather_skipped_valid_rate_limited(self) -> None:
        weather = make_station_config(
            code="WEATHER01", station_kind=StationKind.WEATHER, rng=random.Random(11)
        )
        unmatched = make_station_config(code="UNMATCHED", rng=random.Random(12))
        valid = make_station_config(code="8001", rng=random.Random(13))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429)

        adapter = _batch_adapter(httpx.MockTransport(handler), max_retries=1)
        since = {s.id: _SINCE for s in (weather, unmatched, valid)}

        result = adapter.fetch_observations_batch([weather, unmatched, valid], since)

        outcome_station_ids = {o.station_id for o in result.outcomes}
        assert weather.id not in outcome_station_ids
        assert len(result.outcomes) == 2
        assert all(
            o.failure_cause is FetchOutcomeCause.RATE_LIMITED for o in result.outcomes
        )

    def test_all_weather_batch_makes_zero_requests(self) -> None:
        request_made = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_made
            request_made = True
            return httpx.Response(200, json=_whole_graph_response([]))

        weather_stations = [
            make_station_config(
                code=f"WEATHER{i}",
                station_kind=StationKind.WEATHER,
                rng=random.Random(20 + i),
            )
            for i in range(3)
        ]
        adapter = _batch_adapter(httpx.MockTransport(handler))
        since = {s.id: _SINCE for s in weather_stations}

        result = adapter.fetch_observations_batch(weather_stations, since)

        assert not request_made
        assert result.outcomes == ()

    def test_only_unmatched_codes_still_makes_one_fetch_and_returns_no_data_each(
        self,
    ) -> None:
        # The Q2 behaviour change, easiest to regress back to
        # MALFORMED_RESPONSE by reflex: an unmatched code no longer
        # short-circuits pre-request. It is a lookup miss in the response,
        # so a batch of ONLY unmatched codes still makes exactly one fetch.
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_whole_graph_response([]))

        stations = [
            make_station_config(code=f"NOMATCH{i}", rng=random.Random(30 + i))
            for i in range(2)
        ]
        adapter = _batch_adapter(httpx.MockTransport(handler))
        since = {s.id: _SINCE for s in stations}

        result = adapter.fetch_observations_batch(stations, since)

        assert request_count == 1
        assert len(result.outcomes) == 2
        assert all(
            o.failure_cause is FetchOutcomeCause.NO_DATA for o in result.outcomes
        )


class TestVerifyGaugeGuardSurvivesQ2:
    """Q2: deleting the per-station INGEST path must not weaken the
    onboarding-time injection guard, which lives on in `_build_sparql_query`
    / `verify_gauge_reachable` only."""

    def test_verify_gauge_reachable_still_rejects_invalid_site_code(self) -> None:
        adapter = _make_adapter(
            httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
        with pytest.raises(ValueError, match="Invalid site_code"):
            adapter.verify_gauge_reachable("'; DROP TABLE", StationKind.RIVER)
