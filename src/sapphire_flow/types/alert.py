from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import AlertModelStrategy, AlertSource, AlertStatus
    from sapphire_flow.types.ids import AlertId, ModelId, StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class Alert:
    id: AlertId
    station_id: StationId | None
    source: AlertSource
    alert_level: str
    status: AlertStatus
    trigger_probability: float | None
    trigger_value: float | None
    triggered_at: UtcDatetime
    acknowledged_at: UtcDatetime | None
    acknowledged_by: UUID | None
    resolved_at: UtcDatetime | None
    first_detected_at: UtcDatetime | None
    notified_at: UtcDatetime | None
    created_at: UtcDatetime
    model_ids: tuple[ModelId, ...] = ()
    alert_model_strategy: AlertModelStrategy | None = None
