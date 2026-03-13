from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NamedTuple

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import RatingCurveId, StationId


class RatingCurve(NamedTuple):
    id: RatingCurveId
    station_id: StationId
    version: int
    valid_from: UtcDatetime
    valid_to: UtcDatetime | None
    points: list[dict]  # type: ignore[type-arg]
    interpolation: Literal["linear", "log-linear"]
    uploaded_by: UUID | None
    created_at: UtcDatetime
