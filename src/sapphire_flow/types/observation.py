from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcFlag
    from sapphire_flow.types.enums import ObservationSource, QcStatus
    from sapphire_flow.types.ids import ObservationId, RatingCurveId, StationId


class RawObservation(NamedTuple):
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float
    source: ObservationSource
    rating_curve_id: RatingCurveId | None = None
    rating_curve_correction_version: str | None = None


class Observation(NamedTuple):
    id: ObservationId
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float
    source: ObservationSource
    rating_curve_id: RatingCurveId | None
    rating_curve_correction_version: str | None
    qc_status: QcStatus
    qc_flags: list[QcFlag]
    qc_rule_version: str | None
    created_at: UtcDatetime
