from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import BasinId


@dataclass(frozen=True, kw_only=True, slots=True)
class Basin:
    id: BasinId
    code: str
    name: str
    geometry: Any  # shapely MultiPolygon
    area_km2: float | None
    attributes: dict[str, Any] | None
    band_geometries: list[dict] | None  # type: ignore[type-arg]
    created_at: UtcDatetime
    network: str
