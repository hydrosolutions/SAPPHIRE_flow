from __future__ import annotations

from sapphire_flow.types.datetime import UtcDatetime  # noqa: TC001
from sapphire_flow.types.forecast import ForeignForecast  # noqa: TC001
from sapphire_flow.types.ids import StationId  # noqa: TC001
from sapphire_flow.types.observation import RawObservation  # noqa: TC001
from sapphire_flow.types.pipeline import FlowRunStatus  # noqa: TC001
from sapphire_flow.types.station import (  # noqa: TC001
    StationConfig,
    StationWeatherSource,
)
from sapphire_flow.types.weather import (  # noqa: TC001
    GriddedForecast,
    WeatherForecastResult,
)


class FakeWeatherForecastSource:
    def __init__(
        self,
        result: GriddedForecast | dict[StationId, WeatherForecastResult] | None = None,
    ) -> None:
        self._result: GriddedForecast | dict[StationId, WeatherForecastResult] = (
            result or {}  # type: ignore[assignment]
        )

    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> GriddedForecast | dict[StationId, WeatherForecastResult]:
        return self._result


class FakeStationDataSource:
    def __init__(self, observations: list[RawObservation] | None = None) -> None:
        self._observations = observations or []

    def fetch_observations(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> list[RawObservation]:
        return self._observations


class FakeForeignForecastSource:
    def __init__(self, forecasts: list[ForeignForecast] | None = None) -> None:
        self._forecasts = forecasts or []

    def fetch_published_forecasts(
        self,
        upstream_station_ids: list[str],
        since: UtcDatetime,
    ) -> list[ForeignForecast]:
        return [
            f
            for f in self._forecasts
            if f.upstream_station_id in upstream_station_ids and f.fetched_at >= since
        ]


class FakePipelineStatusSource:
    def __init__(self, runs: list[FlowRunStatus] | None = None) -> None:
        self._runs = runs or []

    def fetch_recent_runs(
        self,
        flow_names: list[str],
        since: UtcDatetime,
    ) -> list[FlowRunStatus]:
        return [r for r in self._runs if r.flow_name in flow_names]
