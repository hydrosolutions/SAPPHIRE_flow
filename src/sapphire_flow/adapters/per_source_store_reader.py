"""Per-source historical-forcing reader (Plan 072 T1).

A thin :class:`WeatherReanalysisSource` that reads rows for a SINGLE
``ForcingSource`` tag, fixed at construction time. Unlike
``StoreBackedReanalysisSource`` (which derives its source tag from
``station_config.nwp_source``), this reader uses only the ctor-fixed tag —
making it the right building block for ``HybridForcingSource``, which wires one
``PerSourceStoreReader`` per active source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.types.historical_forcing import RawHistoricalForcing

if TYPE_CHECKING:
    from sapphire_flow.protocols.stores import HistoricalForcingStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forcing_sources import ForcingSource
    from sapphire_flow.types.station import StationWeatherSource


class PerSourceStoreReader:
    """Reads ``RawHistoricalForcing`` rows for one fixed ``ForcingSource``."""

    def __init__(
        self, *, forcing_store: HistoricalForcingStore, source: ForcingSource
    ) -> None:
        self._store = forcing_store
        self._source = source

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        results: list[RawHistoricalForcing] = []
        # A station may carry several weather-source rows (e.g. an ICON source
        # alongside a historical one); the source tag is fixed here, so fetch
        # once per UNIQUE station_id (order-preserving) to avoid redundant reads
        # + duplicate rows.
        unique_station_ids = list(
            dict.fromkeys(cfg.station_id for cfg in station_configs)
        )
        for station_id in unique_station_ids:
            records = self._store.fetch_forcing(
                station_id=station_id,
                source=self._source.value,
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
