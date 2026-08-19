from enum import Enum, StrEnum


class QcStatus(Enum):
    RAW = "raw"
    QC_PASSED = "qc_passed"
    QC_FAILED = "qc_failed"
    QC_SUSPECT = "qc_suspect"
    MISSING = "missing"


class ForecastStatus(Enum):
    RAW = "raw"
    REVIEWED = "reviewed"
    PUBLISHED = "published"


class EnsembleRepresentation(Enum):
    MEMBERS = "members"
    QUANTILES = "quantiles"


class WarmUpSource(Enum):
    FRESH = "fresh"
    SNAPSHOT = "snapshot"
    COLD_START = "cold_start"


class AlertStatus(Enum):
    RAISED = "raised"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class AlertSource(Enum):
    FORECAST = "forecast"
    OBSERVATION = "observation"
    PIPELINE = "pipeline"


class ArtifactScope(Enum):
    STATION = "station"
    GROUP = "group"
    VIRTUAL = "virtual"


class ModelArtifactStatus(Enum):
    TRAINING = "training"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ForcingType(Enum):
    NWP_ARCHIVE = "nwp_archive"
    REANALYSIS = "reanalysis"


class SkillSource(Enum):
    HINDCAST_NWP_ARCHIVE = "hindcast_nwp_archive"
    HINDCAST_REANALYSIS = "hindcast_reanalysis"
    OPERATIONAL = "operational"
    TRANSFER_VALIDATION = "transfer_validation"


class FlowRegime(Enum):
    LOW = "low"
    HIGH = "high"
    FLOOD = "flood"


class SpatialRepresentation(Enum):
    POINT = "point"
    BASIN_AVERAGE = "basin_average"
    ELEVATION_BAND = "elevation_band"
    GRIDDED = "gridded"


class EnsembleMode(Enum):
    SINGLE = "single"
    ENSEMBLE = "ensemble"


class ForcingRoute(Enum):
    """Plan 151 D10: which assembler built a ``StationModelInputs``, carried
    explicitly on the boundary type rather than inferred from absent data.

    ``LEGACY_SUPERSET`` (the default) is ``services/operational_inputs.py``'s
    superset assembly: ONE frame per station built to the MAX horizon across
    the station's co-assigned models, so a shorter-horizon model is routinely
    handed MORE future rows than it declared. Over-delivery is part of that
    route's contract (``models/nwp_regression.py``: "Over-delivery ... is
    tolerated and forecast in full") and must not be truncated.

    ``PER_TRACK`` is ``services/track_assembly.py``'s per-assignment
    assembly, where the frame is contracted per feature and D9's
    per-variable ``future_steps`` slice applies at the FI boundary.
    """

    LEGACY_SUPERSET = "legacy_superset"
    PER_TRACK = "per_track"


class ThresholdSource(Enum):
    AUTHORITY = "authority"
    INFERRED = "inferred"


class ThresholdDirection(Enum):
    ABOVE = "above"
    BELOW = "below"


class ModelCombinationStrategy(Enum):
    PRIMARY = "primary"
    POOLED = "pooled"
    BMA = "bma"
    CONSENSUS = "consensus"


class ModelTier(Enum):
    SKILL = "skill"
    FALLBACK = "fallback"


class AlertEligibility(Enum):
    SKILL_FORECAST = "skill_forecast"
    CURRENT_OBS_PROXY = "current_obs_proxy"
    NO_EVENT_INFORMATION = "no_event_information"


class StaticNaming(Enum):
    """Plan 155 D16: which namespace a model's declared static-attribute
    names are written in. Read off the model as a class attribute, exactly
    like ``model_tier`` / ``alert_eligibility`` (``services/model_registry.py``).

    ``NATIVE`` (the default) is every incumbent model's behaviour today —
    bare keys, no projection. ``CARAVAN`` opts a model into D15's strict
    ``caravan:``-namespaced, no-bare-fallback resolution rule
    (``services/caravan_statics.py``); a model must declare this explicitly
    because inference from the alias table cannot distinguish a Caravan
    direct name (e.g. ``area``) from an incumbent's own same-named bare
    attribute.
    """

    NATIVE = "native"
    CARAVAN = "caravan"


