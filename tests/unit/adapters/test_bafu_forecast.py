from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from sapphire_flow.adapters.bafu_forecast import USER_AGENT, BafuForecastAdapter
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import ensure_utc

_FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "reference"
_STATIONS_FIXTURE = _FIXTURE_DIR / "bafu_forecast_stations.geojson"
_Q_FORECAST_FIXTURE = _FIXTURE_DIR / "bafu_q_forecast_2135.json"

_STATIONS_JSON = json.loads(_STATIONS_FIXTURE.read_text())
_Q_FORECAST_JSON = json.loads(_Q_FORECAST_FIXTURE.read_text())


def _make_adapter(
    handler: httpx.MockTransport,
    *,
    sleeper: object = None,
    max_retries: int = 2,
) -> BafuForecastAdapter:
    client = httpx.Client(transport=handler)
    kwargs: dict[str, object] = {"max_retries": max_retries}
    if sleeper is not None:
        kwargs["sleeper"] = sleeper
    return BafuForecastAdapter(http_client=client, **kwargs)  # type: ignore[arg-type]


class _SleepSpy:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class TestFetchStationInventory:
    def test_parses_real_fixture(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_STATIONS_JSON)

        adapter = _make_adapter(httpx.MockTransport(handler))
        inventory = adapter.fetch_station_inventory()

        assert len(inventory.stations) == 54
        assert inventory.produced_at == ensure_utc(
            datetime(2026, 7, 10, 9, 43, 8, 786000, tzinfo=UTC)
        )

        station = next(s for s in inventory.stations if s.key == "2135")
        assert station.label == "Aare - Bern, Schönau"
        assert station.icon == "river"
        assert station.metric == "discharge_ms"
        assert station.unit == "m³/s"
        assert station.plot_path == "/web/hydro/hydro_sensor_pq_forecast/2135/plots"

    def test_inventory_accepts_missing_icon_station(self) -> None:
        # BAFU's own legend documents icon "missing" (station with no current
        # data). One such station must NOT fail whole-inventory validation and
        # take down the run — the type accepts it; the flow skips it.
        payload = json.loads(json.dumps(_STATIONS_JSON))
        payload["features"][0]["properties"]["icon"] = "missing"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        inventory = adapter.fetch_station_inventory()
        assert len(inventory.stations) == 54
        assert any(s.icon == "missing" for s in inventory.stations)

    def test_rejects_path_traversal_station_key(self) -> None:
        # A spoofed/hijacked feed must not smuggle a traversal key into an
        # archive path — the key is validated against the expected shape.
        payload = json.loads(json.dumps(_STATIONS_JSON))
        payload["features"][0]["properties"]["key"] = "../../../etc/cron.d/x"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="does not match the expected"):
            adapter.fetch_station_inventory()

    def test_user_agent_header_sent(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("user-agent", ""))
            return httpx.Response(200, json=_STATIONS_JSON)

        adapter = _make_adapter(httpx.MockTransport(handler))
        adapter.fetch_station_inventory()

        assert seen == [USER_AGENT]
        assert "SAPPHIRE-Flow" in USER_AGENT
        assert "marti@hydrosolutions.ch" in USER_AGENT

    def test_raises_adapter_error_on_malformed_json(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not-json")

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="not valid JSON"):
            adapter.fetch_station_inventory()

    def test_raises_adapter_error_on_schema_mismatch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"features": [], "meta": {}})

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="schema validation"):
            adapter.fetch_station_inventory()

    def test_total_failure_raises_adapter_error_on_connect_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        spy = _SleepSpy()
        adapter = _make_adapter(httpx.MockTransport(handler), sleeper=spy)
        with pytest.raises(AdapterError, match="failed after"):
            adapter.fetch_station_inventory()
        # Retried up to the cap without a real sleep.
        assert len(spy.calls) == 2


