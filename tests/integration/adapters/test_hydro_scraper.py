from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest

from sapphire_flow.adapters.hydro_scraper import HydroScraperAdapter
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

_ENDPOINT = "https://ld.admin.ch/query"
_FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"

_SINCE = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))

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
    )


def _sparql_response(bindings: list[dict]) -> httpx.Response:
    body = json.dumps({"results": {"bindings": bindings}})
    return httpx.Response(
        200,
        content=body.encode(),
        headers={"content-type": "application/sparql-results+json"},
    )


def _make_bindings(
    timestamp: str,
    discharge: str | None = None,
    water_level: str | None = None,
    water_temperature: str | None = None,
) -> list[dict]:
    dim = "https://environment.ld.admin.ch/foen/hydro/dimension"
    bindings = [
        {
            "predicate": {"value": f"{dim}/measurementTime"},
            "object": {"value": timestamp},
        },
    ]
    if discharge is not None:
        bindings.append(
            {"predicate": {"value": f"{dim}/discharge"}, "object": {"value": discharge}}
        )
    if water_level is not None:
        bindings.append(
            {
                "predicate": {"value": f"{dim}/waterLevel"},
                "object": {"value": water_level},
            }
        )
    if water_temperature is not None:
        bindings.append(
            {
                "predicate": {"value": f"{dim}/waterTemperature"},
                "object": {"value": water_temperature},
            }
        )
    return bindings


