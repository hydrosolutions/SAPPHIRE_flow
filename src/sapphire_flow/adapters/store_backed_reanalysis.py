from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.types.enums import WeatherSourceRole
from sapphire_flow.types.historical_forcing import RawHistoricalForcing

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import HistoricalForcingStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.station import StationWeatherSource


class StoreBackedReanalysisSource:
    """WeatherReanalysisSource that reads from HistoricalForcingStore.

    Used when historical forcing data has already been imported (e.g. via
    CAMELS-CH onboarding) and no external API call is needed.
    """

    def __init__(self, forcing_store: HistoricalForcingStore) -> None:
        self._store = forcing_store

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
                source=cfg.nwp_source,
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
