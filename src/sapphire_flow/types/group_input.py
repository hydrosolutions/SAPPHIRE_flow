from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.ids import StationId


class GroupMemberInputReason(Enum):
    CADENCE_MISMATCH = "cadence_mismatch"
    MISSING_DECLARED_STATIC = "missing_declared_static"
    MISSING_REQUIRED_TARGET = "missing_required_target"
    DECLARED_STATIC_NOT_REPRESENTABLE = "declared_static_not_representable"
    INSUFFICIENT_FUTURE = "insufficient_future"
    NAN_TOLERANCE_EXCEEDED = "nan_tolerance_exceeded"
    INPUTS_UNAVAILABLE = "inputs_unavailable"
    MISSING_FORECAST_BINDING = "missing_forecast_binding"


@dataclass(frozen=True, kw_only=True, slots=True)
class GroupMemberInputIssue:
    station_id: StationId
    reason: GroupMemberInputReason
