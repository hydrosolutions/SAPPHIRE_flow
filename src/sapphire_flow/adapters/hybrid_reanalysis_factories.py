"""Hybrid forcing-resolver factories (Plan 072 T3; Plan 115b4 §5B retires the
CAMELS-CH tier).

``default_hybrid_forcing_source`` wires the per-parameter MeteoSwiss priority
chain over a ``HistoricalForcingStore``. ``select_reanalysis_source`` is the
read-side selector used by the hindcast and forecast-cycle flows to honour
``DeploymentConfig.reanalysis_source``.

No CAMELS-CH tier (Plan 115b4 §5B): Plan 072's ``... -> CAMELS_CH`` chains are
retired — CAMELS-CH is now a validation reference + audit trail
(Plan 115b3), not a live weather-forcing tier. ``historical_forcing`` rows
tagged ``camels-ch`` are untouched and remain readable by a direct
source-keyed fetch (``PerSourceStoreReader``/``fetch_forcing``); they are
simply never wired into this hybrid chain.

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

# Per-parameter v0b priority chains (Plan 115b4 §5B): MeteoSwiss self-derived
# products only — NO CAMELS_CH tier. Precipitation prefers the definitive
# archive-backed RhiresD, falling back to the preliminary live-tail RprelimD
# for days RhiresD has not yet published; every other parameter is a single-
# source chain (each has exactly one MeteoSwiss product, no fallback).
#
# Plan 146 D4: adds the antecedent snow tier — one single-source chain per
# snow parameter (swe/snow_depth/snowmelt), each backed by
# ForcingSource.RECAP_SNOW_REANALYSIS (the only provenance a stored snow row
# ever carries, Plan 146 D3). No fallback tier exists for snow yet.
_PRIORITY_CHAINS: dict[str, tuple[ForcingSource, ...]] = {
    "precipitation": (
        ForcingSource.METEOSWISS_RHIRESD,
        ForcingSource.METEOSWISS_RPRELIMD,
    ),
    "temperature": (ForcingSource.METEOSWISS_TABSD,),
    "temperature_min": (ForcingSource.METEOSWISS_TMIND,),
    "temperature_max": (ForcingSource.METEOSWISS_TMAXD,),
    "relative_sunshine_duration": (ForcingSource.METEOSWISS_SRELD,),
    "swe": (ForcingSource.RECAP_SNOW_REANALYSIS,),
    "snow_depth": (ForcingSource.RECAP_SNOW_REANALYSIS,),
    "snowmelt": (ForcingSource.RECAP_SNOW_REANALYSIS,),
}

# Public: the canonical parameter set the hybrid chain resolves. Also used by
# the §6D dashboard forcing endpoint (api/routes/stations.py) to request the
# full hybrid-resolved series, not just this factory's own default scope.
#
# Plan 146 D4: adding the three snow params here is REQUIRED (not merely
# adding them to _PRIORITY_CHAINS above) — `HybridForcingSource._sources` is
# derived ONCE at construction from `parameters_in_scope`, and every
# construction-time caller (training/hindcast/live + six more, see D4's full
# caller audit) passes no `parameters_in_scope` override, so it always falls
# back to this default. Without this, the snow `PerSourceStoreReader` is
# never constructed and a stored snow series is never selected.
DEFAULT_PARAMETERS: tuple[str, ...] = (
    "precipitation",
    "temperature",
    "temperature_min",
    "temperature_max",
    "relative_sunshine_duration",
    "swe",
    "snow_depth",
    "snowmelt",
)


def default_hybrid_forcing_source(
    *,
    forcing_store: HistoricalForcingStore,
    parameters_in_scope: tuple[str, ...] = DEFAULT_PARAMETERS,
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
