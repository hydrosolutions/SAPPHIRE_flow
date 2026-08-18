from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest

from sapphire_flow.adapters.hydro_scraper import HydroScraperAdapter
from sapphire_flow.adapters.lindas_rate_limiter import (
    LindasLimiterConfig,
    TokenBucketLindasLimiter,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import GeoCoord
from sapphire_flow.types.enums import (
    ObservationSource,
    StationKind,
    StationOwnership,
    StationStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationConfig
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

_ENDPOINT = "https://ld.admin.ch/query"
_FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"

_SINCE = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))

_DIM = "https://environment.ld.admin.ch/foen/hydro/dimension"
_RIVER_BASE = "https://environment.ld.admin.ch/foen/hydro/river/observation"
_LAKE_BASE = "https://environment.ld.admin.ch/foen/hydro/lake/observation"

_STATION_1_ID = StationId(UUID("00000000-0000-0000-0000-000000000001"))
_STATION_2_ID = StationId(UUID("00000000-0000-0000-0000-000000000002"))


def _make_station(
    station_id: StationId,
    code: str,
    station_kind: StationKind = StationKind.RIVER,
) -> StationConfig:
    measured = (
        frozenset({"water_level"})
        if station_kind == StationKind.LAKE
        else frozenset({"discharge", "water_level"})
    )
    return StationConfig(
        id=station_id,
        code=code,
        name=f"Station {code}",
        location=GeoCoord(lon=7.45, lat=46.95),
        station_kind=station_kind,
        basin_id=None,
        timezone="Europe/Zurich",
        regulation_type=None,
        forecast_targets=None,
        measured_parameters=measured,
        station_status=StationStatus.OPERATIONAL,
        created_at=ensure_utc(datetime(2024, 1, 1, tzinfo=UTC)),
        updated_at=ensure_utc(datetime(2024, 1, 1, tzinfo=UTC)),
        network="bafu",
        ownership=StationOwnership.OWN,
        wigos_id=None,
        tenant_id=DEFAULT_TENANT_ID,
    )


def _triple(subject: str, predicate: str, obj: str) -> dict[str, object]:
    return {
        "subject": {"type": "uri", "value": subject},
        "predicate": {"type": "uri", "value": predicate},
        "object": {"type": "literal", "value": obj},
    }


def _river_bindings(
    code: str,
    timestamp: str,
    discharge: str | None = None,
    water_level: str | None = None,
    water_temperature: str | None = None,
) -> list[dict[str, object]]:
    subject = f"{_RIVER_BASE}/{code}"
    bindings = [_triple(subject, f"{_DIM}/measurementTime", timestamp)]
    if discharge is not None:
        bindings.append(_triple(subject, f"{_DIM}/discharge", discharge))
    if water_level is not None:
        bindings.append(_triple(subject, f"{_DIM}/waterLevel", water_level))
    if water_temperature is not None:
        bindings.append(_triple(subject, f"{_DIM}/waterTemperature", water_temperature))
    return bindings


def _lake_bindings(
    code: str, timestamp: str, water_level: str
) -> list[dict[str, object]]:
    subject = f"{_LAKE_BASE}/{code}"
    return [
        _triple(subject, f"{_DIM}/measurementTime", timestamp),
        _triple(subject, f"{_DIM}/waterLevel", water_level),
    ]


def _sparql_response(bindings: list[dict[str, object]]) -> httpx.Response:
    body = json.dumps({"results": {"bindings": bindings}})
    return httpx.Response(
        200,
        content=body.encode(),
        headers={"content-type": "application/sparql-results+json"},
    )


def _make_client(response: httpx.Response) -> tuple[httpx.Client, list[httpx.Request]]:
    """Plan 186 T2: ingest now sends ONE whole-graph request regardless of
    the requested station count, so the mock transport no longer routes by
    per-station code in the query text — it just returns one fixed
    response and records every request it saw."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return response

    return httpx.Client(transport=httpx.MockTransport(handler)), captured


def _coherent_limiter(*, max_retries: int = 2) -> TokenBucketLindasLimiter:
    """Limiter whose injected sleeper ADVANCES its injected clock.

    A no-op sleeper paired with the real clock is an incoherent time source:
    the limiter's wait budget drains while the bucket never refills, so a
    post-429 drain (or any starved bucket) can never be waited out.
    Production never sees this — `time.sleep` and `datetime.now` always agree
    — only the DI seam can. Here the sleep costs no real time but time still
    MOVES, which is what the limiter's arithmetic assumes.
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