class TestFetchVariantForecast:
    _PRODUCED_AT = ensure_utc(datetime(2026, 7, 10, 9, 43, 8, tzinfo=UTC))

    def test_parses_real_fixture(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "q_forecast/2135_q_forecast_en.json" in str(request.url)
            return httpx.Response(200, json=_Q_FORECAST_JSON)

        adapter = _make_adapter(httpx.MockTransport(handler))
        result = adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)

        assert result is not None
        assert result.station_key == "2135"
        assert result.variant == "q_forecast"
        assert result.metric == "discharge_ms"
        # "Forecast as of 10.07.26 07:00" @ +02:00 -> 05:00 UTC.
        assert result.issued_at == ensure_utc(datetime(2026, 7, 10, 5, 0, tzinfo=UTC))
        assert result.raw_payload == _Q_FORECAST_JSON

        expected_row_count = sum(
            len(trace["x"]) for trace in _Q_FORECAST_JSON["plot"]["data"]
        )
        assert len(result.rows) == expected_row_count

        median_row = next(
            r
            for r in result.rows
            if r.trace_name == "Median"
            and r.valid_time == ensure_utc(datetime(2026, 7, 10, 5, 0, tzinfo=UTC))
        )
        assert median_row.value == pytest.approx(122.1)
        assert median_row.station_key == "2135"
        assert median_row.metric == "discharge_ms"
        assert median_row.unit == "m³/s"
        assert median_row.produced_at == self._PRODUCED_AT

    def test_percentile_band_trace_falls_back_to_default_unit(self) -> None:
        # The "25.-75. Percentile" fill trace carries meta.unit == "" in the
        # real payload; the adapter must fall back to the variant default.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_Q_FORECAST_JSON)

        adapter = _make_adapter(httpx.MockTransport(handler))
        result = adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)
        assert result is not None

        percentile_rows = [
            r for r in result.rows if r.trace_name == "25.-75. Percentile"
        ]
        assert percentile_rows
        assert all(r.unit == "m³/s" for r in percentile_rows)

    def test_returns_none_on_404(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        adapter = _make_adapter(httpx.MockTransport(handler))
        result = adapter.fetch_variant_forecast("9999", "p_forecast", self._PRODUCED_AT)
        assert result is None

    def test_user_agent_header_sent(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("user-agent", ""))
            return httpx.Response(200, json=_Q_FORECAST_JSON)

        adapter = _make_adapter(httpx.MockTransport(handler))
        adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)

        assert seen == [USER_AGENT]

    def test_raises_adapter_error_when_forecast_annotation_missing(self) -> None:
        payload = json.loads(json.dumps(_Q_FORECAST_JSON))
        payload["plot"]["layout"]["annotations"] = [
            a
            for a in payload["plot"]["layout"]["annotations"]
            if not str(a.get("text", "")).startswith("Forecast as of")
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="no 'Forecast as of' annotation"):
            adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)

    def test_malformed_trace_timestamp_raises_adapter_error_not_bare_valueerror(
        self,
    ) -> None:
        # A malformed trace x-timestamp must surface as AdapterError so the flow's
        # per-station AdapterError handler isolates it — a bare ValueError would
        # escape isolation and abort the whole collection run.
        payload = json.loads(json.dumps(_Q_FORECAST_JSON))
        payload["plot"]["data"][0]["x"][0] = "not-a-timestamp"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="unparseable trace timestamp"):
            adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)

    def test_mismatched_trace_xy_lengths_raise_adapter_error(self) -> None:
        # A truncated trace (len(x) != len(y)) must surface as AdapterError so
        # the flow isolates it per-station, not a bare ValueError from zip(strict).
        payload = json.loads(json.dumps(_Q_FORECAST_JSON))
        payload["plot"]["data"][0]["y"] = payload["plot"]["data"][0]["y"][:-1]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="mismatched x/y lengths"):
            adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)

    def test_percentile_band_points_are_indexed_for_reconstruction(self) -> None:
        # The band trace repeats valid_time (upper then lower edge); point_index
        # preserves the polygon order so p25/p75 stay reconstructable.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_Q_FORECAST_JSON)

        adapter = _make_adapter(httpx.MockTransport(handler))
        result = adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)
        assert result is not None
        band = [r for r in result.rows if "Percentile" in r.trace_name]
        assert band, "expected a percentile-band trace"
        # point_index is a contiguous 0..n-1 sequence within the trace.
        assert [r.point_index for r in band] == list(range(len(band)))

    def test_matches_annotation_by_text_prefix_not_index(self) -> None:
        # Reorder annotations so the forecast annotation is first, not last —
        # the adapter must not rely on a fixed array index.
        payload = json.loads(json.dumps(_Q_FORECAST_JSON))
        payload["plot"]["layout"]["annotations"] = list(
            reversed(payload["plot"]["layout"]["annotations"])
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        result = adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)
        assert result is not None
        assert result.issued_at == ensure_utc(datetime(2026, 7, 10, 5, 0, tzinfo=UTC))

    def test_raises_adapter_error_on_malformed_payload(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="schema validation"):
            adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)

    def test_retries_on_5xx_then_succeeds_without_real_sleep(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503, text="try again")
            return httpx.Response(200, json=_Q_FORECAST_JSON)

        spy = _SleepSpy()
        adapter = _make_adapter(
            httpx.MockTransport(handler), sleeper=spy, max_retries=3
        )
        result = adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)

        assert result is not None
        assert calls["n"] == 3
        assert len(spy.calls) == 2  # slept before each retry, not after success

    def test_raises_adapter_error_after_retry_cap_exceeded(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="always fails")

        spy = _SleepSpy()
        adapter = _make_adapter(
            httpx.MockTransport(handler), sleeper=spy, max_retries=2
        )
        with pytest.raises(AdapterError, match="failed with status 503"):
            adapter.fetch_variant_forecast("2135", "q_forecast", self._PRODUCED_AT)
        assert len(spy.calls) == 2