def _make_client(
    responses_by_code: dict[str, httpx.Response],
    default: httpx.Response | None = None,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        params = parse_qs(body)
        query = params.get("query", [""])[0]
        for code, response in responses_by_code.items():
            if f"/river/observation/{code}" in query:
                return response
            if f"/lake/observation/{code}" in query:
                return response
        if default is not None:
            return default
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestHydroScraperAdapter:
    def test_happy_path_multiple_stations(self) -> None:
        station_1 = _make_station(_STATION_1_ID, "2044")
        station_2 = _make_station(_STATION_2_ID, "2160")

        client = _make_client(
            {
                "2044": _sparql_response(
                    _make_bindings(
                        "2024-06-15T10:00:00+00:00",
                        discharge="100.5",
                        water_level="2.1",
                    )
                ),
                "2160": _sparql_response(
                    _make_bindings(
                        "2024-06-15T11:00:00+00:00", discharge="55.0", water_level="1.5"
                    )
                ),
            }
        )
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)
        since: dict[StationId, UtcDatetime] = {
            _STATION_1_ID: _SINCE,
            _STATION_2_ID: _SINCE,
        }

        obs = adapter.fetch_observations([station_1, station_2], since)

        by_station: dict[StationId, dict[str, float]] = {}
        for o in obs:
            by_station.setdefault(o.station_id, {})[o.parameter] = o.value

        assert by_station[_STATION_1_ID]["discharge"] == pytest.approx(100.5)
        assert by_station[_STATION_1_ID]["water_level"] == pytest.approx(2.1)
        assert by_station[_STATION_2_ID]["discharge"] == pytest.approx(55.0)
        assert by_station[_STATION_2_ID]["water_level"] == pytest.approx(1.5)

        for o in obs:
            assert o.source == ObservationSource.MEASURED

    def test_single_station_failure_others_succeed(self) -> None:
        import structlog.testing

        station_1 = _make_station(_STATION_1_ID, "2044")
        station_2 = _make_station(_STATION_2_ID, "2160")

        client = _make_client(
            {
                "2044": httpx.Response(500),
                "2160": _sparql_response(
                    _make_bindings("2024-06-15T11:00:00+00:00", discharge="55.0")
                ),
            }
        )
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)
        since: dict[StationId, UtcDatetime] = {
            _STATION_1_ID: _SINCE,
            _STATION_2_ID: _SINCE,
        }

        with structlog.testing.capture_logs() as captured:
            obs = adapter.fetch_observations([station_1, station_2], since)

        station_ids = {o.station_id for o in obs}
        assert _STATION_1_ID not in station_ids
        assert _STATION_2_ID in station_ids
        assert any(e.get("event") == "observation.fetch_failed" for e in captured)

    def test_empty_bindings_returns_empty_list(self) -> None:
        station = _make_station(_STATION_1_ID, "2044")
        client = _make_client({"2044": _sparql_response([])})
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)

        obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert obs == []

    def test_malformed_timestamp_skipped(self) -> None:
        station = _make_station(_STATION_1_ID, "2044")
        bindings = _make_bindings("not-a-timestamp", discharge="42.0")
        client = _make_client({"2044": _sparql_response(bindings)})
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
            assert o.timestamp == ensure_utc(datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC))

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
        assert obs[0].timestamp == ensure_utc(datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC))

    def test_fetch_logs_warning_and_returns_partial_on_http_error(self) -> None:
        import structlog.testing

        station_1 = _make_station(_STATION_1_ID, "2044")
        station_2 = _make_station(_STATION_2_ID, "2160")

        client = _make_client(
            {
                "2044": httpx.Response(503),
                "2160": _sparql_response(
                    _make_bindings("2024-06-15T10:00:00+00:00", discharge="77.0")
                ),
            }
        )
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)

        with structlog.testing.capture_logs() as captured:
            obs = adapter.fetch_observations(
                [station_1, station_2],
                {_STATION_1_ID: _SINCE, _STATION_2_ID: _SINCE},
            )

        warning_events = [
            e for e in captured if e.get("event") == "observation.fetch_failed"
        ]
        assert len(warning_events) == 1
        assert warning_events[0]["log_level"] == "warning"

        station_ids = {o.station_id for o in obs}
        assert _STATION_1_ID not in station_ids
        assert _STATION_2_ID in station_ids

    def test_fetch_handles_empty_bindings(self) -> None:
        station = _make_station(_STATION_1_ID, "2044")
        client = _make_client({"2044": _sparql_response([])})
        adapter = HydroScraperAdapter(endpoint=_ENDPOINT, http_client=client)

        obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert obs == []

    def test_fetch_lake_station_uses_lake_uri_path(self) -> None:
        station = _make_station(_STATION_1_ID, "2500", station_kind=StationKind.LAKE)
        captured: list[httpx.Request] = []
        empty_envelope = json.dumps({"results": {"bindings": []}}).encode()

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                content=empty_envelope,
                headers={"content-type": "application/sparql-results+json"},
            )

        adapter = HydroScraperAdapter(
            endpoint=_ENDPOINT,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert len(captured) == 1
        query = parse_qs(captured[0].content.decode())["query"][0]
        assert "lake/observation/2500" in query
        assert "river/observation/2500" not in query

    def test_fetch_does_not_embed_since_in_sparql_query(self) -> None:
        station = _make_station(_STATION_1_ID, "2044")
        captured: list[httpx.Request] = []
        empty_envelope = json.dumps({"results": {"bindings": []}}).encode()

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                content=empty_envelope,
                headers={"content-type": "application/sparql-results+json"},
            )

        adapter = HydroScraperAdapter(
            endpoint=_ENDPOINT,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert len(captured) == 1
        query = parse_qs(captured[0].content.decode())["query"][0]
        assert "xsd:dateTime" not in query
        assert "FILTER (?measurementTime" not in query
        assert "2024-01-01" not in query

    def test_fetch_rejects_invalid_station_code(self) -> None:
        import structlog.testing

        station = _make_station(_STATION_1_ID, "'; DROP TABLE")
        adapter = HydroScraperAdapter(
            endpoint=_ENDPOINT,
            http_client=httpx.Client(
                transport=httpx.MockTransport(lambda r: httpx.Response(200))
            ),
        )

        with structlog.testing.capture_logs() as captured:
            obs = adapter.fetch_observations([station], {_STATION_1_ID: _SINCE})

        assert obs == []
        failed = [e for e in captured if e.get("event") == "observation.fetch_failed"]
        assert len(failed) == 1
        assert failed[0]["log_level"] == "warning"
        assert "Invalid site_code" in failed[0]["error"]

    def test_mixed_river_and_lake_stations(self) -> None:
        river_station = _make_station(
            _STATION_1_ID, "2044", station_kind=StationKind.RIVER
        )
        lake_station = _make_station(
            _STATION_2_ID, "2500", station_kind=StationKind.LAKE
        )

        client = _make_client(
            {
                "2044": _sparql_response(
                    _make_bindings(
                        "2024-06-15T10:00:00+00:00",
                        discharge="100.5",
                        water_level="2.1",
                    )
                ),
                "2500": _sparql_response(
                    _make_bindings(
                        "2024-06-15T10:00:00+00:00",
                        water_level="394.8",
                    )
                ),
            }
        )
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

