"""Task 1b (Plan 173, M-A3) — normalised frame -> `Observation` objects (D2, D7).

D2: `Observation.__post_init__` requires `value is None` iff
`qc_status == MISSING` (`sapphire_flow/types/observation.py:46-52`). The
normalised frame (M-A2) is null at every gap — a gap row is constructed as
`MISSING`, a delivered row as `RAW`. This is also semantically right: a gap
*is* a missing observation, and `_apply_frozen_sensor` breaks a run on a
`None` value, which is precisely the bridging M-A2 exists to prevent.

D7: chunked per station — `Stage1QualityChecker.check()` already groups by
`(station_id, parameter)` internally, so per-station batches change no
result and bound peak memory (~52k observations/station vs ~1.37M total).
`iter_observations_by_station` is the streaming entry point the pipeline
uses: it YIELDS one station's `Observation` list at a time rather than
materialising the whole `dict[Station, list[Observation]]` up front, so a
caller (`qc_mask.iter_station_results`) can compute and discard one
station's result before the next station's objects are even constructed.
`observations_by_station` (eager, whole-dict) still exists for tests and
small inputs and is now defined in terms of the generator.

`StationId`/`ObservationId` are deterministic (`uuid5`) — same station name
or `(station, timestamp)` always mints the same id, so a rerun's mask is
reproducible and flags can be traced back to their source row without a
side table (precedent: `tools/record_fixtures.py:53`).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import TYPE_CHECKING

import polars as pl

from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation
from scripts.dhm_precip.domain_types import Station

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.types.datetime import UtcDatetime

_NAMESPACE = uuid.NAMESPACE_URL


def station_id_for(station: Station) -> StationId:
    """Deterministic — the same station name always mints the same id."""
    return StationId(uuid.uuid5(_NAMESPACE, f"dhm-precip-station:{station}"))


def _observation_id_for(station: Station, timestamp: UtcDatetime) -> ObservationId:
    return ObservationId(
        uuid.uuid5(
            _NAMESPACE, f"dhm-precip-observation:{station}:{timestamp.isoformat()}"
        )
    )


def _observations_for_group(
    station: Station,
    group: pl.DataFrame,
    *,
    parameter: str,
    created_at: UtcDatetime,
) -> list[Observation]:
    station_id = station_id_for(station)
    observations: list[Observation] = []
    for row in group.iter_rows(named=True):
        value = row["value_mm"]
        timestamp = row["timestamp"].replace(tzinfo=UTC)
        status = QcStatus.MISSING if value is None else QcStatus.RAW
        observations.append(
            Observation(
                id=_observation_id_for(station, timestamp),
                station_id=station_id,
                timestamp=timestamp,
                parameter=parameter,
                value=value,
                source=ObservationSource.MANUAL_IMPORT,
                rating_curve_id=None,
                rating_curve_correction_version=None,
                qc_status=status,
                qc_flags=[],
                qc_rule_version=None,
                created_at=created_at,
            )
        )
    return observations


def iter_observations_by_station(
    normalised: pl.DataFrame,
    *,
    parameter: str,
    created_at: UtcDatetime,
) -> Iterator[tuple[Station, list[Observation]]]:
    """D7 — the streaming entry point. YIELDS one `(station,
    list[Observation])` pair at a time, sorted ascending by timestamp within
    each station (the ordering `_apply_frozen_sensor` depends on). Filters
    the normalised frame down to one station BEFORE building that station's
    `Observation` objects, so a caller that consumes and discards each
    yielded list (`qc_mask.iter_station_results`) never holds more than one
    station's ~52k `Observation` objects in memory — not all ~1.37M at once.

    The normalised frame's `timestamp` column is naive wall-clock (the DHM
    workbook's own convention, carried through unchanged by M-A2) — attached
    with UTC tzinfo here only to satisfy `Observation.timestamp`'s
    `UtcDatetime` boundary type; this is a typed-wrapper formality for the
    production QC service, not a claim about the real timezone.
    """
    sorted_frame = normalised.sort(["station", "timestamp"])
    station_names = sorted_frame.get_column("station").unique(maintain_order=False)
    for station_name in sorted(station_names.to_list()):
        station = Station(station_name)
        group = sorted_frame.filter(pl.col("station") == station_name)
        yield (
            station,
            _observations_for_group(
                station, group, parameter=parameter, created_at=created_at
            ),
        )


def observations_by_station(
    normalised: pl.DataFrame,
    *,
    parameter: str,
    created_at: UtcDatetime,
) -> dict[Station, list[Observation]]:
    """One `Observation` per normalised row (`(source_row_index, station,
    timestamp, value_mm)`), grouped by station, sorted ascending by
    timestamp within each group. Eagerly materialises every station's
    result at once — fine for tests and small inputs, but NOT what the
    production pipeline uses (that's `iter_observations_by_station`, D7)."""
    return dict(
        iter_observations_by_station(
            normalised, parameter=parameter, created_at=created_at
        )
    )
