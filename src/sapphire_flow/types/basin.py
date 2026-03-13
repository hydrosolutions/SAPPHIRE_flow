from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import BasinId


class Basin(NamedTuple):
    id: BasinId
    code: str
    name: str
    geometry: Any  # shapely MultiPolygon
    area_km2: float | None
    attributes: dict[str, Any] | None
    band_geometries: list[dict] | None  # type: ignore[type-arg]
    created_at: UtcDatetime
