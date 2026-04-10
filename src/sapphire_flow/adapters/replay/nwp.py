from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.exceptions import AdapterError, ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.protocols.stores import NwpGridStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationWeatherSource
    from sapphire_flow.types.weather import (
        GriddedForecast,
        WeatherForecastResult,
    )

log = structlog.get_logger(__name__)


class ReplayNwpAdapter:
    def __init__(self, fixture_dir: Path, grid_store: NwpGridStore) -> None:
        if not fixture_dir.exists():
            raise ConfigurationError(f"NWP fixture directory not found: {fixture_dir}")
        self._fixture_dir = fixture_dir
        self._grid_store = grid_store

    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> GriddedForecast | dict[StationId, WeatherForecastResult]:
        if not station_configs:
            raise AdapterError("station_configs is empty")

        nwp_source = station_configs[0].nwp_source
        if any(cfg.nwp_source != nwp_source for cfg in station_configs[1:]):
            raise AdapterError("All station_configs must share the same nwp_source")

        t0 = time.perf_counter()
        try:
            forecast = self._grid_store.load(self._fixture_dir, nwp_source, cycle_time)
        except Exception as exc:
            raise AdapterError(
                f"Failed to load NWP fixture for {nwp_source} at {cycle_time}: {exc}"
            ) from exc

        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        log.debug(
            "nwp.fetch_completed",
            nwp_source=nwp_source,
            cycle_time=str(cycle_time),
            duration_ms=duration_ms,
        )
        return forecast
