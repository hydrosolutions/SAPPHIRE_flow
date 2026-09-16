from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from sapphire_flow.adapters.dhm import DhmAdapter
from sapphire_flow.config.dhm import parse_dhm_config
from sapphire_flow.protocols.adapters import BatchStationDataSource, StationDataSource
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import FetchOutcomeCause, ObservationSource
from tests.conftest import make_station_config

if TYPE_CHECKING:
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


class TestDhmAdapter:
    def test_captured_day_preserves_every_value_and_timestamp(self) -> None:
        payload = json.loads((CAPTURES / "day.json").read_text())
        # T1 isolates parsing; T2 exercises the captured continuation separately.
        payload["next"] = None
        config = station()
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            )
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
                lambda request: httpx.Response(200, json={"results": [reading(value)]})
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
                lambda request: httpx.Response(200, json={"results": [reading(), row]})
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
            return httpx.Response(200, json={"results": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(
                    settings(window_hours=1, max_pages_per_station=1)
                ),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            ).fetch_observations_batch([config], {config.id: START})
        assert len(requests) == 1
        assert result.failed[0].failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE

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
            return httpx.Response(200, json={"results": [reading(station=169)]})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = DhmAdapter(
                config=parse_dhm_config(settings(bindings=bindings)),
                http_client=client,
                clock=lambda: NOW,
                sleeper=lambda _: None,
            ).fetch_observations_batch([bad, good], {bad.id: START, good.id: START})
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
            return httpx.Response(
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
        assert result.failed[0].failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE
        assert len(requests) == 1

    def test_repeated_content_with_different_urls_is_detected(self) -> None:
        config = station()
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
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
            return httpx.Response(
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
        assert request_times == [0.0, 1.0]

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
            return httpx.Response(200, json={"results": rows})

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
            return httpx.Response(
                status, headers={"location": "https://foreign.example/"}
            )

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
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=body)
            )
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
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            reading(3.0, waterLevelOn="2026-05-05T12:10:00+05:45")
                        ],
                        "next": None,
                    },
                )
            return httpx.Response(
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
                return httpx.Response(503)
            return httpx.Response(
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
