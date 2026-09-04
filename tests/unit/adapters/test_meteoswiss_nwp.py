from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest
import structlog
import xarray as xr

from sapphire_flow.adapters.meteoswiss_nwp import (
    MeteoSwissNwpAdapter,
    _assert_cycle_complete,
    _combine_cfgrib_datasets,
    _compute_wind_speed,
    _convert_units,
    _deaccumulate_precipitation,
    _expected_valid_times,
    convert_raw_dataset,
)
from sapphire_flow.exceptions import AdapterError, NoCycleAvailableError
from sapphire_flow.protocols.adapters import WeatherForecastSource
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc


class TestDeaccumulatePrecipitation:
    def test_preserves_time_length(self) -> None:
        tp = np.array([0, 1, 3, 6, 10], dtype=np.float32).reshape(1, 5, 1, 1)
        ds = xr.Dataset(
            {
                "tp": xr.DataArray(
                    tp, dims=["member", "valid_time", "latitude", "longitude"]
                )
            }
        )
        result = _deaccumulate_precipitation(ds)
        assert result["precipitation"].shape[1] == 5

    def test_deaccumulated_values(self) -> None:
        tp = np.array([0, 1, 3, 6, 10], dtype=np.float32).reshape(1, 5, 1, 1)
        ds = xr.Dataset(
            {
                "tp": xr.DataArray(
                    tp, dims=["member", "valid_time", "latitude", "longitude"]
                )
            }
        )
        result = _deaccumulate_precipitation(ds)
        expected = np.array([0, 1, 2, 3, 4], dtype=np.float32).reshape(1, 5, 1, 1)
        np.testing.assert_array_almost_equal(result["precipitation"].values, expected)

    def test_drops_tp_variable(self) -> None:
        tp = np.array([0, 1, 3], dtype=np.float32).reshape(1, 3, 1, 1)
        ds = xr.Dataset(
            {
                "tp": xr.DataArray(
                    tp, dims=["member", "valid_time", "latitude", "longitude"]
                )
            }
        )
        result = _deaccumulate_precipitation(ds)
        assert "tp" not in result
        assert "precipitation" in result


class TestConvertUnits:
    def test_temperature_kelvin_to_celsius(self) -> None:
        # cfgrib exposes ICON-CH2-EPS 2-m temperature as data var `t2m`
        # (CF convention), not `t_2m` — the latter is only the MeteoSwiss
        # STAC item-id token. See `_convert_units` in the adapter.
        ds = xr.Dataset(
            {
                "t2m": xr.DataArray(
                    np.full((3, 5, 2, 2), 293.15, dtype=np.float32),
                    dims=["member", "valid_time", "latitude", "longitude"],
                )
            }
        )
        result = _convert_units(ds)
        np.testing.assert_allclose(result["temperature"].values, 20.0, atol=0.01)
        assert "t2m" not in result

    def test_snow_depth_meters_to_cm(self) -> None:
        ds = xr.Dataset(
            {
                "sd": xr.DataArray(
                    np.full((1, 2, 2, 2), 0.5, dtype=np.float32),
                    dims=["member", "valid_time", "latitude", "longitude"],
                )
            }
        )
        result = _convert_units(ds)
        np.testing.assert_allclose(result["snow_depth"].values, 50.0, atol=0.01)
        assert "sd" not in result

    def test_humidity_renamed(self) -> None:
        ds = xr.Dataset(
            {
                "relhum_2m": xr.DataArray(
                    np.full((1, 2, 2, 2), 85.0, dtype=np.float32),
                    dims=["member", "valid_time", "latitude", "longitude"],
                )
            }
        )
        result = _convert_units(ds)
        np.testing.assert_allclose(result["humidity"].values, 85.0)
        assert "relhum_2m" not in result


class TestComputeWindSpeed:
    def test_magnitude_from_components(self) -> None:
        ds = xr.Dataset(
            {
                "u_10m": xr.DataArray(
                    np.full((1, 2, 2, 2), 3.0, dtype=np.float32),
                    dims=["member", "valid_time", "latitude", "longitude"],
                ),
                "v_10m": xr.DataArray(
                    np.full((1, 2, 2, 2), 4.0, dtype=np.float32),
                    dims=["member", "valid_time", "latitude", "longitude"],
                ),
            }
        )
        result = _compute_wind_speed(ds)
        np.testing.assert_allclose(result["wind_speed"].values, 5.0, atol=0.01)
        assert "u_10m" not in result
        assert "v_10m" not in result

    def test_zero_components(self) -> None:
        ds = xr.Dataset(
            {
                "u_10m": xr.DataArray(
                    np.zeros((1, 2, 2, 2), dtype=np.float32),
                    dims=["member", "valid_time", "latitude", "longitude"],
                ),
                "v_10m": xr.DataArray(
                    np.zeros((1, 2, 2, 2), dtype=np.float32),
                    dims=["member", "valid_time", "latitude", "longitude"],
                ),
            }
        )
        result = _compute_wind_speed(ds)
        np.testing.assert_allclose(result["wind_speed"].values, 0.0)


class TestConvertRawDataset:
    def test_renames_number_to_member(self) -> None:
        ds = xr.Dataset(
            {
                "t2m": xr.DataArray(
                    np.full((3, 2, 2, 2), 300.0, dtype=np.float32),
                    dims=["number", "valid_time", "latitude", "longitude"],
                )
            }
        )
        result = convert_raw_dataset(ds)
        assert "member" in result.dims
        assert "number" not in result.dims

    def test_full_pipeline(self) -> None:
        n_members = 2
        n_times = 4
        ds = xr.Dataset(
            {
                "tp": xr.DataArray(
                    np.array([0, 1, 3, 6], dtype=np.float32)
                    .reshape(1, n_times, 1, 1)
                    .repeat(n_members, axis=0),
                    dims=["number", "valid_time", "latitude", "longitude"],
                ),
                "t2m": xr.DataArray(
                    np.full((n_members, n_times, 1, 1), 273.15, dtype=np.float32),
                    dims=["number", "valid_time", "latitude", "longitude"],
                ),
                "u_10m": xr.DataArray(
                    np.full((n_members, n_times, 1, 1), 3.0, dtype=np.float32),
                    dims=["number", "valid_time", "latitude", "longitude"],
                ),
                "v_10m": xr.DataArray(
                    np.full((n_members, n_times, 1, 1), 4.0, dtype=np.float32),
                    dims=["number", "valid_time", "latitude", "longitude"],
                ),
                "relhum_2m": xr.DataArray(
                    np.full((n_members, n_times, 1, 1), 80.0, dtype=np.float32),
                    dims=["number", "valid_time", "latitude", "longitude"],
                ),
                "sd": xr.DataArray(
                    np.full((n_members, n_times, 1, 1), 0.1, dtype=np.float32),
                    dims=["number", "valid_time", "latitude", "longitude"],
                ),
            }
        )
        result = convert_raw_dataset(ds)

        assert "member" in result.dims
        assert set(result.data_vars) == {
            "precipitation",
            "temperature",
            "wind_speed",
            "humidity",
            "snow_depth",
        }
        np.testing.assert_allclose(result["temperature"].values, 0.0, atol=0.01)
        np.testing.assert_allclose(result["wind_speed"].values, 5.0, atol=0.01)
        np.testing.assert_allclose(result["humidity"].values, 80.0)
        np.testing.assert_allclose(result["snow_depth"].values, 10.0, atol=0.01)


class TestProtocolConformance:
    def test_has_fetch_forecasts_method(self) -> None:
        assert hasattr(MeteoSwissNwpAdapter, "fetch_forecasts")

    def test_nwp_source_attribute(self) -> None:
        assert MeteoSwissNwpAdapter.NWP_SOURCE == "icon_ch2_eps"

    def test_runtime_checkable(self) -> None:
        assert issubclass(WeatherForecastSource, WeatherForecastSource)


_STAC_BASE = "https://data.geo.admin.ch/api/stac/v1"
_STAC_COLLECTION = "ch.meteoschweiz.ogd-forecasting-icon-ch2"


def _make_adapter(
    transport: httpx.MockTransport, tmp_path: Path
) -> MeteoSwissNwpAdapter:
    # disk_guard_enabled=False: pre-existing tests are not subjected to the
    # D1 stale sweep or D2 pre-fetch disk check (Plan 105).
    client = httpx.Client(transport=transport, base_url="https://dummy")
    return MeteoSwissNwpAdapter(
        stac_base_url=_STAC_BASE,
        stac_collection=_STAC_COLLECTION,
        scratch_path=tmp_path,
        http_client=client,
        disk_guard_enabled=False,
    )


def _cycle_features(cycle: UtcDatetime) -> list[dict[str, object]]:
    prefix = cycle.strftime("%m%d%Y-%H%M-0-")
    ref_dt = cycle.strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        {
            "id": f"{prefix}tot_prec-ctrl-abc123",
            "properties": {"forecast:reference_datetime": ref_dt},
        },
        {
            "id": f"{prefix}t_2m-ctrl-def456",
            "properties": {"forecast:reference_datetime": ref_dt},
        },
    ]


