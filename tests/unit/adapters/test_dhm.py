from __future__ import annotations

import json
import json as json_module
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from structlog.testing import capture_logs

from sapphire_flow.adapters.dhm import DhmAdapter
from sapphire_flow.config.dhm import parse_dhm_config
from sapphire_flow.protocols.adapters import BatchStationDataSource, StationDataSource
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import FetchOutcomeCause, ObservationSource
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.station import StationConfig

NOW = ensure_utc(datetime(2026, 5, 5, 18, 10, tzinfo=UTC))
START = ensure_utc(NOW - timedelta(days=1))
CAPTURES = Path(__file__).resolve().parents[3] / "docs/requirements/dhm-api-examples"


def station(code: str = "DHM-1") -> StationConfig:
    return make_station_config(
        code=code,
        rng=random.Random(code),
        network="dhm",
        water_level_unit="m",
        measured_parameters=frozenset({"water_level"}),
    )


def settings(**overrides: object) -> dict[str, object]:
    return {
        "endpoint": "https://dhm.example/api/v1/",
        "max_requests_per_batch": 100,
        "max_bytes_per_batch": 10_000_000,
        "max_batch_seconds": 10_000.0,
        "bindings": [
            {
                "network": "dhm",
                "station_code": "DHM-1",
                "api_station_id": 168,
                "level_reference": "unknown",
            }
        ],
        **overrides,
    }


def reading(value: object = 2.5, **overrides: object) -> dict[str, object]:
    return {
        "station": 168,
        "waterLevel": value,
        "waterLevelOn": "2026-05-05T12:00:00+05:45",
        **overrides,
    }


def response(
    status: int,
    *,
    json: object = None,
    content: bytes = b"",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    body = json_module.dumps(json).encode() if json is not None else content
    return httpx.Response(status, stream=httpx.ByteStream(body), headers=headers)


class TestDhmAdapter:
    def test_captured_day_preserves_every_value_and_timestamp(self) -> None:
        payload = json.loads((CAPTURES / "day.json").read_text())
        # T1 isolates parsing; T2 exercises the captured continuation separately.
        payload["next"] = None
        config = station()
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: response(200, json=payload))
        ) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            assert isinstance(adapter, StationDataSource)
            assert isinstance(adapter, BatchStationDataSource)
            actual = adapter.fetch_observations([config], {config.id: START})
        assert len(actual) == 118
        assert [(o.timestamp, o.value) for o in actual] == [
            (ensure_utc(datetime.fromisoformat(r["waterLevelOn"])), r["waterLevel"])
            for r in payload["results"]
        ]
        assert {(o.station_id, o.parameter, o.source) for o in actual} == {
            (config.id, "water_level", ObservationSource.MEASURED)
        }

    @pytest.mark.parametrize("value, expected", [(0, [0.0]), (None, [])])
    def test_zero_is_measured_and_null_is_missing(
        self, value: object, expected: list[float]
    ) -> None:
        config = station()
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: response(200, json={"results": [reading(value)]})
            )
        ) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            result = adapter.fetch_observations_batch([config], {config.id: START})
        assert [o.value for o in result.observations] == expected
        assert result.failed == ()

    @pytest.mark.parametrize(
        "row",
        [
            reading(True),
            reading("bad"),
            reading(waterLevelOn="2026-05-05T12:00:00"),
            reading(station=True),
            reading(station=999),
            {"station": 168, "waterLevelOn": "2026-05-05T12:00:00+05:45"},
        ],
    )
    def test_malformed_readings_fail_without_partial_observations(
        self, row: dict[str, object]
    ) -> None:
        config = station()
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: response(200, json={"results": [reading(), row]})
            )
        ) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            result = adapter.fetch_observations_batch([config], {config.id: START})
        assert result.observations == []
        assert result.failed[0].failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE


