# pyright: reportUnknownMemberType=false
from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, cast

from sapphire_flow.types.enums import QcStatus

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import QcFlag
    from sapphire_flow.types.enums import ObservationSource
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
    value: float | None
    source: ObservationSource
    rating_curve_id: RatingCurveId | None
    rating_curve_correction_version: str | None
    qc_status: QcStatus
    qc_flags: list[QcFlag]
    qc_rule_version: str | None
    created_at: UtcDatetime


def _observation_new(
    cls: type,
    id: ObservationId,  # noqa: A002
    station_id: StationId,
    timestamp: UtcDatetime,
    parameter: str,
    value: float | None,
    source: ObservationSource,
    rating_curve_id: RatingCurveId | None,
    rating_curve_correction_version: str | None,
    qc_status: QcStatus,
    qc_flags: list[QcFlag],
    qc_rule_version: str | None,
    created_at: UtcDatetime,
) -> Observation:
    if qc_status == QcStatus.MISSING and value is not None:
        raise ValueError("Observation.value must be None when qc_status is MISSING")
    if qc_status != QcStatus.MISSING and value is None:
        raise ValueError(
            "Observation.value must not be None when qc_status is not MISSING"
        )
    return cast(
        "Observation",
        tuple.__new__(
            cls,
            (
                id,
                station_id,
                timestamp,
                parameter,
                value,
                source,
                rating_curve_id,
                rating_curve_correction_version,
                qc_status,
                qc_flags,
                qc_rule_version,
                created_at,
            ),
        ),
    )


Observation.__new__ = _observation_new  # type: ignore[method-assign]
