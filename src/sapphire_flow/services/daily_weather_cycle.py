from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from typing import TYPE_CHECKING

from sapphire_flow.exceptions import StoreError
from sapphire_flow.services.run_group_forecast import (
    GroupInputAssembly,
    GroupMemberInputIssue,
    GroupMemberInputReason,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import NwpCycleSource

if TYPE_CHECKING:
    from collections.abc import Callable

    from sapphire_flow.protocols.stores import WeatherForecastStore
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class DailyWeatherAssembly:
    assembly: GroupInputAssembly
    source: NwpCycleSource
    reference_time: UtcDatetime | None


def assemble_scheduled_daily_weather(
    *,
    assemble: Callable[[UtcDatetime], GroupInputAssembly],
    store: WeatherForecastStore,
    station_ids: frozenset[StationId],
    sources: frozenset[str],
    future_features: frozenset[str],
    cycle_time: UtcDatetime,
    cycle_source: NwpCycleSource,
    reference_time: UtcDatetime | None,
    issue_time: UtcDatetime,
    now: UtcDatetime,
    max_age_hours: float,
) -> DailyWeatherAssembly:
    """Select one cached cycle before prediction; never combine assemblies."""
    if not future_features:
        return DailyWeatherAssembly(
            assembly=assemble(cycle_time),
            source=cycle_source,
            reference_time=reference_time,
        )
    oldest = ensure_utc(now - timedelta(hours=max_age_hours))
    empty = GroupInputAssembly(
        expected_station_ids=tuple(sorted(station_ids, key=str)),
        inputs=None,
        metadata_by_station={},
        unavailable_members=tuple(
            GroupMemberInputIssue(
                station_id=sid, reason=GroupMemberInputReason.INSUFFICIENT_FUTURE
            )
            for sid in sorted(station_ids, key=str)
        ),
    )
    original = assemble(cycle_time) if oldest <= cycle_time <= now else empty
    unchanged = DailyWeatherAssembly(
        assembly=original, source=cycle_source, reference_time=reference_time
    )
    if original.inputs is not None or "icon_ch2_eps" not in sources:
        return unchanged
    # ICON's declared issue cadence is 00/06/12/18 UTC. Only these stored
    # cycles can be candidates; the store query and assembly count are bounded.
    end = min(now, issue_time, cycle_time)
    if end < oldest:
        return unchanged
    received = store.fetch_received_cycles(
        "icon_ch2_eps", oldest, ensure_utc(end + timedelta(microseconds=1))
    )
    if len(received) > ceil(max_age_hours / 6) + 1:
        raise StoreError("daily weather cycle lookup exceeded ICON cadence bound")
    candidates = sorted(
        {
            cycle
            for cycle in received
            if oldest <= cycle <= end
            and cycle < cycle_time
            and cycle.hour in (0, 6, 12, 18)
            and (cycle.minute, cycle.second, cycle.microsecond) == (0, 0, 0)
        },
        reverse=True,
    )
    for candidate in candidates:
        assembly = assemble(candidate)
        if assembly.inputs is not None:
            return DailyWeatherAssembly(
                assembly=assembly,
                source=NwpCycleSource.FALLBACK,
                reference_time=candidate,
            )
    return unchanged
