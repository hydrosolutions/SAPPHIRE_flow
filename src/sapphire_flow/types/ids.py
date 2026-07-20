from typing import NewType
from uuid import UUID

from sapphire_flow.types.enums import AlertEligibility, ModelTier

StationId = NewType("StationId", UUID)
BasinId = NewType("BasinId", UUID)
ForecastId = NewType("ForecastId", UUID)
HindcastForecastId = NewType("HindcastForecastId", UUID)
ArtifactId = NewType("ArtifactId", UUID)
AlertId = NewType("AlertId", UUID)
RatingCurveId = NewType("RatingCurveId", UUID)
ObservationId = NewType("ObservationId", UUID)
ObservationVersionId = NewType("ObservationVersionId", UUID)
ForecastAdjustmentId = NewType("ForecastAdjustmentId", UUID)
UserId = NewType("UserId", UUID)
AccessTokenId = NewType("AccessTokenId", UUID)
RefreshTokenId = NewType("RefreshTokenId", UUID)
ModelId = NewType("ModelId", str)
POOLED_MODEL_ID = ModelId("_pooled")
BMA_MODEL_ID = ModelId("_bma")
CONSENSUS_MODEL_ID = ModelId("_consensus")
FALLBACK_PRIORITY_THRESHOLD: int = 90
LINEAR_REGRESSION_DAILY_MODEL_ID = ModelId("linear_regression_daily")
NWP_REGRESSION_MODEL_ID = ModelId("nwp_regression")
NWP_RAINFALL_RUNOFF_MODEL_ID = ModelId("nwp_rainfall_runoff")
# Plan 129: past-runoff + season + continuous-precip (RhiresD/RprelimD past +
# NWP future) regression — the RprelimD-consuming continuous-precip-knit model.
SEASONAL_PRECIP_RUNOFF_REGRESSION_MODEL_ID = ModelId(
    "seasonal_precip_runoff_regression"
)
CLIMATOLOGY_FALLBACK_MODEL_ID = ModelId("climatology_fallback")
PERSISTENCE_FALLBACK_MODEL_ID = ModelId("persistence_fallback")
MODEL_TIERS: dict[ModelId, ModelTier] = {
    LINEAR_REGRESSION_DAILY_MODEL_ID: ModelTier.SKILL,
    NWP_REGRESSION_MODEL_ID: ModelTier.SKILL,
    NWP_RAINFALL_RUNOFF_MODEL_ID: ModelTier.SKILL,
    SEASONAL_PRECIP_RUNOFF_REGRESSION_MODEL_ID: ModelTier.SKILL,
    CLIMATOLOGY_FALLBACK_MODEL_ID: ModelTier.FALLBACK,
    PERSISTENCE_FALLBACK_MODEL_ID: ModelTier.FALLBACK,
}
ALERT_ELIGIBILITIES: dict[ModelId, AlertEligibility] = {
    LINEAR_REGRESSION_DAILY_MODEL_ID: AlertEligibility.SKILL_FORECAST,
    NWP_REGRESSION_MODEL_ID: AlertEligibility.SKILL_FORECAST,
    NWP_RAINFALL_RUNOFF_MODEL_ID: AlertEligibility.SKILL_FORECAST,
    SEASONAL_PRECIP_RUNOFF_REGRESSION_MODEL_ID: AlertEligibility.SKILL_FORECAST,
    CLIMATOLOGY_FALLBACK_MODEL_ID: AlertEligibility.NO_EVENT_INFORMATION,
    PERSISTENCE_FALLBACK_MODEL_ID: AlertEligibility.CURRENT_OBS_PROXY,
}
FALLBACK_MODEL_IDS: frozenset[ModelId] = frozenset(
    model_id for model_id, tier in MODEL_TIERS.items() if tier is ModelTier.FALLBACK
)
FALLBACK_ASSIGNMENT_PRIORITIES: dict[ModelId, int] = {
    CLIMATOLOGY_FALLBACK_MODEL_ID: 100,
    PERSISTENCE_FALLBACK_MODEL_ID: 90,
}
StationGroupId = NewType("StationGroupId", UUID)
ForeignForecastId = NewType("ForeignForecastId", UUID)
HistoricalForcingId = NewType("HistoricalForcingId", UUID)
