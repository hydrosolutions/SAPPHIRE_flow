from __future__ import annotations

import math
from collections import Counter
from datetime import timedelta
from statistics import mean
from typing import TYPE_CHECKING

from sapphire_flow.types.observation_history import (
    BlockSummary,
    HistoryRowLimitError,
    StationHistory,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.observation_history import (
        HistoryGrid,
        HistoryRead,
        HistoryReading,
    )


def unique_readings(rows: Iterable[HistoryReading]) -> tuple[HistoryReading, ...]:
    unique: dict[tuple[StationId, UtcDatetime], HistoryReading] = {}
    for row in rows:
        if not math.isfinite(row.value):
            continue
        key = (row.station_id, row.timestamp)
        previous = unique.get(key)
        if (
            previous is not None
            and float(previous.value).hex() != float(row.value).hex()
        ):
            raise ValueError("conflicting duplicate observation history key")
        unique[key] = row
    return tuple(
        sorted(unique.values(), key=lambda row: (row.station_id, row.timestamp))
    )


def check_history_bounds(
    request: HistoryRead, rows: tuple[HistoryReading, ...]
) -> None:
    if len(rows) > request.fleet_row_cap:
        raise HistoryRowLimitError(
            bound=request.fleet_row_cap, eligible_rows=request.fleet_row_cap + 1
        )
    counts = Counter(row.station_id for row in rows)
    for station_id, count in counts.items():
        if station_id not in request.station_ids:
            raise ValueError("out-of-scope observation history")
        if count > request.station_row_cap:
            raise HistoryRowLimitError(
                bound=request.station_row_cap,
                eligible_rows=min(count, request.station_row_cap + 1),
                station_id=station_id,
            )


def summarize_history(
    grid: HistoryGrid,
    station_ids: tuple[StationId, ...],
    rows: Iterable[HistoryReading],
) -> tuple[StationHistory, ...]:
    if len(station_ids) > 200 or len(set(station_ids)) != len(station_ids):
        raise ValueError("history station scope must be unique and bounded")
    start, as_of = grid.start, grid.as_of
    native_start, grid_start = grid.native_start, grid.grid_start
    block_step = timedelta(hours=3)
    block_count = (native_start - grid_start) // block_step
    buckets: dict[StationId, list[list[float]]] = {
        sid: [[] for _ in range(block_count)] for sid in station_ids
    }
    native: dict[StationId, list[HistoryReading]] = {sid: [] for sid in station_ids}
    for row in unique_readings(rows):
        if row.station_id not in buckets:
            raise ValueError("out-of-scope observation history")
        if not start <= row.timestamp <= as_of:
            continue
        if row.timestamp >= native_start:
            native[row.station_id].append(row)
        else:
            index = (row.timestamp - grid_start) // block_step
            buckets[row.station_id][index].append(row.value)
    return tuple(
        StationHistory(
            station_id=sid,
            coarse=tuple(_summarize(values) for values in buckets[sid]),
            native=tuple(native[sid]),
        )
        for sid in station_ids
    )


def _summarize(values: list[float]) -> BlockSummary:
    if not values:
        return BlockSummary(mean=None, minimum=None, maximum=None, count=0)
    return BlockSummary(
        mean=mean(values), minimum=min(values), maximum=max(values), count=len(values)
    )