class TestDhmHistory:
    def test_page_budget_is_shared_across_windows(self) -> None:
        config = station()
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return response(200, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(
                    settings(window_hours=1, max_pages_per_station=1)
                ),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            ).fetch_observations_batch([config], {config.id: START})
        assert len(requests) == 0
        assert result.failed[0].failure_cause is FetchOutcomeCause.RESOURCE_LIMIT

    def test_one_transport_failure_does_not_discard_another_station(self) -> None:
        bad, good = station(), station("DHM-2")
        bindings = [
            {
                "network": "dhm",
                "station_code": sc.code,
                "api_station_id": api_id,
                "level_reference": "unknown",
            }
            for sc, api_id in ((bad, 168), (good, 169))
        ]

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.params["station"] == "168":
                raise httpx.ConnectError("unavailable", request=request)
            return response(200, json={"results": [reading(station=169)]})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings(bindings=bindings)),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            ).fetch_observations_batch([good, bad], {bad.id: START, good.id: START})
        assert [o.station_id for o in result.failed] == [bad.id]
        assert [o.station_id for o in result.observations] == [good.id]

    @pytest.mark.parametrize("fault", ["foreign", "station", "time", "repeat", "limit"])
    def test_untrusted_or_unbounded_continuation_fails_station(
        self, fault: str
    ) -> None:
        config = station()
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            continuation = request.url.copy_set_param("offset", len(requests))
            match fault:
                case "foreign":
                    continuation = continuation.copy_with(host="foreign.example")
                case "station":
                    continuation = continuation.copy_set_param("station", 999)
                case "time":
                    continuation = continuation.copy_remove_param("water_level_on__gt")
                case "repeat":
                    continuation = request.url
            return response(
                200, json={"results": [reading()], "next": str(continuation)}
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(
                    settings(max_pages_per_station=1 if fault == "limit" else 3)
                ),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            result = adapter.fetch_observations_batch([config], {config.id: START})
        assert result.observations == []
        assert result.failed[0].failure_cause is (
            FetchOutcomeCause.RESOURCE_LIMIT
            if fault == "limit"
            else FetchOutcomeCause.MALFORMED_RESPONSE
        )
        assert len(requests) == 1

    def test_repeated_content_with_different_urls_is_detected(self) -> None:
        config = station()
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return response(
                200,
                json={
                    "results": [reading()],
                    "next": str(request.url.copy_set_param("offset", len(requests))),
                },
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            ).fetch_observations_batch([config], {config.id: START})
        assert result.failed[0].failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE
        assert len(requests) == 2

    def test_full_page_then_empty_stops_despite_fabricated_next_and_paces(self) -> None:
        config = station()
        request_times: list[float] = []
        elapsed = 0.0

        def sleep(seconds: float) -> None:
            nonlocal elapsed
            elapsed += seconds

        def respond(request: httpx.Request) -> httpx.Response:
            request_times.append(elapsed)
            rows = [reading()] if len(request_times) == 1 else []
            return response(
                200,
                json={
                    "results": rows,
                    "next": str(
                        request.url.copy_set_param("offset", len(request_times))
                    ),
                },
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings(page_size=1)),
                http_client=client,
                clock=lambda: NOW,
                monotonic=lambda: elapsed,
                sleeper=sleep,
            )
            result = adapter.fetch_observations_batch([config], {config.id: START})
        assert len(result.observations) == 1
        assert result.failed == ()
        assert request_times == [0.0, 5.0]

    @pytest.mark.parametrize("conflict", [False, True])
    def test_multi_window_boundary_is_complete_and_conflicts_are_not_chosen(
        self, conflict: bool
    ) -> None:
        config = station()
        start = ensure_utc(NOW - timedelta(hours=2))
        middle = ensure_utc(start + timedelta(hours=1))
        calls = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            assert request.url.path == "/api/v1/river/"
            lower = datetime.fromisoformat(request.url.params["water_level_on__gt"])
            upper = datetime.fromisoformat(request.url.params["water_level_on__lt"])
            rows = [
                reading(
                    3.0 if conflict and calls == 2 and t == middle else 2.0,
                    waterLevelOn=t.isoformat(),
                )
                for t in (
                    start - timedelta(seconds=1),
                    start,
                    middle,
                    NOW,
                    NOW + timedelta(seconds=1),
                )
                if lower <= t <= upper
            ]
            return response(200, json={"results": rows})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings(window_hours=1)),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            ).fetch_observations_batch([config], {config.id: start})
        if conflict:
            assert result.observations == []
            assert (
                result.failed[0].failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE
            )
        else:
            assert [o.timestamp for o in result.observations] == [middle, NOW]
        assert calls == 2

    @pytest.mark.parametrize(
        "status, expected",
        [
            (429, FetchOutcomeCause.RATE_LIMITED),
            (302, FetchOutcomeCause.HTTP_STATUS_ERROR),
            (401, FetchOutcomeCause.HTTP_STATUS_ERROR),
            (503, FetchOutcomeCause.HTTP_STATUS_ERROR),
        ],
    )
    def test_http_failure_does_not_retry_or_follow_redirects(
        self, status: int, expected: FetchOutcomeCause
    ) -> None:
        config = station()
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            assert request.extensions["timeout"]["read"] == 12
            return response(status, headers={"location": "https://foreign.example/"})

        with httpx.Client(
            transport=httpx.MockTransport(respond), follow_redirects=True
        ) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings(timeout_s=12)),
                http_client=client,
                clock=lambda: NOW,
            ).fetch_observations_batch([config], {config.id: START})
        assert len(requests) == 1
        assert result.failed[0].failure_cause is expected

    def test_transport_failure_detail_does_not_expose_credentials(self) -> None:
        config = station()

        def respond(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("secret-token-in-exception", request=request)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
            ).fetch_observations_batch([config], {config.id: START})
        assert result.failed[0].failure_cause is FetchOutcomeCause.TRANSPORT_ERROR
        assert "secret-token" not in str(result)

    @pytest.mark.parametrize(
        "body",
        [
            b'{"results":[{"station":168,"waterLevel":NaN,"waterLevelOn":"2026-05-05T12:00:00Z"}]}',
            b"[]",
            b'{"results":null}',
            b"not json",
        ],
    )
    def test_invalid_json_or_nonfinite_measurement_fails(self, body: bytes) -> None:
        config = station()
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: response(200, content=body))
        ) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
            ).fetch_observations_batch([config], {config.id: START})
        assert result.failed[0].failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE

    def test_short_nonterminal_page_is_followed(self) -> None:
        config = station()
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if "offset" in request.url.params:
                return response(
                    200,
                    json={
                        "results": [
                            reading(3.0, waterLevelOn="2026-05-05T12:10:00+05:45")
                        ],
                        "next": None,
                    },
                )
            return response(
                200,
                json={
                    "results": [reading()],
                    "count": 9223372036854775807,
                    "next": str(request.url.copy_set_param("offset", 1)),
                },
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            result = adapter.fetch_observations_batch([config], {config.id: START})
        assert [o.value for o in result.observations] == [2.5, 3.0]
        assert len(requests) == 2

    def test_later_page_failure_does_not_return_partial_success(self) -> None:
        config = station()

        def respond(request: httpx.Request) -> httpx.Response:
            if "offset" in request.url.params:
                return response(503)
            return response(
                200,
                json={
                    "results": [reading()],
                    "next": str(request.url.copy_set_param("offset", 1)),
                },
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            )
            result = adapter.fetch_observations_batch([config], {config.id: START})
        assert result.observations == []
        assert result.failed[0].failure_cause is FetchOutcomeCause.HTTP_STATUS_ERROR


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


class CountingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes], clock: FakeTime, step: float = 0) -> None:
        self.chunks = chunks
        self.clock = clock
        self.step = step
        self.received = 0
        self.pulled = 0
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.clock.now += self.step
            self.received += len(chunk)
            self.pulled += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