class TestResolveCycleTime:
    def test_snaps_to_nearest_past_cycle(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            if "datetime=2026-04-19T12:00:00Z" in str(request.url):
                return httpx.Response(200, json={"features": _cycle_features(cycle)})
            return httpx.Response(200, json={"features": []})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        now = ensure_utc(datetime(2026, 4, 19, 14, 37, 12, tzinfo=UTC))
        assert adapter.resolve_cycle_time(now) == cycle

    def test_falls_back_on_empty_features(self, tmp_path: Path) -> None:
        # Plan 067 D7: under the corrected 6 h cadence, _snap_to_cycle(18:30)
        # snaps to 18:00, and the fallback steps back by 6 h (18:00 → 12:00).
        prior = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "datetime=2026-04-19T18:00:00Z" in q:
                return httpx.Response(200, json={"features": []})
            if "datetime=2026-04-19T12:00:00Z" in q:
                return httpx.Response(200, json={"features": _cycle_features(prior)})
            return httpx.Response(200, json={"features": []})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        now = ensure_utc(datetime(2026, 4, 19, 18, 30, tzinfo=UTC))
        assert adapter.resolve_cycle_time(now) == prior

    def test_raises_after_fallback_steps_exhausted(self, tmp_path: Path) -> None:
        # Plan 067 T3.b: default max_fallback_steps=2 under the corrected 6 h
        # cadence covers 12 h, matching the default
        # nwp_max_fallback_age_hours=12.0 policy.
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"features": []})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        now = ensure_utc(datetime(2026, 4, 19, 15, 30, tzinfo=UTC))
        with pytest.raises(NoCycleAvailableError, match="No cycle available"):
            adapter.resolve_cycle_time(now)

    def test_raises_on_tz_naive_input(self, tmp_path: Path) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"features": []})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        naive = datetime(2026, 4, 19, 12, 0)
        with pytest.raises(ValueError, match="tz-aware"):
            adapter.resolve_cycle_time(naive)  # type: ignore[arg-type]


class TestResolveCycleFallbackSignal:
    """epic-088 M4: the cycle-fallback outcome is SURFACED, not just logged.

    ``resolve_cycle`` returns a ``CycleResolution`` value object carrying both
    the resolved cycle and whether the adapter had to walk back >=1 step. This
    is the signal the forecast cycle threads into ``NwpCycleSource.FALLBACK``.
    """

    def test_no_fallback_on_step_zero(self, tmp_path: Path) -> None:
        from sapphire_flow.adapters.meteoswiss_nwp import CycleResolution

        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            if "datetime=2026-04-19T12:00:00Z" in str(request.url):
                return httpx.Response(200, json={"features": _cycle_features(cycle)})
            return httpx.Response(200, json={"features": []})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        now = ensure_utc(datetime(2026, 4, 19, 14, 37, 12, tzinfo=UTC))

        resolution = adapter.resolve_cycle(now)
        assert isinstance(resolution, CycleResolution)
        assert resolution.cycle_time == cycle
        assert resolution.fallback_used is False

    def test_fallback_used_when_walking_back(self, tmp_path: Path) -> None:
        from sapphire_flow.adapters.meteoswiss_nwp import CycleResolution

        # 18:30 snaps to 18:00 (empty) then falls back one 6 h step to 12:00.
        prior = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "datetime=2026-04-19T18:00:00Z" in q:
                return httpx.Response(200, json={"features": []})
            if "datetime=2026-04-19T12:00:00Z" in q:
                return httpx.Response(200, json={"features": _cycle_features(prior)})
            return httpx.Response(200, json={"features": []})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        now = ensure_utc(datetime(2026, 4, 19, 18, 30, tzinfo=UTC))

        resolution = adapter.resolve_cycle(now)
        assert isinstance(resolution, CycleResolution)
        assert resolution.cycle_time == prior
        assert resolution.fallback_used is True


class TestCycleAgeDelayGuard:
    """Plan 090 D2c/D4: the age-delay selection gate.

    A snapped cycle younger than ``cycle_min_age_minutes`` is likely still
    incompletely uploaded. The adapter must skip it and walk back to the next
    older, adequately-aged slot even when the fresh cycle IS already (partially)
    published — preferring a complete older cycle over a truncated newer one.

    Measured latency (Plan 196 T1, 2026-08-28/29, n = 6): the variables the fetch
    allowlists (``tot_prec``/``t_2m`` at +120 h) appear 160.0-173.1 min after
    reference time. The earlier "~90-120 min" figure in this docstring was a
    guess and was wrong low; Plan 213 raised the shipped guard to 210 — a
    deliberate over-estimate, since the observed max moved with one extra day.
    """

    def _make_delay_adapter(
        self, transport: httpx.MockTransport, tmp_path: Path, min_age_minutes: int
    ) -> MeteoSwissNwpAdapter:
        client = httpx.Client(transport=transport, base_url="https://dummy")
        return MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=client,
            cycle_min_age_minutes=min_age_minutes,
        )

    def test_prefers_older_aged_cycle_over_too_recent_published_one(
        self, tmp_path: Path
    ) -> None:
        from sapphire_flow.adapters.meteoswiss_nwp import CycleResolution

        # now=12:30 snaps to 12:00 (age 30 min < 105 → too recent) and walks back
        # to 06:00 (age 390 min >= 105 → adequate). BOTH cycles are published, so
        # the ONLY reason 06:00 wins is the age-delay guard. Pre-Plan-090 the
        # adapter returns the newest (12:00) because it is age-blind.
        recent = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        older = ensure_utc(datetime(2026, 4, 19, 6, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "datetime=2026-04-19T12:00:00Z" in q:
                return httpx.Response(200, json={"features": _cycle_features(recent)})
            if "datetime=2026-04-19T06:00:00Z" in q:
                return httpx.Response(200, json={"features": _cycle_features(older)})
            return httpx.Response(200, json={"features": []})

        adapter = self._make_delay_adapter(
            httpx.MockTransport(handler), tmp_path, min_age_minutes=105
        )
        now = ensure_utc(datetime(2026, 4, 19, 12, 30, tzinfo=UTC))

        resolution = adapter.resolve_cycle(now)
        assert isinstance(resolution, CycleResolution)
        assert resolution.cycle_time == older
        assert resolution.fallback_used is True
        assert resolution.fallback_reason == "too_recent"

    def test_no_walk_back_when_snapped_cycle_old_enough(self, tmp_path: Path) -> None:
        from sapphire_flow.adapters.meteoswiss_nwp import CycleResolution

        # now=14:37 snaps to 12:00 (age 157 min >= 105 → adequate); no walk-back.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            if "datetime=2026-04-19T12:00:00Z" in str(request.url):
                return httpx.Response(200, json={"features": _cycle_features(cycle)})
            return httpx.Response(200, json={"features": []})

        adapter = self._make_delay_adapter(
            httpx.MockTransport(handler), tmp_path, min_age_minutes=105
        )
        now = ensure_utc(datetime(2026, 4, 19, 14, 37, tzinfo=UTC))

        resolution = adapter.resolve_cycle(now)
        assert isinstance(resolution, CycleResolution)
        assert resolution.cycle_time == cycle
        assert resolution.fallback_used is False
        assert resolution.fallback_reason is None

    def test_not_published_reason_preserved(self, tmp_path: Path) -> None:
        from sapphire_flow.adapters.meteoswiss_nwp import CycleResolution

        # Age guard passes (all cycles old enough) but the snapped cycle is not
        # yet published → walk back with reason "not_published".
        prior = ensure_utc(datetime(2026, 4, 19, 6, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "datetime=2026-04-19T12:00:00Z" in q:
                return httpx.Response(200, json={"features": []})
            if "datetime=2026-04-19T06:00:00Z" in q:
                return httpx.Response(200, json={"features": _cycle_features(prior)})
            return httpx.Response(200, json={"features": []})

        adapter = self._make_delay_adapter(
            httpx.MockTransport(handler), tmp_path, min_age_minutes=105
        )
        now = ensure_utc(datetime(2026, 4, 19, 14, 37, tzinfo=UTC))

        resolution = adapter.resolve_cycle(now)
        assert isinstance(resolution, CycleResolution)
        assert resolution.cycle_time == prior
        assert resolution.fallback_used is True
        assert resolution.fallback_reason == "not_published"


class TestCycleIsPublishedPropertyBased:
    """T2a (Plan 067): probe matches on forecast:reference_datetime, not ID prefix.

    Phase 1 H-B confirmed that MeteoSwiss sorts items by reference_datetime
    ascending, so the first 100 items can be occluded by an older cycle's
    forward-step items. The property-based check is robust to this ordering.
    """

    def test_returns_true_when_reference_datetime_matches_without_id_prefix(
        self, tmp_path: Path
    ) -> None:
        # Simulates the H-B scenario: features whose IDs do NOT start with the
        # target cycle's <MMDDYYYY-HHMM>-0- prefix, but whose
        # forecast:reference_datetime property DOES match the target cycle.
        # Proves the probe no longer depends on ID ordering.
        cycle = ensure_utc(datetime(2026, 4, 21, 12, 0, tzinfo=UTC))
        cycle_iso = "2026-04-21T12:00:00Z"

        features = [
            {
                "id": "04212026-1200-6-tot_prec-ctrl-zzzz",
                "properties": {"forecast:reference_datetime": cycle_iso},
            },
            {
                "id": "04212026-1200-12-t_2m-ctrl-yyyy",
                "properties": {"forecast:reference_datetime": cycle_iso},
            },
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            assert "datetime=2026-04-21T12:00:00Z" in str(request.url)
            return httpx.Response(200, json={"features": features})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        assert adapter._cycle_is_published(cycle) is True

    def test_returns_false_when_no_reference_datetime_matches(
        self, tmp_path: Path
    ) -> None:
        # Simulates the H-B failure case: the first page is fully occupied by
        # forward-step items of an older cycle. Under the old prefix-check this
        # would still return True if IDs happened to prefix-match, but here no
        # reference_datetime matches the target, so the probe returns False.
        cycle = ensure_utc(datetime(2026, 4, 21, 12, 0, tzinfo=UTC))
        older_ref_dt = "2026-04-20T18:00:00Z"

        features = [
            {
                "id": f"04202026-1800-{step}-tot_prec-ctrl-aaaa",
                "properties": {"forecast:reference_datetime": older_ref_dt},
            }
            for step in range(1, 5)
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"features": features})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        assert adapter._cycle_is_published(cycle) is False

    def test_returns_false_when_features_empty(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 21, 12, 0, tzinfo=UTC))

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"features": []})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        assert adapter._cycle_is_published(cycle) is False

    def test_handles_missing_reference_datetime_property_gracefully(
        self, tmp_path: Path
    ) -> None:
        # Defensive: if some feature variants omit the property, the probe must
        # not crash on them and must still return True when any other feature
        # carries a matching reference_datetime.
        cycle = ensure_utc(datetime(2026, 4, 21, 12, 0, tzinfo=UTC))
        cycle_iso = "2026-04-21T12:00:00Z"

        features = [
            {"id": "missing-props-item", "properties": {}},
            {
                "id": "04212026-1200-3-t_2m-ctrl-xxxx",
                "properties": {"forecast:reference_datetime": cycle_iso},
            },
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"features": features})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        assert adapter._cycle_is_published(cycle) is True

    def test_raises_adapter_error_on_http_failure(self, tmp_path: Path) -> None:
        from sapphire_flow.exceptions import AdapterError

        cycle = ensure_utc(datetime(2026, 4, 21, 12, 0, tzinfo=UTC))

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "upstream failure"})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        with pytest.raises(AdapterError, match="STAC availability probe failed"):
            adapter._cycle_is_published(cycle)


