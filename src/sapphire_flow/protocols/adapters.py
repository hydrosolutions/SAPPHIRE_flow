from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forcing_track import ForcingTrackKey, RawFetchOutcome
    from sapphire_flow.types.forecast import ForeignForecast
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation import HydroScraperBatchResult, RawObservation
    from sapphire_flow.types.pipeline import FlowRunStatus
    from sapphire_flow.types.station import StationConfig, StationWeatherSource
    from sapphire_flow.types.weather import (
        GriddedForecast,
        SnowForecastFetchResult,
        WeatherForecastResult,
    )


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
class CandidateAwareForecastSource(Protocol):
    """Plan 151 D6: a SEPARATE, narrow capability Protocol (widening
    ``WeatherForecastSource`` would make every legacy adapter/fake
    non-conforming) for a per-forcing-track, walk-back-aware fetch. The
    adapter factory's return type is the dispatch contract — a migrated
    adapter satisfies BOTH this and ``WeatherForecastSource``; the flow
    selects the per-track path via ``isinstance(source,
    CandidateAwareForecastSource)``. Walk-back POLICY is a pure
    ``services/`` concern (D7), never inside the adapter.
    """

    def fetch_requirement(
        self,
        track: ForcingTrackKey,
        stations: list[StationWeatherSource],
        nominal_cycle: UtcDatetime,
    ) -> RawFetchOutcome:
        """Fetch EXACTLY ``nominal_cycle`` — NO adapter-side walk-back.

        Returns the raw fetch outcome (transport classification only,
        D31-candidate-ownership) — never judges completeness, which needs
        the services-side ``ResolvedTrackRequest.fetch_horizons`` this
        method cannot see. Raises a typed transient transport error, or a
        typed auth/config error, rather than returning either as a status.
        """
        raise NotImplementedError

    def expected_member_ids(self, track: ForcingTrackKey) -> frozenset[int]:
        """The source's raw member identity for ``track`` — source-DERIVED
        (Ruling 1), never a literal. Exact-member-set completeness (D8) is
        checked against this."""
        raise NotImplementedError


@runtime_checkable
class SnowForecastSource(Protocol):
    """Narrow capability Protocol for the deterministic snow-forecast channel.

    Plan 145 D6: NOT part of ``WeatherForecastSource`` — a bare
    ``adapter.fetch_snow_forecast(...)`` call would fail pyright and could raise at
    runtime against MeteoSwiss/replay/an ordinary injected ``WeatherForecastSource``.
    Callers detect this capability via ``isinstance(adapter, SnowForecastSource)``
    (structural, not an ``isinstance(RecapGatewayForecastAdapter)`` import) so any
    replay/test double implementing the method is compatible. An adapter that does
    NOT satisfy this Protocol skips snow entirely — no scoping, no fetch, no
    ``snow_unavailable`` outcome — behaving exactly as it does today.
    """

    def fetch_snow_forecast(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
        required_snow: Mapping[StationId, frozenset[str]],
    ) -> SnowForecastFetchResult:
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
class BatchStationDataSource(Protocol):
    """Minor fix (Plan 175 round 2) — narrow capability Protocol for the
    per-station-outcome batch fetch `ingest_observations_flow` actually
    calls. NOT part of `StationDataSource` (touchpoint-maps.md: widening that
    Protocol was the explicit rejected alternative) — `fetch_observations_batch`
    stays an ADDITION any `StationDataSource` implementation may also offer.
    `HydroScraperAdapter` and `ReplayStationAdapter` both satisfy this; a
    `StationDataSource` that only implements the base Protocol does not, and
    should not be handed to `ingest_observations_flow` as its `adapter=`.
    """

    def fetch_observations_batch(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> HydroScraperBatchResult:
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
