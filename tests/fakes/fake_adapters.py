from __future__ import annotations

import xarray as xr  # noqa: TC002

from sapphire_flow.types.alert import Alert  # noqa: TC001
from sapphire_flow.types.basin import Basin  # noqa: TC001
from sapphire_flow.types.datetime import UtcDatetime  # noqa: TC001
from sapphire_flow.types.enums import NotificationChannel  # noqa: TC001
from sapphire_flow.types.forecast import ForeignForecast  # noqa: TC001
from sapphire_flow.types.historical_forcing import RawHistoricalForcing  # noqa: TC001
from sapphire_flow.types.ids import StationId  # noqa: TC001
from sapphire_flow.types.observation import RawObservation  # noqa: TC001
from sapphire_flow.types.pipeline import FlowRunStatus  # noqa: TC001
from sapphire_flow.types.station import (  # noqa: TC001
    StationConfig,
    StationWeatherSource,
)
from sapphire_flow.types.weather import (  # noqa: TC001
    BasinAverageForecast,
    ElevationBandForecast,
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


class FakeNotificationAdapter:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send(
        self,
        channel: NotificationChannel,
        recipients: list[str],
        subject: str,
        body: str,
        alert: Alert | None = None,
    ) -> None:
        self.sent.append(
            {
                "channel": channel,
                "recipients": recipients,
                "subject": subject,
                "body": body,
                "alert": alert,
            }
        )


class FakeWeatherReanalysisSource:
    def __init__(self, records: list[RawHistoricalForcing] | None = None) -> None:
        self._records = records or []
        self.fetch_reanalysis_call_count: int = 0

    def records(self) -> list[RawHistoricalForcing]:
        return list(self._records)

    def set_records(self, records: list[RawHistoricalForcing]) -> None:
        self._records = list(records)

    def extend_records(self, records: list[RawHistoricalForcing]) -> None:
        self._records.extend(records)

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        self.fetch_reanalysis_call_count += 1
        station_ids = {cfg.station_id for cfg in station_configs}
        return [
            r
            for r in self._records
            if r.station_id in station_ids
            and start <= r.valid_time < end
            and r.parameter in parameters
        ]


class FakeGridExtractor:
    def __init__(
        self,
        *,
        result: dict[StationId, BasinAverageForecast | ElevationBandForecast]
        | None = None,
        exception: Exception | None = None,
    ) -> None:
        self._result = result or {}
        self._exception = exception
        self.call_count: int = 0
        self.last_configs: list[StationWeatherSource] = []

    def extract(
        self,
        grid: xr.Dataset,
        configs: list[StationWeatherSource],
        basins: dict[StationId, Basin],
        cycle_time: UtcDatetime,
        nwp_source: str,
    ) -> dict[StationId, BasinAverageForecast | ElevationBandForecast]:
        if self._exception is not None:
            raise self._exception
        self.call_count += 1
        self.last_configs = list(configs)
        return self._result
