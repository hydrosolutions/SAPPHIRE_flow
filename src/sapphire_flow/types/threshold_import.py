from __future__ import annotations

import math
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.domain import StationThreshold


class ThresholdImportStatus(Enum):
    IMPORTED = "imported"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    MISSING = "missing"
    ERROR = "error"
    PREVIEW = "preview"


def classify_threshold_import(
    proposed: list[StationThreshold], existing: list[StationThreshold]
) -> ThresholdImportStatus:
    if not proposed:
        return ThresholdImportStatus.MISSING
    first = proposed[0]
    if any(
        item.station_id != first.station_id
        or item.parameter != first.parameter
        or not math.isfinite(item.value)
        for item in proposed
    ) or len({item.danger_level for item in proposed}) != len(proposed):
        raise ValueError(
            "threshold import requires finite distinct levels for one station/parameter"
        )
    current = {
        (item.station_id, item.parameter, item.danger_level): (item.value, item.source)
        for item in existing
    }
    desired = {
        (item.station_id, item.parameter, item.danger_level): (item.value, item.source)
        for item in proposed
    }
    if any(key in current and current[key] != value for key, value in desired.items()):
        return ThresholdImportStatus.CONFLICT
    if all(key in current for key in desired):
        return ThresholdImportStatus.UNCHANGED
    return ThresholdImportStatus.PREVIEW
