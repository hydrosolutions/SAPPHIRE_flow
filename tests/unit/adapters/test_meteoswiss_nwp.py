from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import numpy as np
import pytest
import xarray as xr

from sapphire_flow.adapters.meteoswiss_nwp import (
    MeteoSwissNwpAdapter,
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
        ds = xr.Dataset(
            {
                "t_2m": xr.DataArray(
                    np.full((3, 5, 2, 2), 293.15, dtype=np.float32),
                    dims=["member", "valid_time", "latitude", "longitude"],
                )
            }
        )
        result = _convert_units(ds)
        np.testing.assert_allclose(result["temperature"].values, 20.0, atol=0.01)
        assert "t_2m" not in result

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
                "t_2m": xr.DataArray(
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
                "t_2m": xr.DataArray(
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
    client = httpx.Client(transport=transport, base_url="https://dummy")
    return MeteoSwissNwpAdapter(
        stac_base_url=_STAC_BASE,
        stac_collection=_STAC_COLLECTION,
        scratch_path=tmp_path,
        http_client=client,
    )


def _cycle_features(cycle: UtcDatetime) -> list[dict[str, object]]:
    prefix = cycle.strftime("%m%d%Y-%H%M-0-")
    return [
        {"id": f"{prefix}tot_prec-ctrl-abc123", "properties": {}},
        {"id": f"{prefix}t_2m-ctrl-def456", "properties": {}},
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
        prior = ensure_utc(datetime(2026, 4, 19, 12, 0, tzinfo=UTC))

        def handler(request: httpx.Request) -> httpx.Response:
            q = str(request.url)
            if "datetime=2026-04-19T15:00:00Z" in q:
                return httpx.Response(200, json={"features": []})
            if "datetime=2026-04-19T12:00:00Z" in q:
                return httpx.Response(200, json={"features": _cycle_features(prior)})
            return httpx.Response(200, json={"features": []})

        adapter = _make_adapter(httpx.MockTransport(handler), tmp_path)
        now = ensure_utc(datetime(2026, 4, 19, 15, 30, tzinfo=UTC))
        assert adapter.resolve_cycle_time(now) == prior

    def test_raises_after_three_fallbacks(self, tmp_path: Path) -> None:
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


def _make_page(
    features: list[dict[str, object]], next_url: str | None = None
) -> dict[str, object]:
    links: list[dict[str, object]] = []
    if next_url is not None:
        links.append({"rel": "next", "href": next_url})
    return {"features": features, "links": links}


def _make_item(
    stac_token: str, step: int = 0, size: int | None = None
) -> dict[str, object]:
    item_id = f"04192026-1200-{step}-{stac_token}-ctrl-abcd1234"
    asset_key = f"icon-ch2-eps-202604191200-{step}-{stac_token}-ctrl.grib2"
    asset: dict[str, object] = {
        "type": "application/grib",
        "href": f"https://rgw.cscs.ch/bucket/{asset_key}?AWSAccessKeyId=x&Signature=y&Expires=9999999999",
        "roles": ["data"],
    }
    if size is not None:
        asset["size"] = size
    return {"id": item_id, "properties": {}, "assets": {asset_key: asset}}


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