class TestCycleIsPublishedPagination:
    """Probe pagination (post-Sprint-1.3 fix, 2026-04-23).

    Phase 1 H-B established that MeteoSwiss sorts items by
    ``forecast:reference_datetime`` ascending. A single-page property match
    is therefore insufficient: newer cycles' items don't land on page 1, so
    the probe reported False for cycles that were in fact published. The
    probe now walks ``rel=next`` pagination with an early exit, capped at
    ``_MAX_PROBE_PAGES``.
    """

    def test_cycle_is_published_finds_cycle_on_later_page(self, tmp_path: Path) -> None:
        # Simulate MeteoSwiss's ref_dt-ascending ordering: the first N pages
        # are filled with an older cycle's items; the target cycle's items
        # only show up on page N+1. The probe must keep walking rel=next
        # until it finds a match.
        cycle = ensure_utc(datetime(2026, 4, 23, 0, 0, tzinfo=UTC))
        cycle_iso = "2026-04-23T00:00:00Z"
        older_ref_dt = "2026-04-22T18:00:00Z"
        pages_served = 3  # three older-cycle pages before the match
        next_url_template = (
            f"{_STAC_BASE}/collections/{_STAC_COLLECTION}/items"
            f"?datetime={cycle_iso}&limit=100&page={{n}}"
        )

        page_hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            page_hits.append(q)
            if "page=" not in q:
                # Page 1: older cycle items, with rel=next → page=2
                return httpx.Response(
                    200,
                    json=_make_page(
                        [
                            {
                                "id": f"04222026-1800-{s}-tot_prec-ctrl-a",
                                "properties": {
                                    "forecast:reference_datetime": older_ref_dt
                                },
                            }
                            for s in range(3)
                        ],
                        next_url=next_url_template.format(n=2),
                    ),
                )
            # Extract page number from the URL.
            page_n = int(q.rsplit("page=", 1)[1])
            if page_n < pages_served + 1:
                return httpx.Response(
                    200,
                    json=_make_page(
                        [
                            {
                                "id": f"04222026-1800-{page_n}-t_2m-ctrl-b",
                                "properties": {
                                    "forecast:reference_datetime": older_ref_dt
                                },
                            }
                        ],
                        next_url=next_url_template.format(n=page_n + 1),
                    ),
                )
            # Page N+1: target-cycle items surface.
            return httpx.Response(
                200,
                json=_make_page(
                    [
                        {
                            "id": "04232026-0000-0-tot_prec-ctrl-target",
                            "properties": {"forecast:reference_datetime": cycle_iso},
                        },
                    ]
                ),
            )

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        assert adapter._cycle_is_published(cycle) is True
        # Must have walked through all older-cycle pages plus the match page.
        assert len(page_hits) == pages_served + 1

    def test_cycle_is_published_exhausts_pages_returns_false(
        self, tmp_path: Path
    ) -> None:
        # Every page carries a rel=next link pointing back to a non-terminating
        # URL, and no page ever matches the target ref_dt. The probe must abort
        # once pages_walked reaches _MAX_PROBE_PAGES and return False.
        from sapphire_flow.adapters.meteoswiss_nwp import _MAX_PROBE_PAGES

        cycle = ensure_utc(datetime(2026, 4, 23, 0, 0, tzinfo=UTC))
        older_ref_dt = "2026-04-22T18:00:00Z"
        never_terminating_url = (
            f"{_STAC_BASE}/collections/{_STAC_COLLECTION}/items"
            "?datetime=2026-04-23T00:00:00Z&limit=100&page=next"
        )
        page_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal page_count
            page_count += 1
            return httpx.Response(
                200,
                json=_make_page(
                    [
                        {
                            "id": "04222026-1800-0-tot_prec-ctrl-old",
                            "properties": {"forecast:reference_datetime": older_ref_dt},
                        }
                    ],
                    next_url=never_terminating_url,
                ),
            )

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        assert adapter._cycle_is_published(cycle) is False
        # Cap must bound the walk at exactly _MAX_PROBE_PAGES pages.
        assert page_count == _MAX_PROBE_PAGES

    def test_cycle_is_published_stops_at_empty_next(self, tmp_path: Path) -> None:
        # Single page, non-matching items, and NO rel=next link. The probe
        # must terminate after one HTTP call and return False.
        cycle = ensure_utc(datetime(2026, 4, 23, 0, 0, tzinfo=UTC))
        older_ref_dt = "2026-04-22T18:00:00Z"
        page_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal page_count
            page_count += 1
            return httpx.Response(
                200,
                json=_make_page(
                    [
                        {
                            "id": "04222026-1800-0-tot_prec-ctrl-old",
                            "properties": {"forecast:reference_datetime": older_ref_dt},
                        }
                    ],
                    next_url=None,
                ),
            )

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        assert adapter._cycle_is_published(cycle) is False
        assert page_count == 1


class TestMaxFallbackStepsKwarg:
    """Plan 067 T3.b: ``max_fallback_steps`` is an instance kwarg.

    The old module-level ``_MAX_FALLBACK_STEPS`` constant has been removed.
    """

    def test_default_is_two(self, tmp_path: Path) -> None:
        # Plan 067 D2: default of 2 matches the corrected-cadence policy
        # (ceil(default_nwp_max_fallback_age_hours=12.0 / 6.0) = 2).
        adapter = _make_adapter(
            httpx.MockTransport(
                lambda _req: httpx.Response(200, json={"features": []})
            ),
            tmp_path,
        )
        assert adapter.max_fallback_steps == 2

    def test_explicit_value_is_honoured(self, tmp_path: Path) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(200, json={"features": []})
            ),
            base_url="https://dummy",
        )
        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=client,
            max_fallback_steps=7,
        )
        assert adapter.max_fallback_steps == 7

    def test_module_constant_is_removed(self) -> None:
        # Plan 067 T3.b: the old module-level _MAX_FALLBACK_STEPS is gone.
        import sapphire_flow.adapters.meteoswiss_nwp as mod

        assert not hasattr(mod, "_MAX_FALLBACK_STEPS")

    @pytest.mark.parametrize(
        ("max_fallback_steps", "expected_probe_count"),
        [(0, 1), (1, 2), (2, 3), (3, 4)],
    )
    def test_resolve_cycle_time_respects_max_fallback_steps(
        self,
        tmp_path: Path,
        max_fallback_steps: int,
        expected_probe_count: int,
    ) -> None:
        # Plan 067 T3.b: resolve_cycle_time probes max_fallback_steps + 1
        # cycles (snapped + N fallbacks) before raising.
        probe_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal probe_count
            probe_count += 1
            return httpx.Response(200, json={"features": []})

        client = httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://dummy"
        )
        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=client,
            max_fallback_steps=max_fallback_steps,
        )
        now = ensure_utc(datetime(2026, 4, 21, 12, 0, tzinfo=UTC))
        with pytest.raises(NoCycleAvailableError):
            adapter.resolve_cycle_time(now)
        assert probe_count == expected_probe_count

    def test_error_message_cites_instance_value(self, tmp_path: Path) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _req: httpx.Response(200, json={"features": []})
            ),
            base_url="https://dummy",
        )
        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=client,
            max_fallback_steps=5,
        )
        now = ensure_utc(datetime(2026, 4, 21, 12, 0, tzinfo=UTC))
        with pytest.raises(NoCycleAvailableError, match="within 5 fallback steps"):
            adapter.resolve_cycle_time(now)


class TestSnapToCycleCadence:
    """Plan 067 T3.d: cycles publish at 6 h cadence (0, 6, 12, 18 UTC)."""

    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            (
                datetime(2026, 4, 21, 7, 30, tzinfo=UTC),
                datetime(2026, 4, 21, 6, 0, tzinfo=UTC),
            ),
            (
                datetime(2026, 4, 21, 11, 59, tzinfo=UTC),
                datetime(2026, 4, 21, 6, 0, tzinfo=UTC),
            ),
            (
                datetime(2026, 4, 21, 12, 0, tzinfo=UTC),
                datetime(2026, 4, 21, 12, 0, tzinfo=UTC),
            ),
            (
                datetime(2026, 4, 21, 21, 0, tzinfo=UTC),
                datetime(2026, 4, 21, 18, 0, tzinfo=UTC),
            ),
        ],
    )
    def test_snap_to_cycle_uses_six_hourly_grid(
        self, now: datetime, expected: datetime
    ) -> None:
        snapped = MeteoSwissNwpAdapter._snap_to_cycle(ensure_utc(now))
        assert snapped == ensure_utc(expected)

    def test_cycle_hours_tuple_is_six_hourly(self) -> None:
        from sapphire_flow.adapters.meteoswiss_nwp import _CYCLE_HOURS

        assert _CYCLE_HOURS == (0, 6, 12, 18)


