from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import structlog.testing

from sapphire_flow.adapters.bafu_forecast import USER_AGENT, BafuForecastAdapter
from sapphire_flow.exceptions import AdapterError
from sapphire_flow.flows.collect_bafu_forecasts import _variants_for_station
from sapphire_flow.types.bafu_forecast import BafuGaugeDataStatus, BafuWaterBodyIcon
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
        assert station.icon == BafuWaterBodyIcon(
            kind="river", data_status=BafuGaugeDataStatus.PRESENT
        )
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
        # Plan 160 D3: this is now per-feature containment, not a whole-batch
        # abort — the poisoned station is skipped and the other 53 are still
        # returned (matching the icon-drift containment path exactly).
        payload = json.loads(json.dumps(_STATIONS_JSON))
        payload["features"][0]["properties"]["key"] = "../../../etc/cron.d/x"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        inventory = adapter.fetch_station_inventory()
        assert len(inventory.stations) == 53
        assert inventory.skipped_count == 1
        assert all(s.key != "../../../etc/cron.d/x" for s in inventory.stations)

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

    def test_raises_adapter_error_on_unparseable_produced_at(self) -> None:
        # T2 (D3): an unparseable meta.produced_at is structurally unusable --
        # every row in the batch needs it -- so it must still be a batch-wide
        # AdapterError, not silently skipped like a per-feature failure.
        payload = json.loads(json.dumps(_STATIONS_JSON))
        payload["meta"]["produced_at"] = "not-a-timestamp"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="not a parseable ISO8601 timestamp"):
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


