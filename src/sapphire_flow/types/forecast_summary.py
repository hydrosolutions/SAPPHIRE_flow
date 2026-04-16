from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.enums import (
        EnsembleRepresentation,
        ForecastStatus,
        NwpCycleSource,
        QcStatus,
    )
    from sapphire_flow.types.ids import ForecastId, ModelId, StationId


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastSummaryRow:
    id: ForecastId
    station_id: StationId
    model_id: ModelId
    issued_at: UtcDatetime
    parameter: str
    representation: EnsembleRepresentation
    status: ForecastStatus
    qc_status: QcStatus
    nwp_cycle_source: NwpCycleSource
    created_at: UtcDatetime
