from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import QcStatus

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcFlag
    from sapphire_flow.types.enums import ObservationSource
    from sapphire_flow.types.ids import (
        ObservationId,
        ObservationVersionId,
        RatingCurveId,
        StationId,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class RawObservation:
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float
    source: ObservationSource
    rating_curve_id: RatingCurveId | None = None
    rating_curve_correction_version: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class Observation:
    id: ObservationId
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float | None
    source: ObservationSource
    rating_curve_id: RatingCurveId | None
    rating_curve_correction_version: str | None
    qc_status: QcStatus
    qc_flags: list[QcFlag]
    qc_rule_version: str | None
    created_at: UtcDatetime

    def __post_init__(self) -> None:
        if self.qc_status == QcStatus.MISSING and self.value is not None:
            raise ValueError("Observation.value must be None when qc_status is MISSING")
        if self.qc_status != QcStatus.MISSING and self.value is None:
            raise ValueError(
                "Observation.value must not be None when qc_status is not MISSING"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class ArchivedObservationValue:
    """A discharge value superseded by a rating-curve reprocessing (Plan 035 Task 3).

    Archived before Flow 12 Branch A overwrites a rating-curve-derived observation,
    so the pre-reprocessing operational record survives.
    """

    id: ObservationVersionId
    observation_id: ObservationId
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float | None  # None if the superseded observation was MISSING
    rating_curve_id: RatingCurveId  # curve that produced the archived value
    superseded_at: UtcDatetime
    superseded_by_curve_id: RatingCurveId  # curve that replaced it