class TestHydroScraperAdapter:
    def test_happy_path_multiple_stations(self) -> None:
        station_1 = _make_station(_STATION_1_ID, "2044")
        station_2 = _make_station(_STATION_2_ID, "2160")

        bindings = [
            *_river_bindings(
                "2044",
                "2024-06-15T10:00:00+00:00",
                discharge="100.5",
                water_level="2.1",
            ),
            *_river_bindings(
                "2160", "2024-06-15T11:00:00+00:00", discharge="55.0", water_level="1.5"
            ),
        ]
        client, requests_seen = _make_client(_sparql_response(bindings))
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)
        since: dict[StationId, UtcDatetime] = {
            _STATION_1_ID: _SINCE,
            _STATION_2_ID: _SINCE,
        }

        obs = adapter.fetch_observations([station_1, station_2], since)

        assert len(requests_seen) == 1

        by_station: dict[StationId, dict[str, float]] = {}
        for o in obs:
            by_station.setdefault(o.station_id, {})[o.parameter] = o.value

        assert by_station[_STATION_1_ID]["discharge"] == pytest.approx(100.5)
        assert by_station[_STATION_1_ID]["water_level"] == pytest.approx(2.1)
        assert by_station[_STATION_2_ID]["discharge"] == pytest.approx(55.0)
        assert by_station[_STATION_2_ID]["water_level"] == pytest.approx(1.5)

        for o in obs:
            assert o.source == ObservationSource.MEASURED

    def test_whole_batch_failure_others_fail_too(self) -> None:
        # Plan 186 D4: a transport/HTTP fault is now WHOLE-BATCH — a real
        # change from the old per-station system, where only the failing
        # station's request was affected.
        import structlog.testing

        station_1 = _make_station(_STATION_1_ID, "2044")
        station_2 = _make_station(_STATION_2_ID, "2160")

        client, _requests_seen = _make_client(httpx.Response(500))
        # Plan 175 T3: a no-op sleeper — the 500 now retries through the
        # shared limiter, and this test must not perform real multi-second
        # sleeps waiting for the retry cap.
        adapter = HydroScraperAdapter(
            endpoint=_ENDPOINT,
            http_client=client,
            limiter=_coherent_limiter(),
        )
        since: dict[StationId, UtcDatetime] = {
            _STATION_1_ID: _SINCE,
            _STATION_2_ID: _SINCE,
        }

        with structlog.testing.capture_logs() as captured:
            obs = adapter.fetch_observations([station_1, station_2], since)

        assert obs == []
        failed_events = [
            e for e in captured if e.get("event") == "observation.fetch_failed"
        ]
        assert len(failed_events) == 2

    def test_empty_bindings_returns_empty_list(self) -> None:
        station = _make_station(_STATION_1_ID, "2044")
        client, _ = _make_client(_sparql_response([]))
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)

        obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert obs == []

    def test_malformed_timestamp_skipped(self) -> None:
        station = _make_station(_STATION_1_ID, "2044")
        bindings = _river_bindings("2044", "not-a-timestamp", discharge="42.0")
        client, _ = _make_client(_sparql_response(bindings))
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)

        obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert obs == []

    def test_fetch_returns_expected_records_from_fixture_response(self) -> None:
        fixture_path = _FIXTURES_DIR / "lindas_sample_response.json"
        fixture_body = fixture_path.read_bytes()
        station = _make_station(_STATION_1_ID, "2044")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=fixture_body,
                headers={"content-type": "application/sparql-results+json"},
            )

        adapter = HydroScraperAdapter(
            endpoint=_ENDPOINT,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert len(obs) == 3
        params = {o.parameter: o.value for o in obs}
        assert params["discharge"] == pytest.approx(45.3)
        assert params["water_level"] == pytest.approx(1.82)
        assert params["water_temperature"] == pytest.approx(12.7)

        for o in obs:
            assert o.station_id == _STATION_1_ID
            assert o.source == ObservationSource.MEASURED
            assert o.timestamp == ensure_utc(
                datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
            )

    def test_fetch_returns_expected_lake_records_from_fixture_response(self) -> None:
        fixture_path = _FIXTURES_DIR / "lindas_lake_sample_response.json"
        fixture_body = fixture_path.read_bytes()
        station = _make_station(_STATION_1_ID, "2500", station_kind=StationKind.LAKE)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=fixture_body,
                headers={"content-type": "application/sparql-results+json"},
            )

        adapter = HydroScraperAdapter(
            endpoint=_ENDPOINT,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert len(obs) == 1
        assert obs[0].parameter == "water_level"
        assert obs[0].value == pytest.approx(394.8)
        assert obs[0].station_id == _STATION_1_ID
        assert obs[0].source == ObservationSource.MEASURED
        assert obs[0].timestamp == ensure_utc(
            datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
        )

    def test_fetch_logs_warning_and_returns_partial_on_http_error(self) -> None:
        import structlog.testing

        station_1 = _make_station(_STATION_1_ID, "2044")
        station_2 = _make_station(_STATION_2_ID, "2160")

        # Plan 186 D4: a 503 is now whole-batch, so BOTH stations fail (not
        # "station 1 fails, station 2 succeeds" as in the old per-station
        # system) — this test now locks that both outcomes carry the cause.
        client, _ = _make_client(httpx.Response(503))
        # Plan 175 T3: no-op sleeper — see test_whole_batch_failure_others_
        # fail_too above for why.
        adapter = HydroScraperAdapter(
            endpoint=_ENDPOINT,
            http_client=client,
            limiter=_coherent_limiter(),
        )

        with structlog.testing.capture_logs() as captured:
            obs = adapter.fetch_observations(
                [station_1, station_2],
                {_STATION_1_ID: _SINCE, _STATION_2_ID: _SINCE},
            )

        warning_events = [
            e for e in captured if e.get("event") == "observation.fetch_failed"
        ]
        assert len(warning_events) == 2
        assert all(e["log_level"] == "warning" for e in warning_events)
        assert obs == []

    def test_fetch_handles_empty_bindings(self) -> None:
        station = _make_station(_STATION_1_ID, "2044")
        client, _ = _make_client(_sparql_response([]))
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)

        obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert obs == []

    def test_fetch_makes_one_whole_graph_request_regardless_of_station_kind(
        self,
    ) -> None:
        # Plan 186 T2: the request no longer encodes ANY station's code or
        # kind — the same fixed whole-graph query is sent whether the batch
        # is river, lake, or mixed. Query-shape assertions ("river/
        # observation/2500 not in query") no longer apply; what matters is
        # that exactly one request is made and it selects ?subject.
        station = _make_station(_STATION_1_ID, "2500", station_kind=StationKind.LAKE)
        bindings = _lake_bindings("2500", "2024-06-15T10:00:00+00:00", "394.8")
        client, requests_seen = _make_client(_sparql_response(bindings))

        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)
        obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert len(requests_seen) == 1
        query = parse_qs(requests_seen[0].content.decode())["query"][0]
        assert "SELECT ?subject ?predicate ?object" in query
        assert "BIND" not in query
        assert len(obs) == 1
        assert obs[0].value == pytest.approx(394.8)

    def test_fetch_does_not_embed_since_in_sparql_query(self) -> None:
        station = _make_station(_STATION_1_ID, "2044")
        client, requests_seen = _make_client(_sparql_response([]))

        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)
        adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert len(requests_seen) == 1
        query = parse_qs(requests_seen[0].content.decode())["query"][0]
        assert "xsd:dateTime" not in query
        assert "FILTER (?measurementTime" not in query
        assert "2024-01-01" not in query

    def test_fetch_no_longer_pre_request_rejects_invalid_station_code(self) -> None:
        # Plan 186 Q2: ingest no longer interpolates site_code into SPARQL,
        # so there is no injection surface and no pre-request guard in this
        # path any more. An "invalid" code is simply a lookup miss against
        # the whole-graph response -> NO_DATA, not a pre-request
        # MALFORMED_RESPONSE. The guard survives only in
        # `verify_gauge_reachable` (see the unit-test suite).
        import structlog.testing

        station = _make_station(_STATION_1_ID, "'; DROP TABLE")
        client, requests_seen = _make_client(_sparql_response([]))
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)

        with structlog.testing.capture_logs() as captured:
            obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert obs == []
        # A request WAS made this time (D4: only a pre-request guard could
        # avoid it, and Q2 deliberately removes that guard from ingest).
        assert len(requests_seen) == 1
        failed = [e for e in captured if e.get("event") == "observation.fetch_failed"]
        assert len(failed) == 1
        assert failed[0]["log_level"] == "warning"
        assert failed[0]["failure_cause"] == "no_data"

    def test_mixed_river_and_lake_stations(self) -> None:
        river_station = _make_station(
            _STATION_1_ID, "2044", station_kind=StationKind.RIVER
        )
        lake_station = _make_station(
            _STATION_2_ID, "2500", station_kind=StationKind.LAKE
        )

        bindings = [
            *_river_bindings(
                "2044",
                "2024-06-15T10:00:00+00:00",
                discharge="100.5",
                water_level="2.1",
            ),
            *_lake_bindings("2500", "2024-06-15T10:00:00+00:00", "394.8"),
        ]
        client, _ = _make_client(_sparql_response(bindings))
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)
        since: dict[StationId, UtcDatetime] = {
            _STATION_1_ID: _SINCE,
            _STATION_2_ID: _SINCE,
        }

        obs = adapter.fetch_observations([river_station, lake_station], since)

        river_obs = {o.parameter: o.value for o in obs if o.station_id == _STATION_1_ID}
        lake_obs = {o.parameter: o.value for o in obs if o.station_id == _STATION_2_ID}

        assert river_obs["discharge"] == pytest.approx(100.5)
        assert river_obs["water_level"] == pytest.approx(2.1)
        assert lake_obs == {"water_level": pytest.approx(394.8)}

    def test_weather_station_skipped(self) -> None:
        import structlog.testing

        station = _make_station(
            _STATION_1_ID, "WEATHER01", station_kind=StationKind.WEATHER
        )
        request_made = False

        def _fail_handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_made
            request_made = True
            return httpx.Response(200)

        client = httpx.Client(transport=httpx.MockTransport(_fail_handler))
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)

        with structlog.testing.capture_logs() as captured:
            obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert obs == []
        assert not request_made
        assert any(
            e.get("event") == "observation.skip_weather_station" for e in captured
        )
