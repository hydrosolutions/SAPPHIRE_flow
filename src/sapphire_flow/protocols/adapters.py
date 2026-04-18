from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast import ForeignForecast
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import RawObservation
    from sapphire_flow.types.pipeline import FlowRunStatus
    from sapphire_flow.types.station import StationConfig, StationWeatherSource
    from sapphire_flow.types.weather import GriddedForecast, WeatherForecastResult


@runtime_checkable
class WeatherForecastSource(Protocol):
    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> GriddedForecast | dict[StationId, WeatherForecastResult]:
        """Fetch weather forecasts for the given stations and NWP cycle.

        Return type depends on the implementation path:
        - Gridded-NWP sources (ICON-CH2-EPS, ECMWF IFS) return ``GriddedForecast``.
          The flow layer passes this to ``GridExtractor.extract()`` for bulk extraction.
        - Per-station / pre-extracted sources (Data Gateway, point stations) return
          ``dict[StationId, WeatherForecastResult]``, already station-keyed.

        Callers discriminate via ``isinstance(result, GriddedForecast)``.
        """
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
    ) -> list[RawHistoricalForcing]:
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
