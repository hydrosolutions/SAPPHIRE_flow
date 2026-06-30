"""Hybrid forcing resolver (Plan 072 T2).

Chains per-source :class:`WeatherReanalysisSource` instances with PER-PARAMETER
priority and emits a unified, deduplicated row stream — AT MOST ONE winning row
per ``(station_id, valid_time, parameter)``, the winner being the
highest-priority source *for that parameter*. Coverage gaps invent no rows.

Resolution rule (Plan 072 D2): fan out to every source serially, collect rows
keyed on ``(station_id, valid_time, parameter)``, and for each key walk
``priority[parameter]`` in order, keeping the first row whose ``.source`` tag
equals a ``ForcingSource.value`` in that list. Serial execution is the only
safe default against ``PgHistoricalForcingStore``'s single ``sa.Connection``.
"""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sapphire_flow.protocols.adapters import WeatherReanalysisSource
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forcing_sources import ForcingSource
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.station import StationWeatherSource

log = structlog.get_logger(__name__)

_RowKey = tuple["StationId", "UtcDatetime", str]


class HybridForcingSource:
    """Per-parameter priority resolver over several reanalysis sources."""

    def __init__(
        self,
        *,
        sources: dict[ForcingSource, WeatherReanalysisSource],
        priority: Mapping[str, tuple[ForcingSource, ...]],
    ) -> None:
        self._sources = sources
        self._priority = priority

    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]:
        started = monotonic()

        # Fan out serially; index every row by logical key, then by its source
        # tag so the priority walk is an O(1) lookup per tier.
        collected: dict[_RowKey, dict[str, RawHistoricalForcing]] = {}
        for source in self._sources.values():
            for row in source.fetch_reanalysis(station_configs, start, end, parameters):
                key: _RowKey = (row.station_id, row.valid_time, row.parameter)
                collected.setdefault(key, {})[row.source] = row

        winners: list[RawHistoricalForcing] = []
        for key, by_source in collected.items():
            chain = self._priority.get(key[2], ())
            winner = next(
                (by_source[tier.value] for tier in chain if tier.value in by_source),
                None,
            )
            if winner is None:
                continue
            winners.append(winner)
            log.debug(
                "forcing.source_selected",
                station_id=key[0],
                valid_time=key[1],
                parameter=key[2],
                winning_source=winner.source,
                available_sources=sorted(by_source),
            )

        source_counts: dict[str, int] = {}
        for w in winners:
            source_counts[w.source] = source_counts.get(w.source, 0) + 1
        log.info(
            "forcing.resolution_completed",
            station_count=len(station_configs),
            row_count=len(winners),
            source_counts=source_counts,
            elapsed_ms=round((monotonic() - started) * 1000, 3),
        )
        return winners
