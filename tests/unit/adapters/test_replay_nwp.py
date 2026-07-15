from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
import xarray as xr

from sapphire_flow.adapters.replay.nwp import ReplayNwpAdapter
from sapphire_flow.exceptions import AdapterError, ConfigurationError
from sapphire_flow.protocols.adapters import WeatherForecastSource
from sapphire_flow.store.zarr_nwp_grid_store import ZarrNwpGridStore
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.enums import (
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.weather import GriddedForecast


def _make_config(nwp_source: str = "icon_ch2_eps") -> StationWeatherSource:
    return StationWeatherSource(
        station_id=StationId(uuid.uuid4()),
        nwp_source=nwp_source,
        extraction_type=SpatialRepresentation.BASIN_AVERAGE,
        status=WeatherSourceStatus.ACTIVE,
        role=WeatherSourceRole.FORECAST,
    )


def _make_and_archive_forecast(
    base_path: object, cycle_time: UtcDatetime
) -> GriddedForecast:
    ds = xr.Dataset(
        {
            "precipitation": xr.DataArray(
                np.random.rand(3, 5, 4, 4).astype(np.float32),
                dims=["member", "valid_time", "latitude", "longitude"],
            ),
        }
    )
    forecast = GriddedForecast(
        nwp_source="icon_ch2_eps", cycle_time=cycle_time, values=ds
    )
    store = ZarrNwpGridStore()
    store.archive(forecast, base_path)  # type: ignore[arg-type]
    return forecast


class TestReplayNwpAdapter:
    def test_round_trip(self, tmp_path: object) -> None:
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        original = _make_and_archive_forecast(tmp_path, ct)

        adapter = ReplayNwpAdapter(fixture_dir=tmp_path, grid_store=ZarrNwpGridStore())  # type: ignore[arg-type]
        result = adapter.fetch_forecasts([_make_config()], ct)

        assert isinstance(result, GriddedForecast)
        assert result.nwp_source == "icon_ch2_eps"
        xr.testing.assert_equal(result.values, original.values)

    def test_missing_fixture_raises_adapter_error(self, tmp_path: object) -> None:
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        adapter = ReplayNwpAdapter(fixture_dir=tmp_path, grid_store=ZarrNwpGridStore())  # type: ignore[arg-type]

        with pytest.raises(AdapterError, match="Failed to load"):
            adapter.fetch_forecasts([_make_config()], ct)

    def test_empty_station_configs_raises_adapter_error(self, tmp_path: object) -> None:
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        adapter = ReplayNwpAdapter(fixture_dir=tmp_path, grid_store=ZarrNwpGridStore())  # type: ignore[arg-type]

        with pytest.raises(AdapterError, match="empty"):
            adapter.fetch_forecasts([], ct)

    def test_missing_fixture_dir_raises_configuration_error(
        self, tmp_path: object
    ) -> None:
        from pathlib import Path

        nonexistent = Path(str(tmp_path)) / "nonexistent"
        with pytest.raises(ConfigurationError, match="not found"):
            ReplayNwpAdapter(fixture_dir=nonexistent, grid_store=ZarrNwpGridStore())

    def test_mixed_nwp_sources_raises_adapter_error(self, tmp_path: object) -> None:
        ct = ensure_utc(datetime(2026, 4, 1, 6, tzinfo=UTC))
        adapter = ReplayNwpAdapter(fixture_dir=tmp_path, grid_store=ZarrNwpGridStore())  # type: ignore[arg-type]

        configs = [_make_config("icon_ch2_eps"), _make_config("other_source")]
        with pytest.raises(AdapterError, match="same nwp_source"):
            adapter.fetch_forecasts(configs, ct)

    def test_protocol_conformance(self, tmp_path: object) -> None:
        adapter = ReplayNwpAdapter(fixture_dir=tmp_path, grid_store=ZarrNwpGridStore())  # type: ignore[arg-type]
        assert isinstance(adapter, WeatherForecastSource)
