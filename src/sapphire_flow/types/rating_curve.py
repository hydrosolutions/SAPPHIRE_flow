from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import InterpolationMethod
    from sapphire_flow.types.ids import RatingCurveId, StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingCurve:
    id: RatingCurveId
    station_id: StationId
    version: int
    valid_from: UtcDatetime
    valid_to: UtcDatetime | None
    points: list[dict]  # type: ignore[type-arg]
    interpolation: InterpolationMethod
    uploaded_by: UUID | None
    created_at: UtcDatetime
