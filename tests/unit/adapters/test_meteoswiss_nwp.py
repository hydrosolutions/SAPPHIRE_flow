from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import numpy as np
import pytest
import structlog
import xarray as xr

from sapphire_flow.adapters.meteoswiss_nwp import (
    MeteoSwissNwpAdapter,
    _combine_cfgrib_datasets,
    _compute_wind_speed,
    _convert_units,
    _deaccumulate_precipitation,
    convert_raw_dataset,
)
from sapphire_flow.exceptions import NoCycleAvailableError
from sapphire_flow.protocols.adapters import WeatherForecastSource
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc

if TYPE_CHECKING:
    from pathlib import Path


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
    incompletely uploaded (MeteoSwiss publishes ICON-CH2-EPS lead-times
    incrementally over ~90-120 min). The adapter must skip it and walk back to
    the next older, adequately-aged slot even when the fresh cycle IS already
    (partially) published — preferring a complete older cycle over a truncated
    newer one.
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

        with pytest.raises(BudgetExceededError, match="Download size cap"):
            adapter._fetch_grib_files(cycle)

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
    total_pages: int, final_item: dict[str, object] | None
) -> object:
    """Serve exactly `total_pages` pages via rel=next, item only on the last.

    Unlike `_paged_handler` (which slices a feature list by offset), this
    tracks call count in closure state so tests can cheaply simulate a
    walk hundreds/thousands of pages deep without materializing that many
    STAC items. The GRIB asset itself is served on every request whose URL
    contains ``.grib2``.
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
        features = [final_item] if (is_last and final_item is not None) else []
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
        handler = _make_n_page_handler(850, item)

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
        handler = _make_n_page_handler(1200, item)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(cycle)

        assert len(files) == 1
        warnings = [e for e in captured if e.get("event") == "nwp.pagination_near_cap"]
        assert len(warnings) == 1
        assert warnings[0]["page_count"] == 1200

    def test_no_near_cap_warning_below_threshold(self, tmp_path: Path) -> None:
        # 850 pages is below the 1200-page (80%) warning threshold — no
        # WARNING should fire, only the routine completion INFO log.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        item = _make_item("tot_prec", ref_dt="2026-04-19T12:00:00Z")
        handler = _make_n_page_handler(850, item)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as captured:
            adapter._fetch_grib_files(cycle)

        warnings = [e for e in captured if e.get("event") == "nwp.pagination_near_cap"]
        assert warnings == []

    def test_fetch_completed_log_includes_page_and_item_counts(
        self, tmp_path: Path
    ) -> None:
        # Observability: a successful fetch logs the actual page count and
        # matched target-cycle item count so the next breach shows up as a
        # trend, not a silent outage.
        cycle = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))
        item = _make_item("tot_prec", ref_dt="2026-04-19T12:00:00Z")
        handler = _make_n_page_handler(5, item)

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as captured:
            files = adapter._fetch_grib_files(cycle)

        assert len(files) == 1
        completed = [e for e in captured if e.get("event") == "nwp.fetch_completed"]
        assert len(completed) == 1
        assert completed[0]["page_count"] == 5
        assert completed[0]["matched_ref_dt_count"] == 1
        assert completed[0]["files_fetched"] == 1


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

        # All 100 items are allowlisted (tp) and below _MAX_FILE_COUNT=500.
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