class TestMaxFallbackStepsFromConfig:
    """Plan 067 T3.c: callers derive max_fallback_steps from DeploymentConfig."""

    @pytest.mark.parametrize(
        ("age_hours", "expected_steps"),
        [
            (12.0, 2),
            (6.0, 1),
            (0.0, 0),
            (1.5, 1),
            (18.0, 3),
        ],
    )
    def test_ceil_div_six_maps_age_hours_to_steps(
        self, age_hours: float, expected_steps: int
    ) -> None:
        import math

        assert math.ceil(age_hours / 6.0) == expected_steps


class TestParamGroups:
    def test_three_column_shape(self) -> None:
        from sapphire_flow.adapters.meteoswiss_nwp import PARAM_GROUPS

        assert len(PARAM_GROUPS) >= 1
        for row in PARAM_GROUPS:
            assert isinstance(row, tuple)
            assert len(row) == 3
            stac_token, short_name, type_of_level = row
            assert isinstance(stac_token, str) and stac_token
            assert isinstance(short_name, str) and short_name
            assert isinstance(type_of_level, str) and type_of_level

    def test_exact_param_group_tuples(self) -> None:
        # Exact-value pin so the separate Recap variable catalog (Plan 081 Task 2A)
        # cannot silently drift the Swiss STAC token / cfgrib shortName / typeOfLevel
        # extraction keys.
        from sapphire_flow.adapters.meteoswiss_nwp import PARAM_GROUPS

        assert list(PARAM_GROUPS) == [
            ("tot_prec", "tp", "surface"),
            ("t_2m", "2t", "heightAboveGround"),
        ]


def _make_page(
    features: list[dict[str, object]], next_url: str | None = None
) -> dict[str, object]:
    links: list[dict[str, object]] = []
    if next_url is not None:
        links.append({"rel": "next", "href": next_url})
    return {"features": features, "links": links}


