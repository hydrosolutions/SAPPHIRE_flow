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

Parameter-drop rule (Plan 115b4 §5A): a parameter with NO configured priority
chain is decided from the rows ACTUALLY RETURNED for that key, never a static
source map — zero sources returned rows means the key never reaches this
resolver at all (absent, matching pre-115b4 behaviour); exactly one source
returned rows means that source wins outright (no raise); two or more
distinct sources returned rows raises ``ConfigurationError`` — a
nondeterministic winner is a bug, not something to silently drop or pick
arbitrarily.
"""

from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

import structlog

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import WeatherSourceRole

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

        # Filter to REANALYSIS-role bindings before fanning out to child
        # sources — a FORECAST binding must never be asked for reanalysis rows.
        reanalysis_configs = [
            cfg for cfg in station_configs if cfg.role is WeatherSourceRole.REANALYSIS
        ]

        # Fan out serially; index every row by logical key, then by its source
        # tag so the priority walk is an O(1) lookup per tier.
        collected: dict[_RowKey, dict[str, RawHistoricalForcing]] = {}
        for source in self._sources.values():
            for row in source.fetch_reanalysis(
                reanalysis_configs, start, end, parameters
            ):
                key: _RowKey = (row.station_id, row.valid_time, row.parameter)
                collected.setdefault(key, {})[row.source] = row

        winners: list[RawHistoricalForcing] = []
        for key, by_source in collected.items():
            chain = self._priority.get(key[2], ())
            if chain:
                winner = next(
                    (
                        by_source[tier.value]
                        for tier in chain
                        if tier.value in by_source
                    ),
                    None,
                )
                if winner is None:
                    continue
            elif len(by_source) == 1:
                # No configured chain for this parameter (5A): exactly one
                # source returned a row for this key, so it wins outright.
                winner = next(iter(by_source.values()))
            else:
                # 2+ distinct sources returned rows with no configured chain
                # to arbitrate between them — a nondeterministic winner would
                # be a silent bug, so this must be loud instead.
                raise ConfigurationError(
                    f"parameter {key[2]!r} has no configured priority chain "
                    f"but {len(by_source)} distinct sources returned rows "
                    f"for station_id={key[0]} valid_time={key[1]}: "
                    f"{sorted(by_source)} — add a priority chain for this "
                    "parameter so the winner is deterministic."
                )
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
            station_count=len(reanalysis_configs),
            row_count=len(winners),
            source_counts=source_counts,
            elapsed_ms=round((monotonic() - started) * 1000, 3),
        )
        return winners
