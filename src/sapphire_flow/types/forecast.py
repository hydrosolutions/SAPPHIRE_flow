from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sapphire_flow.types.enums import InputQualityLevel, QcStatus

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.domain import InputQualityFlag, QcFlag
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
        RatingCurveId,
        StationId,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastProvenance:
    """Forward-compatible NWP provenance value object.

    RUNOFF_ONLY carries a null reference time (there is no NWP cycle). Kept
    extensible so future degradation facts (e.g. FI input shortfall) fold in
    here rather than sprawling across flat forecast fields.
    """

    nwp_cycle_source: NwpCycleSource
    nwp_cycle_reference_time: UtcDatetime | None


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalForecast:
    id: ForecastId
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId | None
    issued_at: UtcDatetime
    nwp_cycle_reference_time: UtcDatetime | None
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
    qc_status: QcStatus = QcStatus.RAW
    qc_flags: tuple[QcFlag, ...] = ()
    input_quality: InputQualityLevel = InputQualityLevel.FULL
    input_quality_flags: tuple[InputQualityFlag, ...] = ()
    combination_strategy: str | None = None
    source_model_ids: list[ModelId] | None = None
    rating_curve_id: RatingCurveId | None = None
    """Active rating curve bound to this forecast's station at ``issued_at``.

    NULL for stations that report discharge directly (Swiss BAFU, weather-only).
    Set at forecast-storage time by Task 4; Task 2 only plumbs the column.
    """

    @property
    def provenance(self) -> ForecastProvenance:
        return ForecastProvenance(
            nwp_cycle_source=self.nwp_cycle_source,
            nwp_cycle_reference_time=self.nwp_cycle_reference_time,
        )


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
    qc_status: QcStatus = QcStatus.RAW
    qc_flags: tuple[QcFlag, ...] = ()


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