def _make_item(
    stac_token: str,
    step: int = 0,
    size: int | None = None,
    ref_dt: str = "2026-04-19T12:00:00Z",
) -> dict[str, object]:
    # ref_dt default matches the 2026-04-19T12:00Z cycle the legacy tests use.
    # T2b (Plan 067) filters by forecast:reference_datetime property; items
    # without a matching ref_dt are dropped inside _fetch_grib_files.
    # The ID-prefix date is derived from ref_dt so prefix and property stay
    # consistent for tests that inspect both.
    dt = datetime.strptime(ref_dt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    id_prefix = dt.strftime("%m%d%Y-%H%M")
    item_id = f"{id_prefix}-{step}-{stac_token}-ctrl-abcd1234"
    asset_key = f"icon-ch2-eps-{dt:%Y%m%d%H%M}-{step}-{stac_token}-ctrl.grib2"
    asset: dict[str, object] = {
        "type": "application/grib",
        "href": f"https://rgw.cscs.ch/bucket/{asset_key}?AWSAccessKeyId=x&Signature=y&Expires=9999999999",
        "roles": ["data"],
    }
    if size is not None:
        asset["size"] = size
    return {
        "id": item_id,
        "properties": {"forecast:reference_datetime": ref_dt},
        "assets": {asset_key: asset},
    }


class TestFetchGribFiles:
    def test_skips_unallowed_variables(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        features = [
            _make_item("tot_prec"),
            _make_item("t_2m"),
            _make_item("alb_rad"),
            _make_item("qv"),
            _make_item("h_snow"),
        ]
        download_hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "/items" in q:
                return httpx.Response(200, json=_make_page(features))
            if ".grib2" in q:
                download_hits.append(q)
                return httpx.Response(200, content=b"GRIB" + b"\x00" * 100)
            return httpx.Response(404)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        files = adapter._fetch_grib_files(cycle)
        assert len(files) == 2
        assert all(any(t in str(f) for t in ("tot_prec", "t_2m")) for f in files)
        assert len(download_hits) == 2

    def test_raises_on_budget_exceeded(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        huge = 10 * 1024 * 1024  # 10 MB each
        features = [_make_item("tot_prec", step=s, size=huge) for s in range(10)]

        def handler(request: httpx.Request) -> httpx.Response:
            if "/items" in str(request.url):
                return httpx.Response(200, json=_make_page(features))
            return httpx.Response(200, content=b"GRIB" + b"\x00")

        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=httpx.Client(
                transport=httpx.MockTransport(handler), base_url="https://dummy"
            ),
            max_download_bytes=5 * huge,
            disk_guard_enabled=False,
        )
        from sapphire_flow.exceptions import BudgetExceededError

        with pytest.raises(BudgetExceededError, match="Download size cap") as exc_info:
            adapter._fetch_grib_files(cycle)

        # Plan 223 D6: structured fields so a downstream constructed reason
        # can name which cap tripped without parsing `str(exc)`.
        assert exc_info.value.kind == "byte"
        assert exc_info.value.observed > 5 * huge
        assert exc_info.value.limit == 5 * huge

    def test_raises_on_file_count_exceeded(self, tmp_path: Path) -> None:
        """Plan 223 — the 2026-08-29 outage shape: many small GRIB files
        trip the file-count cap, not the byte cap. The exception must
        carry structured fields naming the CAP KIND plus the
        observed/limit numbers (D6), distinct from the byte-cap case.

        Plan 221: this deliberately builds `_MAX_FILE_COUNT + 1` items
        rather than a literal 501. The original literal encoded the cap's
        value (then 500) rather than its CONTRACT, so raising the cap to
        2000 turned a passing test red without any behaviour regressing.
        A guard test must assert "exceeding the cap raises", never a
        specific number the cap is expected to change to."""
        from sapphire_flow.adapters.meteoswiss_nwp import _MAX_FILE_COUNT

        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        features = [_make_item("tot_prec", step=s) for s in range(_MAX_FILE_COUNT + 1)]

        def handler(request: httpx.Request) -> httpx.Response:
            if "/items" in str(request.url):
                return httpx.Response(200, json=_make_page(features))
            return httpx.Response(200, content=b"GRIB" + b"\x00")

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        from sapphire_flow.exceptions import BudgetExceededError

        with pytest.raises(
            BudgetExceededError, match="GRIB file count exceeded"
        ) as exc_info:
            adapter._fetch_grib_files(cycle)

        assert exc_info.value.kind == "file_count"
        assert exc_info.value.observed == _MAX_FILE_COUNT + 1
        assert exc_info.value.limit == _MAX_FILE_COUNT

    def test_creates_per_cycle_scratch_dir(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            if "/items" in str(request.url):
                return httpx.Response(200, json=_make_page([_make_item("tot_prec")]))
            return httpx.Response(200, content=b"GRIB" + b"\x00" * 50)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        adapter._fetch_grib_files(cycle)
        expected = tmp_path / "20260419T1200"
        assert expected.exists() and expected.is_dir()
        assert list(expected.glob("*.grib2"))

    def test_cleans_scratch_on_entry(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        per_cycle = tmp_path / "20260419T1200"
        per_cycle.mkdir(parents=True)
        junk = per_cycle / "stale.grib2"
        junk.write_bytes(b"not grib")

        def handler(request: httpx.Request) -> httpx.Response:
            if "/items" in str(request.url):
                return httpx.Response(200, json=_make_page([_make_item("tot_prec")]))
            return httpx.Response(200, content=b"GRIB" + b"\x00" * 50)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        adapter._fetch_grib_files(cycle)
        assert not junk.exists()

    def test_raises_on_truncated_grib(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            if "/items" in str(request.url):
                return httpx.Response(200, json=_make_page([_make_item("tot_prec")]))
            return httpx.Response(200, content=b"ABCD" + b"\x00" * 50)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        with pytest.raises(Exception, match="truncated or non-GRIB2"):
            adapter._fetch_grib_files(cycle)

    def test_timeout_surfaces_as_adapter_error(self, tmp_path: Path) -> None:
        from sapphire_flow.exceptions import AdapterError

        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("simulated timeout")

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        with pytest.raises(AdapterError, match="timed out"):
            adapter._fetch_grib_files(cycle)


class TestFetchGribFilesReferenceDatetimeFilter:
    """T2b (Plan 067): client-side forecast:reference_datetime filter.

    Phase 1 H-C confirmed: MeteoSwiss's `?datetime=<cycle>/<cycle+120h>`
    range matches items from every cycle whose forecast horizon overlaps
    that window (~72 % of items belonged to non-target cycles in the
    dress rehearsal). CQL is not supported server-side (T1.e), so the
    filter must run client-side inside the pagination loop.
    """

    def test_drops_feature_with_nonmatching_reference_datetime_despite_id_prefix(
        self, tmp_path: Path
    ) -> None:
        # The old prefix-based check would have accepted this feature because
        # its ID starts with the target cycle's <MMDDYYYY-HHMM>- prefix.
        # The T2b property-based filter rejects it because its
        # forecast:reference_datetime points to a different cycle.
        # Demonstrates the property, not the prefix, is the active mechanism.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        target_prefix = "04192026-1200"

        misleading = {
            "id": f"{target_prefix}-6-tot_prec-ctrl-mismatch",
            "properties": {
                # Points to a DIFFERENT cycle even though the ID prefix matches.
                "forecast:reference_datetime": "2026-04-18T12:00:00Z",
            },
            "assets": {
                "x.grib2": {
                    "type": "application/grib",
                    "href": "https://rgw.cscs.ch/bucket/x.grib2",
                }
            },
        }
        download_hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "/items" in q:
                return httpx.Response(200, json=_make_page([misleading]))
            download_hits.append(q)
            return httpx.Response(200, content=b"GRIB" + b"\x00" * 50)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        from sapphire_flow.exceptions import AdapterError

        with pytest.raises(AdapterError, match="No matching GRIB2 files"):
            adapter._fetch_grib_files(cycle)
        assert download_hits == []

    def test_keeps_feature_with_matching_reference_datetime(
        self, tmp_path: Path
    ) -> None:
        # Happy path: property matches → feature is processed.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        features = [_make_item("tot_prec", ref_dt="2026-04-19T12:00:00Z")]
        download_hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "/items" in q:
                return httpx.Response(200, json=_make_page(features))
            download_hits.append(q)
            return httpx.Response(200, content=b"GRIB" + b"\x00" * 50)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        files = adapter._fetch_grib_files(cycle)
        assert len(files) == 1
        assert len(download_hits) == 1

    def test_mixed_cycle_response_filters_to_target_only(self, tmp_path: Path) -> None:
        # Reproduces Phase 1 T1.c observation: the server returns items from
        # multiple cycles whose forecast horizons overlap the 120 h window.
        # Only target-cycle items should be downloaded.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        features = [
            _make_item("tot_prec", step=6, ref_dt="2026-04-19T12:00:00Z"),
            _make_item("t_2m", step=12, ref_dt="2026-04-19T12:00:00Z"),
            _make_item("tot_prec", step=0, ref_dt="2026-04-19T06:00:00Z"),
            _make_item("t_2m", step=0, ref_dt="2026-04-19T00:00:00Z"),
        ]
        download_hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "/items" in q:
                return httpx.Response(200, json=_make_page(features))
            download_hits.append(q)
            return httpx.Response(200, content=b"GRIB" + b"\x00" * 50)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        files = adapter._fetch_grib_files(cycle)
        assert len(files) == 2
        assert len(download_hits) == 2
        # All downloaded filenames should carry the target cycle's date stamp.
        for f in files:
            assert "202604191200" in str(f)

    def test_drops_feature_with_missing_reference_datetime_property(
        self, tmp_path: Path
    ) -> None:
        # Defensive: a feature without the property must be treated as
        # non-matching, not as a cache-bypass. Item would have matched the
        # allowlist token if the ref_dt check didn't run first.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        features = [
            {
                "id": "04192026-1200-0-tot_prec-ctrl-noprop",
                "properties": {},
                "assets": {
                    "x.grib2": {
                        "type": "application/grib",
                        "href": "https://rgw.cscs.ch/bucket/x.grib2",
                    }
                },
            },
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            if "/items" in str(request.url):
                return httpx.Response(200, json=_make_page(features))
            return httpx.Response(200, content=b"GRIB" + b"\x00" * 50)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        from sapphire_flow.exceptions import AdapterError

        with pytest.raises(AdapterError, match="No matching GRIB2 files"):
            adapter._fetch_grib_files(cycle)


def _make_n_page_handler(
    total_pages: int, final_items: list[dict[str, object]] | None
) -> object:
    """Serve exactly `total_pages` pages via rel=next, items only on the last.

    Unlike `_paged_handler` (which slices a feature list by offset), this
    tracks call count in closure state so tests can cheaply simulate a
    walk hundreds/thousands of pages deep without materializing that many
    STAC items. All of `final_items` land on the final page (so a test can
    place several same-cycle items — allowlisted or not — to exercise the
    ref_dt-count-before-allowlist ordering). The GRIB asset itself is served
    on every request whose URL contains ``.grib2``.
    """
    calls = {"n": 0}
    next_url = (
        f"{_STAC_BASE}/collections/{_STAC_COLLECTION}/items"
        "?datetime=2026-04-19T12:00:00Z/2026-04-24T12:00:00Z&limit=100&page=next"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if ".grib2" in str(request.url):
            return httpx.Response(200, content=b"GRIB" + b"\x00" * 50)
        calls["n"] += 1
        is_last = calls["n"] >= total_pages
        features = list(final_items) if (is_last and final_items) else []
        nxt = None if is_last else next_url
        return httpx.Response(200, json=_make_page(features, next_url=nxt))

    return handler


class TestPaginationCap:
    """Plan 140 T2: pagination cap raised 800 -> 1500 (re-benchmarked 2026-07-22).

    T1 re-benchmarked the live catalog at 861 pages (still 4 cycles / 24 h
    retention; items/cycle grew +56 %). 1500 = 861 * ~1.7 safety margin. The
    cap is required because CQL is not supported server-side (Plan 067
    T1.e), so the adapter always walks the full 120 h datetime-range window.
    """

    def test_max_pagination_pages_constant_is_fifteen_hundred(self) -> None:
        # Locks the value against accidental change and documents the T1
        # re-benchmark. Raising this requires re-benchmarking again.
        from sapphire_flow.adapters.meteoswiss_nwp import _MAX_PAGINATION_PAGES

        assert _MAX_PAGINATION_PAGES == 1500

    def test_pagination_cap_raises_after_max_pages(self, tmp_path: Path) -> None:
        # Simulate an infinite pagination chain: every response carries a
        # rel=next link that points back into /items. The adapter should abort
        # once page_count exceeds _MAX_PAGINATION_PAGES (1500).
        from sapphire_flow.exceptions import AdapterError

        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        next_url = (
            f"{_STAC_BASE}/collections/{_STAC_COLLECTION}/items"
            "?datetime=2026-04-19T12:00:00Z/2026-04-24T12:00:00Z&limit=100&page=next"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            # Return no matching features but always emit a next link so the
            # loop never terminates naturally.
            return httpx.Response(200, json=_make_page([], next_url=next_url))

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        with pytest.raises(AdapterError, match="exceeded 1500 pages"):
            adapter._fetch_grib_files(cycle)

    def test_completes_above_old_cap_below_new_cap(self, tmp_path: Path) -> None:
        # 850 pages exceeds the OLD cap (800) but is comfortably under the
        # NEW cap (1500) — this is the outage this plan fixes: a fetch that
        # would previously abort now completes.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        item = _make_item("tot_prec", ref_dt="2026-04-19T12:00:00Z")
        handler = _make_n_page_handler(850, [item])

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]
        files = adapter._fetch_grib_files(cycle)
        assert len(files) == 1

    def test_near_cap_warning_fires_at_eighty_percent_threshold(
        self, tmp_path: Path
    ) -> None:
        # 1200 pages = 80% of the 1500 cap. The fetch must still complete
        # (this is an early warning, not an abort) but must emit exactly one
        # WARNING naming the page count.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        item = _make_item("tot_prec", ref_dt="2026-04-19T12:00:00Z")
        handler = _make_n_page_handler(1200, [item])

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(cycle)

        assert len(files) == 1
        warnings = [e for e in captured if e.get("event") == "nwp.pagination_near_cap"]
        assert len(warnings) == 1
        assert warnings[0]["page_count"] == 1200
        # Must be a WARNING, not an INFO — the whole point of the near-cap
        # signal is to page ops before the treadmill hits the abort cap.
        assert warnings[0]["log_level"] == "warning"

    def test_no_near_cap_warning_below_threshold(self, tmp_path: Path) -> None:
        # 850 pages is below the 1200-page (80%) warning threshold — no
        # WARNING should fire, only the routine completion INFO log.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        item = _make_item("tot_prec", ref_dt="2026-04-19T12:00:00Z")
        handler = _make_n_page_handler(850, [item])

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as captured:
            adapter._fetch_grib_files(cycle)

        warnings = [e for e in captured if e.get("event") == "nwp.pagination_near_cap"]
        assert warnings == []

    def test_stac_walk_completed_log_includes_page_and_item_counts(
        self, tmp_path: Path
    ) -> None:
        # Observability: a successful fetch logs the actual page count and
        # matched target-cycle item count so the next breach shows up as a
        # trend, not a silent outage. This is a narrower event name
        # (nwp.stac_walk_completed), distinct from the canonical
        # nwp.fetch_completed emitted by fetch_forecasts() only after
        # parse/archive/extraction also succeed (docs/standards/logging.md:
        # duration_ms is mandatory on all *.completed events, and the two
        # events must never collide under the same name).
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        # Two same-cycle items on the final page — one allowlisted (tot_prec),
        # one NOT (alb_rad). matched_ref_dt_count must be 2 (both ref_dt-match)
        # while files_fetched is 1 (only the allowlisted item downloads). This
        # proves the counter is incremented BEFORE the variable-allowlist skip;
        # if it sat after the skip, matched_ref_dt_count would collapse to 1.
        allowlisted = _make_item("tot_prec", ref_dt="2026-04-19T12:00:00Z")
        not_allowlisted = _make_item("alb_rad", ref_dt="2026-04-19T12:00:00Z")
        handler = _make_n_page_handler(5, [allowlisted, not_allowlisted])

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(cycle)

        assert len(files) == 1
        completed = [e for e in captured if e.get("event") == "nwp.stac_walk_completed"]
        assert len(completed) == 1
        assert completed[0]["page_count"] == 5
        assert completed[0]["matched_ref_dt_count"] == 2
        assert completed[0]["files_fetched"] == 1
        assert isinstance(completed[0]["duration_ms"], int)
        # Must not collide with the canonical nwp.fetch_completed event name
        # (emitted separately by fetch_forecasts after parse/archive succeed).
        assert [e for e in captured if e.get("event") == "nwp.fetch_completed"] == []


def _per_file_ds(
    *,
    member: int,
    step_hours: int,
    var: str,
) -> xr.Dataset:
    # Simulate the shape cfgrib produces for a single MeteoSwiss GRIB message:
    # scalar `number` (ensemble index), scalar `valid_time`, one variable,
    # 2D grid. Dims are (latitude, longitude); member/time are scalar coords.
    base = datetime(2026, 4, 23, 0, 0, tzinfo=UTC)
    valid_time = np.datetime64(base.replace(tzinfo=None), "ns") + np.timedelta64(
        step_hours, "h"
    )
    return xr.Dataset(
        {
            var: xr.DataArray(
                np.full((2, 2), float(member * 100 + step_hours), dtype=np.float32),
                dims=["latitude", "longitude"],
            )
        },
        coords={
            "number": member,
            "valid_time": valid_time,
            "latitude": [46.0, 46.1],
            "longitude": [7.0, 7.1],
        },
    )


class TestCombineCfgribDatasets:
    """_combine_cfgrib_datasets: member-then-valid_time stacking from per-file.

    Each input represents one GRIB message (scalar member, scalar valid_time,
    one 2D grid). Output must be (number, valid_time, latitude, longitude)
    with valid_time monotonic within each member.
    """

    def test_single_member_concats_along_valid_time(self) -> None:
        ds = _combine_cfgrib_datasets(
            [
                _per_file_ds(member=0, step_hours=3, var="tp"),
                _per_file_ds(member=0, step_hours=0, var="tp"),
                _per_file_ds(member=0, step_hours=6, var="tp"),
            ]
        )
        assert "valid_time" in ds.dims
        assert ds.sizes["valid_time"] == 3
        # Sorted within-member → monotonic time axis.
        times = ds.coords["valid_time"].values
        assert list(times) == sorted(times)

    def test_multi_member_concats_along_number(self) -> None:
        ds = _combine_cfgrib_datasets(
            [
                _per_file_ds(member=1, step_hours=0, var="tp"),
                _per_file_ds(member=0, step_hours=0, var="tp"),
                _per_file_ds(member=1, step_hours=3, var="tp"),
                _per_file_ds(member=0, step_hours=3, var="tp"),
            ]
        )
        assert "number" in ds.dims
        assert ds.sizes["number"] == 2
        assert ds.sizes["valid_time"] == 2
        # Member dim sorted for determinism.
        assert list(ds.coords["number"].values) == [0, 1]

    def test_output_rename_through_convert_raw_dataset(self) -> None:
        # Downstream extractor expects `member`, not `number`.
        # The adapter pipeline is: _combine_cfgrib_datasets → xr.merge →
        # convert_raw_dataset (which renames number → member).
        ds = _combine_cfgrib_datasets(
            [
                _per_file_ds(member=0, step_hours=0, var="t2m"),
                _per_file_ds(member=1, step_hours=0, var="t2m"),
            ]
        )
        # Inside the helper the dim is still "number" (cfgrib convention).
        assert "number" in ds.dims
        # convert_raw_dataset is responsible for the rename.
        renamed = convert_raw_dataset(
            ds.assign({"t2m": ds["t2m"].astype(np.float32) + 273.15})
        )
        assert "member" in renamed.dims
        assert "number" not in renamed.dims


def _make_paged_items(count: int) -> list[dict[str, object]]:
    # Build N tp items spread across the implicit server pagination. Each has
    # a unique step so the asset filenames differ (required by
    # _download_asset's scratch-dir path-traversal check).
    return [_make_item("tot_prec", step=s) for s in range(count)]


class TestMaxFilesCap:
    """``max_files`` scope-limiter: caps per-cycle GRIB fetches with a graceful stop.

    Distinct from ``max_download_bytes`` (safety cap, raises) — ``max_files``
    is a scope-limiter for smoke tests / sampled runs. Default ``None``
    preserves unlimited production behaviour.
    """

    @staticmethod
    def _paged_handler(
        features: list[dict[str, object]], page_size: int = 10
    ) -> object:
        """Serve `features` split into pages of `page_size`, linked via rel=next."""
        base = f"{_STAC_BASE}/collections/{_STAC_COLLECTION}/items"

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if ".grib2" in q:
                return httpx.Response(200, content=b"GRIB" + b"\x00" * 50)
            # Extract offset from query string (default 0).
            offset = 0
            if "offset=" in q:
                offset = int(q.rsplit("offset=", 1)[1].split("&")[0])
            chunk = features[offset : offset + page_size]
            next_offset = offset + page_size
            next_url: str | None = None
            if next_offset < len(features):
                next_url = f"{base}?datetime=x&offset={next_offset}"
            return httpx.Response(200, json=_make_page(chunk, next_url=next_url))

        return handler

    def test_max_files_stops_fetch_gracefully(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        features = _make_paged_items(100)
        handler = self._paged_handler(features, page_size=10)

        client = httpx.Client(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
            base_url="https://dummy",
        )
        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=client,
            max_files=5,
            disk_guard_enabled=False,
        )

        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(cycle)

        assert len(files) == 5
        cap_events = [e for e in captured if e.get("event") == "nwp.fetch_cap_reached"]
        assert len(cap_events) == 1
        assert cap_events[0]["files_fetched"] == 5
        assert cap_events[0]["max_files_cap"] == 5

    def test_max_files_none_preserves_unlimited(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        features = _make_paged_items(100)
        handler = self._paged_handler(features, page_size=10)

        client = httpx.Client(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
            base_url="https://dummy",
        )
        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=client,
            disk_guard_enabled=False,
            # max_files defaults to None = unlimited.
        )

        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(cycle)

        # All 100 items are allowlisted (tp) and well below the runaway guard
        # (_MAX_FILE_COUNT).
        assert len(files) == 100
        cap_events = [e for e in captured if e.get("event") == "nwp.fetch_cap_reached"]
        assert cap_events == []

    def test_max_files_larger_than_available(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        features = _make_paged_items(3)
        handler = self._paged_handler(features, page_size=10)

        client = httpx.Client(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
            base_url="https://dummy",
        )
        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=client,
            max_files=1000,
            disk_guard_enabled=False,
        )

        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(cycle)

        assert len(files) == 3
        cap_events = [e for e in captured if e.get("event") == "nwp.fetch_cap_reached"]
        assert cap_events == []

    def test_max_files_zero_fetches_nothing(self, tmp_path: Path) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        features = _make_paged_items(10)
        handler = self._paged_handler(features, page_size=10)

        client = httpx.Client(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
            base_url="https://dummy",
        )
        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=client,
            max_files=0,
            disk_guard_enabled=False,
        )

        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(cycle)

        assert files == []
        cap_events = [e for e in captured if e.get("event") == "nwp.fetch_cap_reached"]
        assert len(cap_events) == 1
        assert cap_events[0]["files_fetched"] == 0
        assert cap_events[0]["max_files_cap"] == 0


class TestMaxFileCountRunawayGuard:
    """Plan 221: ``_MAX_FILE_COUNT`` is a runaway guard, not an operating limit.

    2026-08-31 live outage: ICON-CH2-EPS cycles publishing 501 allowlisted
    GRIB files tripped the (then) 500-file cap and aborted every affected
    forecast cycle with zero forecasts written. The cap must sit well clear
    of the observed 484-501 working range (D3: raised to 2000) while still
    catching a genuine runaway.
    """

    def test_observed_working_range_of_501_files_does_not_trip_the_cap(
        self, tmp_path: Path
    ) -> None:
        # 501 is the exact count that caused the outage (D3). No `size` is
        # set on these items, so each falls back to _ASSET_SIZE_ESTIMATE_BYTES
        # (2 MiB) — 501 * 2 MiB ≈ 1.0 GiB, well under the 4 GiB byte budget,
        # so only the file-count cap is exercised here.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        features = _make_paged_items(501)
        handler = TestMaxFilesCap._paged_handler(features, page_size=100)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]

        files = adapter._fetch_grib_files(cycle)

        assert len(files) == 501


class TestShippedCycleMinAgeGuard:
    """Plan 213: the guard VALUE shipped in ``config.toml``, not the mechanism.

    ``TestCycleAgeDelayGuard`` above covers the mechanism with literal ages, and
    passes for any value. These two tests pin the value we actually ship — the
    only thing Plan 213 changes — so they must read it from ``config.toml``
    rather than from a literal, or they would pass with none of the change
    applied.
    """

    @staticmethod
    def _shipped_min_age(monkeypatch: pytest.MonkeyPatch) -> int:
        # load_config always applies SAPPHIRE_CONFIG_OVERLAY when set, which
        # would silently substitute a different value for the base file's.
        monkeypatch.delenv("SAPPHIRE_CONFIG_OVERLAY", raising=False)
        from pathlib import Path as _Path

        from sapphire_flow.config.deployment import load_config

        repo_root = _Path(__file__).resolve().parents[3]
        return load_config(repo_root / "config.toml").nwp_cycle_min_age_minutes

    def test_shipped_config_clears_the_measured_publication_latency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Plan 196 T1 measured 160.0-173.1 min (n = 6) for the variables the fetch
        # needs. 210 is a deliberate over-estimate above that (Plan 213 D3).
        assert self._shipped_min_age(monkeypatch) == 210

    def test_shipped_value_skips_a_cycle_that_is_published_but_incomplete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.adapters.meteoswiss_nwp import CycleResolution

        # 14:30 snaps to 12:00 → age 150 min. Under the old 105 that was
        # ACCEPTED; measurement says the cycle is still publishing until ~168.
        # BOTH cycles are published, so a walk-back can only be caused by the
        # age guard — asserting fallback_reason rules out `not_published`.
        recent = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        older = ensure_utc(datetime(2026, 4, 19, 6, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "datetime=2026-04-19T12:00:00Z" in q:
                return httpx.Response(200, json={"features": _cycle_features(recent)})
            if "datetime=2026-04-19T06:00:00Z" in q:
                return httpx.Response(200, json={"features": _cycle_features(older)})
            return httpx.Response(200, json={"features": []})

        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=httpx.Client(
                transport=httpx.MockTransport(handler), base_url="https://dummy"
            ),
            cycle_min_age_minutes=self._shipped_min_age(monkeypatch),
        )

        resolution = adapter.resolve_cycle(
            ensure_utc(datetime(2026, 4, 19, 14, 30, tzinfo=UTC))
        )
        assert isinstance(resolution, CycleResolution)
        assert resolution.cycle_time == older
        assert resolution.fallback_used is True
        assert resolution.fallback_reason == "too_recent"

    @pytest.mark.parametrize("min_age", [105, 210])
    def test_on_time_cron_run_resolves_identically_under_both_values(
        self, tmp_path: Path, min_age: int
    ) -> None:
        from sapphire_flow.adapters.meteoswiss_nwp import CycleResolution

        # The property Plan 213 must not disturb: at an on-grid cron instant the
        # candidates are ~0 and ~360 min old, so 105 and 210 decide the same.
        # This test is expected to pass BEFORE and AFTER the change.
        on_grid = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        previous = ensure_utc(datetime(2026, 4, 19, 6, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "datetime=2026-04-19T12:00:00Z" in q:
                return httpx.Response(200, json={"features": _cycle_features(on_grid)})
            if "datetime=2026-04-19T06:00:00Z" in q:
                return httpx.Response(200, json={"features": _cycle_features(previous)})
            return httpx.Response(200, json={"features": []})

        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=httpx.Client(
                transport=httpx.MockTransport(handler), base_url="https://dummy"
            ),
            cycle_min_age_minutes=min_age,
        )

        resolution = adapter.resolve_cycle(on_grid)
        assert isinstance(resolution, CycleResolution)
        assert resolution.cycle_time == previous
        assert resolution.fallback_used is True
        assert resolution.fallback_reason == "too_recent"


# ---------------------------------------------------------------------------
# Plan 237 T1 — dedup on collision-safe identity, collision-proof destination
# naming, and a completeness assertion so an incomplete cycle fails loudly
# instead of returning a NaN-filled dataset that gets stored as real data.
# ---------------------------------------------------------------------------

_REAL_FIXTURE_DIR = (
    Path(__file__).parent.parent.parent
    / "fixtures"
    / "meteoswiss_nwp"
    / "icon_ch2_eps_202604231200"
)
_REAL_CYCLE = ensure_utc(datetime(2026, 4, 23, 12, 0, tzinfo=UTC))


def _real_fixture_path(stac_token: str, step: int, variant: str) -> Path:
    return (
        _REAL_FIXTURE_DIR
        / f"icon-ch2-eps-202604231200-{step}-{stac_token}-{variant}.grib2"
    )


def _skip_unless_real_fixtures() -> None:
    # `Path.glob()` returns a generator, which is ALWAYS truthy — the previous
    # `if not ...glob(...)` could never skip (independent review, Plan 237).
    # Materialise it so the guard actually guards.
    if not any(_REAL_FIXTURE_DIR.glob("*.grib2")):
        pytest.skip(f"real fixtures not found at {_REAL_FIXTURE_DIR}")


def _real_fixture_item(
    stac_token: str, step: int, variant: str, *, query: str
) -> dict[str, object]:
    # A real MeteoSwiss STAC item shape: one asset, a presigned href whose
    # QUERY carries the per-request signature (Plan 237 T1: dedup must key
    # on netloc+path with the query stripped, never the raw href).
    file_name = f"icon-ch2-eps-202604231200-{step}-{stac_token}-{variant}.grib2"
    return {
        "id": f"04232026-1200-{step}-{stac_token}-{variant}-{query}",
        "properties": {"forecast:reference_datetime": "2026-04-23T12:00:00Z"},
        "assets": {
            file_name: {
                "type": "application/grib",
                "href": f"https://rgw.cscs.ch/bucket/{file_name}?{query}",
                "roles": ["data"],
            }
        },
    }


def _real_fixture_handler(features: list[dict[str, object]]) -> object:
    """MockTransport handler serving `_make_page(features)` for `/items`,
    and the ACTUAL fixture bytes for asset downloads matched by basename —
    so `_parse_grib_files` really opens the file via cfgrib, exercising the
    exact xarray semantics production hits (Plan 237's "measured" claims
    were reproduced this way, not through the byte-magic fakes the rest of
    this file uses elsewhere)."""

    # Guard here rather than in each test: every caller of this handler needs
    # the real GRIB fixtures, and without them the tests would fail with a 404
    # rather than skip (independent review, Plan 237).
    _skip_unless_real_fixtures()

    def handler(request: httpx.Request) -> httpx.Response:
        q = str(request.url)
        if "/items" in q:
            return httpx.Response(200, json=_make_page(features))
        file_name = request.url.path.rsplit("/", 1)[-1]
        fixture_path = _REAL_FIXTURE_DIR / file_name
        if fixture_path.exists():
            return httpx.Response(200, content=fixture_path.read_bytes())
        return httpx.Response(404)

    return handler


class TestExpectedValidTimes:
    def test_hourly_cadence_across_window(self) -> None:
        cycle = ensure_utc(datetime(2026, 4, 23, 12, 0, tzinfo=UTC))
        window_end = cycle + timedelta(hours=3)
        times = _expected_valid_times(cycle, window_end)
        assert len(times) == 4
        assert times[0] == np.datetime64(datetime(2026, 4, 23, 12, 0), "ns")
        assert times[-1] == np.datetime64(datetime(2026, 4, 23, 15, 0), "ns")

    def test_full_horizon_derives_121_not_hardcoded(self) -> None:
        # The empirical 121-step / 484-file figure must be a CONSEQUENCE of
        # the window, not a literal baked into the function.
        cycle = ensure_utc(datetime(2026, 4, 23, 12, 0, tzinfo=UTC))
        window_end = cycle + timedelta(hours=120)
        assert len(_expected_valid_times(cycle, window_end)) == 121


class TestAssertCycleComplete:
    """Direct unit tests of the completeness predicate against SYNTHETIC
    per-file datasets (`_per_file_ds` + `_combine_cfgrib_datasets` +
    `convert_raw_dataset`) — no cfgrib/real GRIB needed to exercise the
    NaN-grid logic itself; the real-fixture tests below drive it end to
    end through `_parse_grib_files`."""

    @staticmethod
    def _dataset(entries: list[tuple[int, int, str]]) -> xr.Dataset:
        # entries: (member, step_hours, var)
        per_file = [_per_file_ds(member=m, step_hours=s, var=v) for m, s, v in entries]
        combined = _combine_cfgrib_datasets(per_file)
        return convert_raw_dataset(combined)

    def test_complete_grid_does_not_raise(self) -> None:
        entries = [(m, s, "tp") for m in range(2) for s in (0, 3)]
        ds = self._dataset(entries)
        expected = [
            np.datetime64(datetime(2026, 4, 23, 0, 0), "ns"),
            np.datetime64(datetime(2026, 4, 23, 0, 0), "ns") + np.timedelta64(3, "h"),
        ]
        _assert_cycle_complete(ds, expected, expected_members=range(2))

    def test_missing_step_names_it(self) -> None:
        entries = [(m, 0, "tp") for m in range(2)]
        ds = self._dataset(entries)
        expected = [
            np.datetime64(datetime(2026, 4, 23, 0, 0), "ns"),
            np.datetime64(datetime(2026, 4, 23, 0, 0), "ns") + np.timedelta64(3, "h"),
        ]
        with pytest.raises(AdapterError, match="missing valid_time step"):
            _assert_cycle_complete(ds, expected, expected_members=range(2))

    def test_missing_member_at_a_present_step_names_it(self) -> None:
        # Step 0 has both members; step 3 has ONLY member 0 — the join="outer"
        # NaN-fill case (i) alone would miss (member 1 wholly NaN at step 3).
        entries = [(0, 0, "tp"), (1, 0, "tp"), (0, 3, "tp")]
        ds = self._dataset(entries)
        expected = [
            np.datetime64(datetime(2026, 4, 23, 0, 0), "ns"),
            np.datetime64(datetime(2026, 4, 23, 0, 0), "ns") + np.timedelta64(3, "h"),
        ]
        with pytest.raises(AdapterError, match="wholly missing"):
            _assert_cycle_complete(ds, expected, expected_members=range(2))

    def test_member_absent_from_every_step_names_it(self) -> None:
        # Independent review (Plan 237): a member absent from EVERY file at EVERY
        # step never enters the `member` coordinate, so the NaN scan — which
        # iterates OBSERVED members — could never see it. Only member 0 here;
        # member 1 is expected but wholly absent from the coordinate.
        entries = [(0, 0, "tp"), (0, 3, "tp")]
        ds = self._dataset(entries)
        expected = [
            np.datetime64(datetime(2026, 4, 23, 0, 0), "ns"),
            np.datetime64(datetime(2026, 4, 23, 0, 0), "ns") + np.timedelta64(3, "h"),
        ]
        with pytest.raises(AdapterError, match="ensemble member.* absent"):
            _assert_cycle_complete(ds, expected, expected_members=range(2))

    def test_unexpected_extra_step_names_it(self) -> None:
        # (i) claims SET EQUALITY, so a superset is a contract breach too.
        entries = [(m, s, "tp") for m in range(2) for s in (0, 3)]
        ds = self._dataset(entries)
        expected = [np.datetime64(datetime(2026, 4, 23, 0, 0), "ns")]
        with pytest.raises(AdapterError, match="unexpected valid_time step"):
            _assert_cycle_complete(ds, expected, expected_members=range(2))


class TestFetchGribFilesDuplicateAssetDedup:
    """`_fetch_grib_files` de-dupes on (netloc, path) with the query
    stripped — the presigned signature must never defeat the dedup."""

    def test_duplicate_query_signature_is_deduped_and_warned(
        self, tmp_path: Path
    ) -> None:
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        first = _make_item("tot_prec", ref_dt="2026-04-19T12:00:00Z")
        # Same asset, RE-LISTED with a different presigned query — the
        # shape a STAC pagination re-list produces.
        second = _make_item("tot_prec", ref_dt="2026-04-19T12:00:00Z")
        second["id"] = f"relisted-duplicate-{first['id']}"
        first_href = next(iter(first["assets"].values()))["href"]  # type: ignore[index]
        assert "?" in first_href
        base_href = first_href.split("?", 1)[0]
        for asset in second["assets"].values():  # type: ignore[union-attr]
            asset["href"] = f"{base_href}?AWSAccessKeyId=y2&Signature=z2&Expires=1"

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "/items" in q:
                return httpx.Response(200, json=_make_page([first, second]))
            return httpx.Response(200, content=b"GRIB" + b"\x00" * 50)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(cycle)

        assert len(files) == 1
        warnings = [
            e for e in captured if e.get("event") == "nwp.duplicate_assets_dropped"
        ]
        assert len(warnings) == 1
        assert warnings[0]["dropped_count"] == 1
        assert warnings[0]["log_level"] == "warning"

    def test_different_asset_sharing_a_basename_is_not_deduped(
        self, tmp_path: Path
    ) -> None:
        # Two genuinely DIFFERENT assets (different path) that happen to
        # share a basename must both be kept — dedup keys on the full path,
        # not the basename.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        item_a = _make_item("tot_prec", ref_dt="2026-04-19T12:00:00Z")
        item_b = _make_item("t_2m", ref_dt="2026-04-19T12:00:00Z")
        for asset in item_a["assets"].values():  # type: ignore[union-attr]
            asset["href"] = "https://rgw.cscs.ch/bucket/dir-a/shared-name.grib2?sig=1"
        for asset in item_b["assets"].values():  # type: ignore[union-attr]
            asset["href"] = "https://rgw.cscs.ch/bucket/dir-b/shared-name.grib2?sig=2"

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "/items" in q:
                return httpx.Response(200, json=_make_page([item_a, item_b]))
            return httpx.Response(200, content=b"GRIB" + b"\x00" * 50)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(cycle)

        assert len(files) == 2
        assert files[0] != files[1]
        assert files[0].name != files[1].name
        assert [
            e for e in captured if e.get("event") == "nwp.duplicate_assets_dropped"
        ] == []


class TestDownloadAssetCollisionProofDestination:
    """`_download_asset` part 2: two different URL paths sharing one
    basename must land in two distinct files, never overwrite one
    another — this is the belt-and-braces behind part 1's dedup."""

    def test_two_paths_sharing_a_basename_land_in_two_files(
        self, tmp_path: Path
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"GRIB" + str(request.url).encode())

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        dest_a = adapter._download_asset(
            "https://rgw.cscs.ch/bucket/dir-a/name.grib2?sig=1", "k", scratch
        )
        dest_b = adapter._download_asset(
            "https://rgw.cscs.ch/bucket/dir-b/name.grib2?sig=2", "k", scratch
        )

        assert dest_a != dest_b
        assert dest_a.exists() and dest_b.exists()
        assert dest_a.name.endswith(".grib2")
        assert dest_b.name.endswith(".grib2")
        assert dest_a.read_bytes() != dest_b.read_bytes()


class TestParseGribFilesRealDuplicateAndCompleteness:
    """T1 verification (a)/(b1)/(b2), end to end through
    `_fetch_grib_files` + `_parse_grib_files`, against REAL ICON-CH2-EPS
    fixtures (see `tests/fixtures/meteoswiss_nwp/README.md`). These tests
    must be RED against the pre-change code:

    (a) is RED because `_fetch_grib_files` appends every asset with no
        de-duplication, so a duplicated step-0 file is opened twice by
        cfgrib and `_combine_cfgrib_datasets`'s final valid_time concat
        (join="outer") tries to reindex a `number` axis that itself
        contains duplicate values — the EXACT production error
        (`cannot reindex or align along dimension 'number' ... duplicate
        values`) — for BOTH `PARAM_GROUPS`, so `_parse_grib_files` ends up
        with zero parseable groups and raises `AdapterError`.
    (b1)/(b2) are RED because there is no completeness check anywhere
        pre-change; a short cycle parses "successfully" with the missing
        steps/members silently NaN-filled instead of raising.
    """

    def test_duplicate_across_both_param_groups_deduped_and_parses(
        self, tmp_path: Path
    ) -> None:
        features = [
            _real_fixture_item("tot_prec", 0, "ctrl", query="sig=1"),
            _real_fixture_item("tot_prec", 0, "ctrl", query="sig=2"),  # duplicate
            _real_fixture_item("tot_prec", 0, "perturb", query="sig=1"),
            _real_fixture_item("t_2m", 0, "ctrl", query="sig=1"),
            _real_fixture_item("t_2m", 0, "ctrl", query="sig=2"),  # duplicate
            _real_fixture_item("t_2m", 0, "perturb", query="sig=1"),
            # A non-duplicated valid_time — required so the final
            # cross-time concat (where the fault actually manifests) runs.
            _real_fixture_item("tot_prec", 1, "ctrl", query="sig=1"),
            _real_fixture_item("t_2m", 1, "ctrl", query="sig=1"),
        ]
        adapter = _make_adapter(
            httpx.MockTransport(_real_fixture_handler(features)),  # type: ignore[arg-type]
            tmp_path,
        )

        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(_REAL_CYCLE)

        assert len(files) == len(set(files)) == 6
        warnings = [
            e for e in captured if e.get("event") == "nwp.duplicate_assets_dropped"
        ]
        assert len(warnings) == 1
        assert warnings[0]["dropped_count"] == 2

        # No completeness window threaded here — this test isolates dedup;
        # (b1)/(b2) below isolate the completeness assertion.
        ds = adapter._parse_grib_files(files)
        assert "precipitation" in ds.data_vars
        assert "temperature" in ds.data_vars
        assert ds.sizes["valid_time"] == 2

    def test_missing_entire_step_names_it(self, tmp_path: Path) -> None:
        adapter = _make_adapter(
            httpx.MockTransport(lambda _req: httpx.Response(404)),  # type: ignore[arg-type]
            tmp_path,
        )
        files = [
            _real_fixture_path("tot_prec", 0, "ctrl"),
            _real_fixture_path("tot_prec", 0, "perturb"),
            _real_fixture_path("t_2m", 0, "ctrl"),
            _real_fixture_path("t_2m", 0, "perturb"),
        ]
        with pytest.raises(AdapterError, match="missing valid_time step"):
            adapter._parse_grib_files(
                files,
                cycle_time=_REAL_CYCLE,
                window_end=_REAL_CYCLE + timedelta(hours=1),
            )

    def test_missing_perturb_only_at_a_present_step_names_it(
        self, tmp_path: Path
    ) -> None:
        # Step 0 is a full 21-member cycle; step 1 has ONLY the ctrl file
        # (no perturb) — exactly the production shape (09-02T18/09-03T18:
        # duplicates/gaps were perturb-only). Check (i) alone would pass
        # (step 1 IS present, contributed by ctrl); check (ii) must catch
        # the wholly-NaN member 1..20 slots that join="outer" produced.
        adapter = _make_adapter(
            httpx.MockTransport(lambda _req: httpx.Response(404)),  # type: ignore[arg-type]
            tmp_path,
        )
        files = [
            _real_fixture_path("tot_prec", 0, "ctrl"),
            _real_fixture_path("tot_prec", 0, "perturb"),
            _real_fixture_path("t_2m", 0, "ctrl"),
            _real_fixture_path("t_2m", 0, "perturb"),
            _real_fixture_path("tot_prec", 1, "ctrl"),
            _real_fixture_path("t_2m", 1, "ctrl"),
        ]
        with pytest.raises(AdapterError, match="wholly missing"):
            adapter._parse_grib_files(
                files,
                cycle_time=_REAL_CYCLE,
                window_end=_REAL_CYCLE + timedelta(hours=1),
            )

    def test_complete_cycle_with_window_threaded_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        # Control: step 0 alone IS complete (both variants, 21 members) —
        # the completeness assertion must not fire on a genuinely full
        # window.
        adapter = _make_adapter(
            httpx.MockTransport(lambda _req: httpx.Response(404)),  # type: ignore[arg-type]
            tmp_path,
        )
        files = [
            _real_fixture_path("tot_prec", 0, "ctrl"),
            _real_fixture_path("tot_prec", 0, "perturb"),
            _real_fixture_path("t_2m", 0, "ctrl"),
            _real_fixture_path("t_2m", 0, "perturb"),
        ]
        ds = adapter._parse_grib_files(
            files, cycle_time=_REAL_CYCLE, window_end=_REAL_CYCLE
        )
        assert ds.sizes["member"] == 21


class TestParseGribFilesCompletenessSkippedWithMaxFiles:
    """The `max_files` scope-limiter has no temporal boundary (Plan 237
    T1) — the completeness assertion must be skipped, and say why, rather
    than misfire on a deliberately-truncated smoke-test fetch."""

    def test_max_files_set_skips_completeness_and_logs_reason(
        self, tmp_path: Path
    ) -> None:
        client = httpx.Client(
            transport=httpx.MockTransport(lambda _req: httpx.Response(404)),
            base_url="https://dummy",
        )
        adapter = MeteoSwissNwpAdapter(
            stac_base_url=_STAC_BASE,
            stac_collection=_STAC_COLLECTION,
            scratch_path=tmp_path,
            http_client=client,
            disk_guard_enabled=False,
            max_files=2,
        )
        # 21 members present (clears `_MIN_ENSEMBLE_MEMBERS`) but only ONE
        # of the 121 expected steps — would fail completeness check (i) if
        # it ran.
        files = [
            _real_fixture_path("tot_prec", 0, "ctrl"),
            _real_fixture_path("tot_prec", 0, "perturb"),
            _real_fixture_path("t_2m", 0, "ctrl"),
            _real_fixture_path("t_2m", 0, "perturb"),
        ]
        with structlog.testing.capture_logs() as captured:
            ds = adapter._parse_grib_files(
                files,
                cycle_time=_REAL_CYCLE,
                window_end=_REAL_CYCLE + timedelta(hours=120),
            )
        assert ds is not None
        skipped = [
            e for e in captured if e.get("event") == "nwp.completeness_check_skipped"
        ]
        assert len(skipped) == 1
        assert skipped[0]["max_files"] == 2
