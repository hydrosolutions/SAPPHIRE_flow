"""Hybrid forcing-resolver factories (Plan 072 T3).

``default_hybrid_forcing_source`` wires the v0b priority chain (MeteoSwiss
per-parameter source -> CAMELS-CH) over a ``HistoricalForcingStore``.
``select_reanalysis_source`` is the read-side selector used by the hindcast and
forecast-cycle flows to honour ``DeploymentConfig.reanalysis_source``.

No NWP-archive tier: ``ForcingSource.NWP_ARCHIVE`` is reserved-but-unused in
v0b (rev-3 dropped it); it is never wired here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sapphire_flow.adapters.hybrid_reanalysis import HybridForcingSource
from sapphire_flow.adapters.per_source_store_reader import PerSourceStoreReader
from sapphire_flow.types.forcing_sources import ForcingSource

if TYPE_CHECKING:
    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.protocols.stores import HistoricalForcingStore

# Per-parameter v0b priority chains (Plan 072 §Priority chains): the MeteoSwiss
# open-data source first, then CAMELS-CH as the pre-2020 fallback.
_PRIORITY_CHAINS: dict[str, tuple[ForcingSource, ...]] = {
    "precipitation": (ForcingSource.METEOSWISS_RPRELIMD, ForcingSource.CAMELS_CH),
    "temperature": (ForcingSource.METEOSWISS_TABSD, ForcingSource.CAMELS_CH),
    "temperature_min": (ForcingSource.METEOSWISS_TMIND, ForcingSource.CAMELS_CH),
    "temperature_max": (ForcingSource.METEOSWISS_TMAXD, ForcingSource.CAMELS_CH),
}

_DEFAULT_PARAMETERS: tuple[str, ...] = (
    "precipitation",
    "temperature",
    "temperature_min",
    "temperature_max",
)


def default_hybrid_forcing_source(
    *,
    forcing_store: HistoricalForcingStore,
    parameters_in_scope: tuple[str, ...] = _DEFAULT_PARAMETERS,
) -> HybridForcingSource:
    priority = {
        parameter: _PRIORITY_CHAINS[parameter]
        for parameter in parameters_in_scope
        if parameter in _PRIORITY_CHAINS
    }
    wired_sources = {tier for chain in priority.values() for tier in chain}
    sources: dict[ForcingSource, WeatherReanalysisSource] = {
        source: PerSourceStoreReader(forcing_store=forcing_store, source=source)
        for source in wired_sources
    }
    return HybridForcingSource(sources=sources, priority=priority)


def select_reanalysis_source(
    *,
    forcing_store: HistoricalForcingStore,
    mode: Literal["single", "hybrid"],
) -> WeatherReanalysisSource:
    """Pick the read-side reanalysis source for the configured ``mode``."""
    if mode == "hybrid":
        return default_hybrid_forcing_source(forcing_store=forcing_store)
    from sapphire_flow.adapters.store_backed_reanalysis import (
        StoreBackedReanalysisSource,
    )

    return StoreBackedReanalysisSource(forcing_store)
