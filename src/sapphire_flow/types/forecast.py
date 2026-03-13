from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.enums import (
        EnsembleRepresentation,
        ForcingType,
        ForecastStatus,
        WarmUpSource,
    )
    from sapphire_flow.types.ids import (
        ArtifactId,
        ForecastAdjustmentId,
        ForecastId,
        HindcastForecastId,
        ModelId,
        StationId,
    )


class OperationalForecast(NamedTuple):
    id: ForecastId
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId
    issued_at: UtcDatetime
    nwp_cycle_reference_time: UtcDatetime
    nwp_cycle_is_fallback: bool
    representation: EnsembleRepresentation
    status: ForecastStatus
    version: int
    warm_up_source: WarmUpSource | None
    warm_up_state_age_hours: float | None
    observation_staleness_hours: float | None
    ensemble: ForecastEnsemble
    created_at: UtcDatetime
    updated_at: UtcDatetime


class HindcastForecast(NamedTuple):
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


class ForecastAdjustment(NamedTuple):
    id: ForecastAdjustmentId
    forecast_id: ForecastId
    forecaster_id: UUID
    adjusted_at: UtcDatetime
    rationale: str
    adjustments: list[dict]  # type: ignore[type-arg]