class ForecastCycleHealth(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


class RegulationType(Enum):
    UNREGULATED = "unregulated"
    RESERVOIR = "reservoir"
    IRRIGATION_DIVERSION = "irrigation_diversion"
    RUN_OF_RIVER_HYDRO = "run_of_river_hydro"


class StationKind(Enum):
    WEATHER = "weather"
    RIVER = "river"
    LAKE = "lake"


class ParameterDomain(Enum):
    RIVER = "river"
    WEATHER = "weather"
    WATER_QUALITY = "water_quality"
    GROUNDWATER = "groundwater"
    SOIL = "soil"


class AggregationMethod(Enum):
    SUM = "sum"
    MEAN = "mean"


class PipelineHealthStatus(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


class PipelineCheckType(Enum):
    NWP_DELIVERY = "nwp_delivery"
    OBSERVATION_FRESHNESS = "observation_freshness"
    FORECAST_FRESHNESS = "forecast_freshness"
    FLOW_RUN_HEALTH = "flow_run_health"
    DISK_USAGE = "disk_usage"
    BACKUP_FRESHNESS = "backup_freshness"
    BACKUP_RESTORE_TEST = "backup_restore_test"
    FORECAST_STATION_DARK = "forecast_station_dark"
    ALERT_SUPPRESSED_FALLBACK = "alert_suppressed_fallback"
    PRIORITY_MIGRATION_AUDIT = "priority_migration_audit"
    CLIMATOLOGY_THRESHOLD_REVIEW = "climatology_threshold_review"
    BAFU_FORECAST_FRESHNESS = "bafu_forecast_freshness"
    WEATHER_HISTORY_INGEST = "weather_history_ingest"
    BAFU_OBSERVATION_FRESHNESS = "bafu_observation_freshness"
    # Plan 146 D7: DEDICATED check type for the recap-reanalysis snow ingest —
    # kept distinct from WEATHER_HISTORY_INGEST (MeteoSwiss) so an operator
    # filtering one feed's health never conflates it with the other's.
    RECAP_SNOW_REANALYSIS_INGEST = "recap_snow_reanalysis_ingest"
    # Plan 175 D8/T3: the operational ingest's per-station FETCH outcome —
    # deliberately distinct from BAFU_OBSERVATION_FRESHNESS (the quarantined
    # Plan 136 archive collector's own heartbeat, watched by the watchdog)
    # and from OBSERVATION_FRESHNESS (per-station staleness). Reusing either
    # would contaminate a check the watchdog already queries.
    OBSERVATION_INGEST_FETCH = "observation_ingest_fetch"


class NotificationChannel(Enum):
    EMAIL = "email"
    SMS = "sms"
    WEBHOOK = "webhook"


class StationStatus(Enum):
    ONBOARDING = "onboarding"
    OPERATIONAL = "operational"
    SUSPENDED = "suspended"
    DECOMMISSIONED = "decommissioned"


class ObservationSource(Enum):
    MEASURED = "measured"
    RATING_CURVE_DERIVED = "rating_curve_derived"
    COMPONENT_DERIVED = "component_derived"
    MANUAL_IMPORT = "manual_import"


class FetchOutcomeCause(Enum):
    """Plan 175 D9 — the per-station LINDAS fetch failure taxonomy. `NO_DATA`
    and `MALFORMED_RESPONSE` are deliberately split: pre-Plan-175 code could
    not tell a legitimately-empty poll from a bad timestamp, since both
    collapsed to the same empty `list[RawObservation]`."""

    RATE_LIMITED = "rate_limited"
    HTTP_STATUS_ERROR = "http_status_error"
    TRANSPORT_ERROR = "transport_error"
    MALFORMED_RESPONSE = "malformed_response"
    NO_DATA = "no_data"


class GaugingStatus(Enum):
    GAUGED = "gauged"
    UNGAUGED = "ungauged"
    CALCULATED = "calculated"


class StationOwnership(Enum):
    OWN = "own"
    FOREIGN = "foreign"


class ForeignForecastStatus(Enum):
    PUBLISHED = "published"


class NwpCycleSource(Enum):
    PRIMARY = "primary"
    FALLBACK = "fallback"
    RUNOFF_ONLY = "runoff_only"


class WeatherSourceStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class WeatherSourceRole(Enum):
    FORECAST = "forecast"
    REANALYSIS = "reanalysis"


class SkillFreshness(Enum):
    CURRENT = "current"
    STALE = "stale"


class ModelAssignmentStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class FlowRunState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CRASHED = "crashed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class AuditActorType(Enum):
    USER = "user"
    API_KEY = "api_key"
    SYSTEM = "system"


class AccessTokenRole(Enum):
    """Plan 147 Slice C: the v1.0 headless HTTP role model (G4) — exactly
    two roles, both GET-only. `CONSUMER` is station-scoped (per
    `access_token_stations`); `ADMIN` is unscoped read + CLI token/tenant
    management. No third role (no session/operator role in v1.0)."""

    CONSUMER = "consumer"
    ADMIN = "admin"


class AuditEventType(Enum):
    """Plan 147 Slice B: promoted from design-intent
    (`docs/spec/types-and-protocols.md`) to a runtime enum. v1.0 wires
    API_KEY_CREATED / API_KEY_REVOKED (Slice C) and MODEL_PROMOTED /
    MODEL_REJECTED / STATION_ONBOARDED / MODEL_ASSIGNED (Slice E); the
    remaining members are v1.x session-auth events, kept here so the enum
    matches the authoritative spec/architecture-context contract in full.
    STATION_ONBOARDED and MODEL_ASSIGNED are additive members (Plan 147
    Slice B) not present in the original spec-only draft.
    """

    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    PASSWORD_CHANGED = "password_changed"
    USER_CREATED = "user_created"
    USER_DEACTIVATED = "user_deactivated"
    API_KEY_CREATED = "api_key_created"
    API_KEY_REVOKED = "api_key_revoked"
    API_KEY_REQUEST = "api_key_request"
    FORECAST_STATUS_CHANGE = "forecast_status_change"
    FORECAST_ADJUSTED = "forecast_adjusted"
    MODEL_PROMOTED = "model_promoted"
    MODEL_REJECTED = "model_rejected"
    STATION_STATUS_CHANGE = "station_status_change"
    OBSERVATION_REPROCESSED = "observation_reprocessed"
    STATION_ONBOARDED = "station_onboarded"
    MODEL_ASSIGNED = "model_assigned"


class ForcingProvenance(Enum):
    NWP_DIRECT = "nwp_direct"
    OBSERVED = "observed"
    INTERPOLATED = "interpolated"
    GAP_FILLED_CLIMATOLOGY = "gap_filled_climatology"
    GAP_FILLED_PERSISTENCE = "gap_filled_persistence"
    REANALYSIS = "reanalysis"
    DERIVED = "derived"
    UNKNOWN = "unknown"


class OnboardingOutcome(Enum):
    PROMOTED = "promoted"
    GATE_REJECTED = "gate_rejected"
    SKIPPED_COMPAT = "skipped_compat"
    SKIPPED_NO_DATA = "skipped_no_data"
    SKIPPED_INSUFFICIENT_EVAL = "skipped_insufficient_eval"
    FAILED_SMOKE_TEST = "failed_smoke_test"
    FAILED_TRAINING = "failed_training"
    FAILED_HINDCAST = "failed_hindcast"
    FAILED_SKILL = "failed_skill"
    FAILED_ASSIGNMENT = "failed_assignment"


class InputQualityLevel(Enum):
    FULL = "full"
    PARTIAL = "partial"
    DEGRADED = "degraded"


class InputQualityCategory(Enum):
    OBSERVATION = "observation"
    NWP = "nwp"
    WARM_UP = "warm_up"


class InterpolationMethod(Enum):
    LINEAR = "linear"
    LOG_LINEAR = "log_linear"
