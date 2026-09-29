from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.types.enums import WeatherSourceRole
from sapphire_flow.types.historical_forcing import RawHistoricalForcing

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.protocols.stores import HistoricalForcingStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.station import StationWeatherSource


class StoreBackedReanalysisSource:
    """WeatherReanalysisSource that reads from HistoricalForcingStore.

    Used when historical forcing data has already been imported (e.g. via
    CAMELS-CH onboarding) and no external API call is needed.
    Optional source_mapping translates binding names into persisted source tags;
    unmapped names retain their existing behavior.
    """

    def __init__(
        self,
        forcing_store: HistoricalForcingStore,
        *,
        source_mapping: Mapping[str, str] | None = None,
    ) -> None:
        self._store = forcing_store
        self._source_mapping = dict(source_mapping or {})

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        results: list[RawHistoricalForcing] = []
        for cfg in station_configs:
            if cfg.role is not WeatherSourceRole.REANALYSIS:
                continue
            records = self._store.fetch_forcing(
                station_id=cfg.station_id,
                source=self._source_mapping.get(cfg.nwp_source, cfg.nwp_source),
                start=start,
                end=end,
                parameters=parameters,
            )
            results.extend(
                RawHistoricalForcing(
                    station_id=r.station_id,
                    source=r.source,
                    version=r.version,
                    valid_time=r.valid_time,
                    parameter=r.parameter,
                    spatial_type=r.spatial_type,
                    band_id=r.band_id,
                    member_id=r.member_id,
                    value=r.value,
                )
                for r in records
            )
        return results
