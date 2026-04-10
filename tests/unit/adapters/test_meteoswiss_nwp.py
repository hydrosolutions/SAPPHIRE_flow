from __future__ import annotations

import numpy as np
import xarray as xr

from sapphire_flow.adapters.meteoswiss_nwp import (
    MeteoSwissNwpAdapter,
    _compute_wind_speed,
    _convert_units,
    _deaccumulate_precipitation,
    convert_raw_dataset,
)
from sapphire_flow.protocols.adapters import WeatherForecastSource


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
