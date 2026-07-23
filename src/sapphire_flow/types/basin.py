from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import BasinId, BasinVersionId, PackageId


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


@dataclass(frozen=True, kw_only=True, slots=True)
class BasinCorrectionResult:
    """Return value of ``PgBasinStore.update_basin_from_package`` (Plan 120
    Task 2C correction branch). ``superseded_version_id`` is the PRIOR
    current ``basin_versions`` row this correction just stamped
    ``superseded_at`` on — Task 2C's affected-artifact-set query is scoped
    to exactly this id (never every historically-superseded version)."""

    basin_id: BasinId
    superseded_version_id: BasinVersionId
    new_version_id: BasinVersionId


def is_missing_static_value(value: Any) -> bool:
    """A required static-feature value counts as missing when it is absent,
    `None`, or a float NaN (Codex review, Plan 120 fixer round: a
    `{"elevation_mean": None}` attribute previously passed the D-UP gate
    because it only checked key presence)."""
    if value is None:
        return True
    return isinstance(value, float) and math.isnan(value)


def non_null_static_keys(attributes: dict[str, Any] | None) -> frozenset[str]:
    """Static-attribute keys whose value is present and not missing per
    `is_missing_static_value` -- the "available" set for compatibility
    checks and the D-UP training gate."""
    if not attributes:
        return frozenset()
    return frozenset(
        key for key, value in attributes.items() if not is_missing_static_value(value)
    )
