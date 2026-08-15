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

`StationId`/`ObservationId` are deterministic (`uuid5`) — same station name
or `(station, timestamp)` always mints the same id, so a rerun's mask is
reproducible and flags can be traced back to their source row without a
side table (precedent: `tools/record_fixtures.py:53`).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation
from scripts.dhm_precip.domain_types import Station

if TYPE_CHECKING:
    import polars as pl

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


def observations_by_station(
    normalised: pl.DataFrame,
    *,
    parameter: str,
    created_at: UtcDatetime,
) -> dict[Station, list[Observation]]:
    """One `Observation` per normalised row (`(source_row_index, station,
    timestamp, value_mm)`), grouped by station, sorted ascending by
    timestamp within each group (the ordering `_apply_frozen_sensor`
    depends on).

    The normalised frame's `timestamp` column is naive wall-clock (the DHM
    workbook's own convention, carried through unchanged by M-A2) — attached
    with UTC tzinfo here only to satisfy `Observation.timestamp`'s
    `UtcDatetime` boundary type; this is a typed-wrapper formality for the
    production QC service, not a claim about the real timezone.
    """
    result: dict[Station, list[Observation]] = {}
    partitions = normalised.sort(["station", "timestamp"]).partition_by(
        "station", maintain_order=True, as_dict=True
    )
    for (station_name,), group in partitions.items():
        station = Station(station_name)
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
        result[station] = observations
    return result