def batch_settings(count: int = 3, **overrides: object) -> dict[str, object]:
    return settings(
        bindings=[
            {
                "network": "dhm",
                "station_code": f"DHM-{i + 1}",
                "api_station_id": 168 + i,
                "level_reference": "unknown",
            }
            for i in range(count)
        ],
        **overrides,
    )


class TestDhmBatchBounds:
    @pytest.mark.parametrize(
        "stop", ["429", "503", "network", "schema", "request", "bytes", "deadline"]
    )
    def test_preserves_completed_station_and_aborts_rest(self, stop: str) -> None:
        clock = FakeTime()
        configs = [station(f"DHM-{i + 1}") for i in range(3)]
        calls: list[int] = []
        body = json.dumps({"results": [reading()]}).encode()

        def respond(request: httpx.Request) -> httpx.Response:
            api_id = int(request.url.params["station"])
            calls.append(api_id)
            if api_id == 168:
                return response(200, content=body)
            if stop in ("429", "503"):
                return response(int(stop), headers={"Retry-After": "100000"})
            if stop == "network":
                raise httpx.ConnectError("private", request=request)
            if stop == "deadline":
                clock.now = 10000
                return response(200, json={"results": []})
            return response(200, content=b"bad")

        # Runtime-only limits: preflight requires three requests and passes.
        overrides = {"max_bytes_per_batch": len(body)} if stop == "bytes" else {}
        if stop == "request":
            # Force an extra page at station 2 to consume the third request.
            def respond(request: httpx.Request) -> httpx.Response:
                api_id = int(request.url.params["station"])
                calls.append(api_id)
                if api_id == 168:
                    return response(200, content=body)
                offset = len(calls)
                return response(
                    200,
                    json={
                        "results": [reading(float(offset), station=169)],
                        "next": str(request.url.copy_set_param("offset", offset)),
                    },
                )

            overrides = {"max_requests_per_batch": 3}
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(batch_settings(**overrides)),
                http_client=client,
                clock=lambda: NOW,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            ).fetch_observations_batch(configs, {s.id: START for s in configs})
        expected = {
            "429": FetchOutcomeCause.RATE_LIMITED,
            "503": FetchOutcomeCause.HTTP_STATUS_ERROR,
            "network": FetchOutcomeCause.TRANSPORT_ERROR,
            "schema": FetchOutcomeCause.MALFORMED_RESPONSE,
            "request": FetchOutcomeCause.RESOURCE_LIMIT,
            "bytes": FetchOutcomeCause.RESOURCE_LIMIT,
            "deadline": FetchOutcomeCause.RESOURCE_LIMIT,
        }[stop]
        assert [o.failure_cause for o in result.outcomes] == [
            None,
            expected,
            FetchOutcomeCause.BATCH_ABORTED,
        ]
        assert [o.station_id for o in result.observations] == [configs[0].id]
        assert 170 not in calls
        assert all(wait == 5 for wait in clock.waits)

    @pytest.mark.parametrize(
        "limit, delta", [(2 * 1024 * 1024, 0), (2 * 1024 * 1024, 1), (128, 1)]
    )
    def test_raw_chunk_accounting_and_closure(self, limit: int, delta: int) -> None:
        clock = FakeTime()
        body = b'{"results":[]}' + b" " * (limit - len(b'{"results":[]}') + delta)
        stream = CountingStream([body[:10], body[10:], b"never"], clock)
        # Exactly full valid JSON must reach EOF, not the sentinel.
        if delta == 0:
            stream.chunks.pop()
        cfg = station()
        with (
            capture_logs() as events,
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(200, stream=stream)
                )
            ) as client,
        ):
            result = DhmAdapter(
                config=parse_dhm_config(settings(max_bytes_per_batch=limit)),
                http_client=client,
                clock=lambda: NOW,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            ).fetch_observations_batch([cfg], {cfg.id: START})
        summary = next(
            e for e in events if e["event"] == "observation.dhm_batch_finished"
        )
        assert summary["received_bytes"] == len(body)
        assert summary["accepted_bytes"] == (10 if delta else len(body))
        assert summary["requests"] == 1
        assert stream.closed
        assert stream.received == len(body)
        assert stream.pulled == 2
        assert (result.failed[0].failure_cause if result.failed else None) == (
            FetchOutcomeCause.RESOURCE_LIMIT if delta else None
        )

    @pytest.mark.parametrize(
        "failure, elapsed, expected",
        [
            ("timeout", 9, FetchOutcomeCause.TRANSPORT_ERROR),
            ("timeout", 10, FetchOutcomeCause.RESOURCE_LIMIT),
            ("timeout", 11, FetchOutcomeCause.RESOURCE_LIMIT),
            ("network", 11, FetchOutcomeCause.TRANSPORT_ERROR),
            ("429", 11, FetchOutcomeCause.RATE_LIMITED),
            ("503", 11, FetchOutcomeCause.HTTP_STATUS_ERROR),
            ("success", 11, FetchOutcomeCause.RESOURCE_LIMIT),
            ("encoding", 11, FetchOutcomeCause.MALFORMED_RESPONSE),
            ("length", 11, FetchOutcomeCause.MALFORMED_RESPONSE),
        ],
    )
    def test_header_and_timeout_precedence(
        self, failure: str, elapsed: int, expected: FetchOutcomeCause
    ) -> None:
        clock = FakeTime()
        stream = CountingStream([b'{"results":[]}'], clock)
        cfg = station()

        def respond(request: httpx.Request) -> httpx.Response:
            assert request.headers["accept-encoding"] == "identity"
            assert set(request.extensions["timeout"].values()) == {10.0}
            clock.now = elapsed
            if failure == "timeout":
                raise httpx.ReadTimeout("private", request=request)
            if failure == "network":
                raise httpx.RemoteProtocolError("private", request=request)
            headers = {"Content-Encoding": "gzip"} if failure == "encoding" else {}
            if failure == "length":
                headers = {"Content-Length": "-1"}
            return httpx.Response(
                int(failure) if failure.isdigit() else 200,
                stream=stream,
                headers=headers,
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings(max_batch_seconds=10)),
                http_client=client,
                clock=lambda: NOW,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            ).fetch_observations_batch([cfg], {cfg.id: START})
        assert result.failed[0].failure_cause is expected
        assert stream.pulled == 0
        if failure not in ("timeout", "network"):
            assert stream.closed

    def test_subchunk_trickle_checks_each_raw_yield(self) -> None:
        clock = FakeTime()
        stream = CountingStream([b" " for _ in range(100)], clock, step=1)
        cfg = station()
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=stream)
            )
        ) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings(max_batch_seconds=3)),
                http_client=client,
                clock=lambda: NOW,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            ).fetch_observations_batch([cfg], {cfg.id: START})
        assert result.failed[0].failure_cause is FetchOutcomeCause.RESOURCE_LIMIT
        assert stream.pulled == 3
        assert stream.closed

    @pytest.mark.parametrize(
        "mode", ["individual", "aggregate", "pacing_equal", "multiple", "mixed_expired"]
    )
    def test_preflight_rejects_before_any_http(self, mode: str) -> None:
        clock = FakeTime()
        configs = [station(f"DHM-{i + 1}") for i in range(3)]
        since = {s.id: START for s in configs}
        bounds: dict[str, object] = {}
        if mode == "individual":
            bounds = {"window_hours": 1, "max_pages_per_station": 1}
        elif mode == "aggregate":
            bounds = {"max_requests_per_batch": 2}
        elif mode == "pacing_equal":
            bounds = {"max_batch_seconds": 10}
        elif mode == "multiple":
            since.pop(configs[0].id)
            since.pop(configs[1].id)
        else:
            since.pop(configs[0].id)

        def end_clock() -> UtcDatetime:
            if mode == "mixed_expired":
                clock.now = 10000
            return NOW

        calls: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return response(200, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(batch_settings(**bounds)),
                http_client=client,
                clock=end_clock,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            ).fetch_observations_batch(configs, since)
        assert calls == []
        expected = [FetchOutcomeCause.RESOURCE_LIMIT] * 3
        if mode == "multiple":
            expected = [FetchOutcomeCause.CONFIGURATION_ERROR] * 2 + [
                FetchOutcomeCause.BATCH_ABORTED
            ]
        elif mode == "mixed_expired":
            expected[0] = FetchOutcomeCause.CONFIGURATION_ERROR
        assert [o.failure_cause for o in result.outcomes] == expected

    @pytest.mark.parametrize(
        "span, age, expected",
        [
            (1, timedelta(0), 0),
            (1, timedelta(hours=-1), 0),
            (1, timedelta(hours=1), 1),
            (1, timedelta(hours=1, microseconds=1), 2),
            (1.5 / 3600000000, timedelta(microseconds=4), 2),
        ],
    )
    def test_preflight_and_window_parity(
        self, span: float, age: timedelta, expected: int
    ) -> None:
        clock = FakeTime()
        cfg = station()
        calls: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return response(200, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(
                    settings(window_hours=span, max_requests_per_batch=max(1, expected))
                ),
                http_client=client,
                clock=lambda: NOW,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            ).fetch_observations_batch([cfg], {cfg.id: ensure_utc(NOW - age)})
        assert result.failed == ()
        assert len(calls) == expected

    def test_new_explicit_batch_resets_budget_and_uses_one_captured_end(self) -> None:
        clock = FakeTime()
        cfg = station()
        ends: list[object] = []
        calls: list[httpx.Request] = []

        def end_clock() -> UtcDatetime:
            ends.append(NOW)
            return NOW

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return response(200, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            adapter = DhmAdapter(
                config=parse_dhm_config(settings(max_requests_per_batch=1)),
                http_client=client,
                clock=end_clock,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            )
            for _ in range(2):
                assert (
                    adapter.fetch_observations_batch([cfg], {cfg.id: START}).failed
                    == ()
                )
        assert len(calls) == len(ends) == 2

    @pytest.mark.parametrize(
        "headers,expected",
        [
            ({"Content-Length": "2097153"}, FetchOutcomeCause.RESOURCE_LIMIT),
            ({"Content-Length": "100"}, FetchOutcomeCause.RESOURCE_LIMIT),
            ({"Content-Length": "1"}, None),
            ({"Content-Encoding": "br"}, FetchOutcomeCause.MALFORMED_RESPONSE),
        ],
    )
    def test_declared_limits_and_encoding_close_without_reading(
        self, headers: dict[str, str], expected: FetchOutcomeCause | None
    ) -> None:
        clock = FakeTime()
        stream = CountingStream([b'{"results":[]}'], clock)
        cfg = station()
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, headers=headers, stream=stream)
            )
        ) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings(max_bytes_per_batch=32)),
                http_client=client,
                clock=lambda: NOW,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            ).fetch_observations_batch([cfg], {cfg.id: START})
        assert stream.closed
        assert stream.pulled == (0 if expected else 1)
        assert (result.failed[0].failure_cause if result.failed else None) is expected

    def test_runtime_stop_aborts_later_zero_work_station(self) -> None:
        clock = FakeTime()
        configs = [station(f"DHM-{i + 1}") for i in range(3)]
        calls: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            clock.now = 10000
            return response(429)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(batch_settings()),
                http_client=client,
                clock=lambda: NOW,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            ).fetch_observations_batch(
                configs, {configs[0].id: NOW, configs[1].id: START, configs[2].id: NOW}
            )
        assert [o.failure_cause for o in result.outcomes] == [
            None,
            FetchOutcomeCause.RATE_LIMITED,
            FetchOutcomeCause.BATCH_ABORTED,
        ]
        assert len(calls) == 1

    @pytest.mark.parametrize("seconds,expected_calls", [(5.0, 0), (5.001, 2)])
    def test_strict_pacing_lower_bound(
        self, seconds: float, expected_calls: int
    ) -> None:
        clock = FakeTime()
        configs = [station(), station("DHM-2")]
        calls: list[float] = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(clock.now)
            return response(200, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(batch_settings(2, max_batch_seconds=seconds)),
                http_client=client,
                clock=lambda: NOW,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            ).fetch_observations_batch(configs, {s.id: START for s in configs})
        assert len(calls) == expected_calls
        assert bool(result.failed) == (expected_calls == 0)

    def test_midstream_error_closes_and_discards_station(self) -> None:
        clock = FakeTime()

        class BrokenStream(CountingStream):
            def __iter__(self) -> Iterator[bytes]:
                yield b'{"results":['
                raise httpx.ReadError("private")

        stream = BrokenStream([], clock)
        cfg = station()
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=stream)
            )
        ) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings()),
                http_client=client,
                clock=lambda: NOW,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            ).fetch_observations_batch([cfg], {cfg.id: START})
        assert stream.closed
        assert result.observations == []
        assert result.failed[0].failure_cause is FetchOutcomeCause.TRANSPORT_ERROR
