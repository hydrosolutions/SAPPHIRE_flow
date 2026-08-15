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
FormulaId = NewType("FormulaId", UUID)
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
AQUACAST_CMAL_POOL_PT_MODEL_ID = ModelId("cmal_pool_pt")

# Plan 159 T0d — INTERIM, DELETE-ON-ARRIVAL.
#
# Maps a model to the MINIMUM future steps it may run on, for models whose declared
# `future_steps` is really a CEILING but which cannot yet say so. ForecastInterface
# v0.1.20 added `horizon_semantics=AT_MOST`; a model that declares it takes precedence
# over this table (`services/horizon_semantics.py`), so entries here go stale on their
# own and every use is logged at WARNING.
#
# A value is a FLOOR, never "anything goes": below it the model is still refused.
#
# `cmal_pool_pt` declares 15 daily steps, but its architecture accepts fewer (aquacast
# 85e09a45, "trained horizon is a maximum, not a fixed input length") while the
# declaration cannot express it. 5 = MeteoSwiss ICON-CH2-EPS's 120 h, our operational
# ceiling. **This 5 is a PROVIDER assumption, not a modelling judgement** — whether a
# 15-day-trained model is still useful at 5 days is the modeller's call, and this entry
# must be replaced by their AT_MOST + min_future_steps declaration, not ratified by it.
HORIZON_CEILING_FLOORS: dict[ModelId, int] = {
    AQUACAST_CMAL_POOL_PT_MODEL_ID: 5,
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
# Plan 147 Slice A: the tenant-model foundation. A tenant's identity, used to
# scope stations/groups (canonical on `stations.tenant_id`, see
# `types/tenant.py`).
TenantId = NewType("TenantId", UUID)
HistoricalForcingId = NewType("HistoricalForcingId", UUID)
# Plan 120: basin/static package importer provenance + versioning.
# Producer-declared identifier (manifest.json "package_id"), NOT a UUID —
# see tests/fixtures/basin_static/nepal-dhm-basins/manifest.json:3.
PackageId = NewType("PackageId", str)
BasinVersionId = NewType("BasinVersionId", UUID)
# Plan 147 Slice E: a config-declared operator handle (the `[deployment]`
# block's `operator` field, or a `--operator` CLI override) carried by a
# `WritePrincipal`. A plain string label — NOT a UUID, NOT a UserId, and NOT
# an AccessTokenId (a WritePrincipal is never materialized from an
# access-token; see `types/write_principal.py`).
PrincipalId = NewType("PrincipalId", str)
