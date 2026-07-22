from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import BasinId, PackageId


@dataclass(frozen=True, kw_only=True, slots=True)
class Basin:
    id: BasinId
    code: str
    name: str
    geometry: Any  # shapely MultiPolygon
    area_km2: float | None
    attributes: dict[str, Any] | None
    regional_basin: str | None = None
    band_geometries: list[dict] | None  # type: ignore[type-arg]
    created_at: UtcDatetime
    network: str
    # Plan 120 Task 0A: the basin/static package that produced the CURRENT
    # projection row. NULL for legacy (pre-120) and non-package (onboarding)
    # basins — see basin_versions.package_id for the same sentinel on the
    # versioned history row.
    package_id: PackageId | None = None
