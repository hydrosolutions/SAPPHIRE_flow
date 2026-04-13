from typing import NewType
from uuid import UUID

StationId = NewType("StationId", UUID)
BasinId = NewType("BasinId", UUID)
ForecastId = NewType("ForecastId", UUID)
HindcastForecastId = NewType("HindcastForecastId", UUID)
ArtifactId = NewType("ArtifactId", UUID)
AlertId = NewType("AlertId", UUID)
RatingCurveId = NewType("RatingCurveId", UUID)
ObservationId = NewType("ObservationId", UUID)
ForecastAdjustmentId = NewType("ForecastAdjustmentId", UUID)
UserId = NewType("UserId", UUID)
AccessTokenId = NewType("AccessTokenId", UUID)
RefreshTokenId = NewType("RefreshTokenId", UUID)
ModelId = NewType("ModelId", str)
POOLED_MODEL_ID = ModelId("_pooled")
BMA_MODEL_ID = ModelId("_bma")
CONSENSUS_MODEL_ID = ModelId("_consensus")
FALLBACK_PRIORITY_THRESHOLD: int = 90
StationGroupId = NewType("StationGroupId", UUID)
ForeignForecastId = NewType("ForeignForecastId", UUID)
HistoricalForcingId = NewType("HistoricalForcingId", UUID)
