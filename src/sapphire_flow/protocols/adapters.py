from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast import ForeignForecast
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import RawObservation
    from sapphire_flow.types.pipeline import FlowRunStatus
    from sapphire_flow.types.station import StationConfig, StationWeatherSource
    from sapphire_flow.types.weather import (
        BasinAverageForecast,
        ElevationBandForecast,
        GriddedForecast,
        WeatherForecastResult,
    )


@runtime_checkable
class WeatherForecastSource(Protocol):
    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> GriddedForecast | dict[StationId, WeatherForecastResult]:
        raise NotImplementedError


@runtime_checkable
class StationDataSource(Protocol):
    def fetch_observations(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> list[RawObservation]:
        raise NotImplementedError


@runtime_checkable
class WeatherReanalysisSource(Protocol):
    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> dict[StationId, BasinAverageForecast | ElevationBandForecast]:
        raise NotImplementedError


@runtime_checkable
class ForeignForecastSource(Protocol):
    def fetch_published_forecasts(
        self,
        upstream_station_ids: list[str],
        since: UtcDatetime,
    ) -> list[ForeignForecast]:
        raise NotImplementedError


@runtime_checkable
class PipelineStatusSource(Protocol):
    def fetch_recent_runs(
        self,
        flow_names: list[str],
        since: UtcDatetime,
    ) -> list[FlowRunStatus]:
        raise NotImplementedError