class TestIconSchemaDriftResilience:
    """Plan 160: the live-outage class fix. Key acceptance criteria (T1/T2)."""

    def test_river_missing_icon_routes_like_river(self) -> None:
        # T1 (D1/D2/D8, probe-backed): "river_missing" describes the live
        # GAUGE, not forecast availability -- BAFU still publishes a full
        # forecast for it, so it must route exactly like plain "river", not
        # be skipped. Before this plan, "river_missing" is not a member of
        # the flat BafuIcon Literal, so this raises AdapterError before
        # routing is ever reached.
        payload = json.loads(json.dumps(_STATIONS_JSON))
        target = next(
            f for f in payload["features"] if f["properties"]["icon"] == "river"
        )
        target["properties"]["icon"] = "river_missing"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        inventory = adapter.fetch_station_inventory()
        assert inventory.skipped_count == 0
        station = next(
            s for s in inventory.stations if s.key == target["properties"]["key"]
        )
        # Parsed as MISSING data status, not silently coerced to PRESENT — a
        # parser that dropped the `_missing` suffix would still route this
        # station correctly (kind alone drives routing, D2) but would be
        # lying about the gauge's actual state.
        assert station.icon == BafuWaterBodyIcon(
            kind="river", data_status=BafuGaugeDataStatus.MISSING
        )
        assert _variants_for_station(station) == ("q_forecast",)

    def test_lake_missing_icon_routes_like_lake(self) -> None:
        # T1 (D1/D2/D6): "lake_missing" has never appeared in the live feed
        # (0/54 as of 2026-08-13) but is the forward-looking lock that stops
        # the NEXT outage -- the first lake station whose gauge goes down.
        # Same reasoning as river_missing: raises today, must route like
        # plain "lake" (both variants) once fixed.
        payload = json.loads(json.dumps(_STATIONS_JSON))
        target = next(
            f for f in payload["features"] if f["properties"]["icon"] == "lake"
        )
        target["properties"]["icon"] = "lake_missing"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        inventory = adapter.fetch_station_inventory()
        assert inventory.skipped_count == 0
        station = next(
            s for s in inventory.stations if s.key == target["properties"]["key"]
        )
        assert station.icon == BafuWaterBodyIcon(
            kind="lake", data_status=BafuGaugeDataStatus.MISSING
        )
        assert _variants_for_station(station) == ("q_forecast", "p_forecast")

    def test_river_lake_and_legacy_missing_routing_unchanged(self) -> None:
        # D2/D6: the three previously-recognised icon values keep their
        # exact routing -- this plan only ADDS the {kind}_missing case.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_STATIONS_JSON)

        adapter = _make_adapter(httpx.MockTransport(handler))
        inventory = adapter.fetch_station_inventory()

        river = next(
            s
            for s in inventory.stations
            if isinstance(s.icon, BafuWaterBodyIcon) and s.icon.kind == "river"
        )
        assert _variants_for_station(river) == ("q_forecast",)

        lake = next(
            s
            for s in inventory.stations
            if isinstance(s.icon, BafuWaterBodyIcon) and s.icon.kind == "lake"
        )
        assert _variants_for_station(lake) == ("q_forecast", "p_forecast")

    def test_single_invalid_icon_is_skipped_other_53_stations_returned(self) -> None:
        # T2 (D3, the class fix): the exact shape of the production outage --
        # ONE of 54 features has an icon BAFU has not documented. Today this
        # aborts the whole inventory (AdapterError); after the fix it must be
        # skipped and recorded, with the other 53 stations still returned.
        payload = json.loads(json.dumps(_STATIONS_JSON))
        assert len(payload["features"]) == 54
        original_keys = {f["properties"]["key"] for f in payload["features"]}
        poisoned_key = payload["features"][0]["properties"]["key"]
        payload["features"][0]["properties"]["icon"] = "reservoir"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        inventory = adapter.fetch_station_inventory()
        assert len(inventory.stations) == 53
        assert inventory.skipped_count == 1
        # Not just a count check: the SURVIVING 53 must be exactly the
        # original set minus the poisoned station -- an implementation that
        # dropped a different (valid) station while still returning 53
        # entries would pass a bare length assertion.
        returned_keys = {s.key for s in inventory.stations}
        assert returned_keys == original_keys - {poisoned_key}
        assert poisoned_key not in returned_keys

    def test_every_feature_invalid_still_raises(self) -> None:
        # T2 (D3): the "no station validates" edge of the partial/total
        # distinction. This is unchanged behaviour (already raises today),
        # locked so the containment fix does not accidentally widen into
        # "always return whatever validated, even if that's nothing".
        payload = json.loads(json.dumps(_STATIONS_JSON))
        for feature in payload["features"]:
            feature["properties"]["icon"] = "reservoir"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="failed validation"):
            adapter.fetch_station_inventory()

    def test_empty_feature_collection_raises_not_silently_empty(self) -> None:
        # A structurally valid envelope with ZERO features is operationally a
        # total loss -- BAFU serving a broken/empty feed -- indistinguishable
        # from "every feature failed validation" in effect. It must raise,
        # never be reported as a healthy zero-station run (which would mask
        # a total collector outage behind an OK heartbeat).
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "features": [],
                    "meta": {"produced_at": "2026-07-10T09:43:08.786000+00:00"},
                },
            )

        adapter = _make_adapter(httpx.MockTransport(handler))
        with pytest.raises(AdapterError, match="0 feature"):
            adapter.fetch_station_inventory()

    def test_skipped_station_emits_warning_with_key_and_offending_value(self) -> None:
        # D5: drift is telemetry, not silence -- the operator must be able to
        # see WHICH station and WHAT value caused the skip from the log alone.
        payload = json.loads(json.dumps(_STATIONS_JSON))
        target = payload["features"][0]
        target["properties"]["icon"] = "reservoir"
        target_key = target["properties"]["key"]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        adapter = _make_adapter(httpx.MockTransport(handler))
        with structlog.testing.capture_logs() as captured:
            adapter.fetch_station_inventory()

        skip_events = [
            e for e in captured if e.get("event") == "bafu_forecast.station_skipped"
        ]
        assert len(skip_events) == 1
        assert skip_events[0]["log_level"] == "warning"
        assert skip_events[0].get("station_key") == target_key
        assert "reservoir" in skip_events[0].get("error", "")


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
