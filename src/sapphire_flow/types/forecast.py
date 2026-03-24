from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import (
        EnsembleRepresentation,
        ForcingType,
        ForecastStatus,
        ForeignForecastStatus,
        NwpCycleSource,
        WarmUpSource,
    )
    from sapphire_flow.types.ids import (
        ArtifactId,
        ForecastAdjustmentId,
        ForecastId,
        ForeignForecastId,
        HindcastForecastId,
        ModelId,
        StationId,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalForecast:
    id: ForecastId
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId
    issued_at: UtcDatetime
    nwp_cycle_reference_time: UtcDatetime
    nwp_cycle_source: NwpCycleSource
    representation: EnsembleRepresentation
    status: ForecastStatus
    version: int
    warm_up_source: WarmUpSource | None
    warm_up_state_age_hours: float | None
    observation_staleness_hours: float | None
    ensemble: ForecastEnsemble
    created_at: UtcDatetime
    updated_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class HindcastForecast:
    id: HindcastForecastId
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId
    hindcast_step: UtcDatetime
    forcing_type: ForcingType
    representation: EnsembleRepresentation
    hindcast_run_id: UUID
    ensemble: ForecastEnsemble
    created_at: UtcDatetime


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastAdjustment:
    id: ForecastAdjustmentId
    forecast_id: ForecastId
    forecaster_id: UUID
    adjusted_at: UtcDatetime
    rationale: str
    adjustments: list[dict]  # type: ignore[type-arg]


@dataclass(frozen=True, kw_only=True, slots=True)
class ForeignForecast:
    id: ForeignForecastId
    station_id: StationId
    upstream_instance_url: str
    upstream_station_id: str
    upstream_forecast_id: str
    issued_at: UtcDatetime
    valid_from: UtcDatetime
    valid_to: UtcDatetime
    representation: EnsembleRepresentation
    status: ForeignForecastStatus
    ensemble: ForecastEnsemble
    fetched_at: UtcDatetime
    created_at: UtcDatetime
