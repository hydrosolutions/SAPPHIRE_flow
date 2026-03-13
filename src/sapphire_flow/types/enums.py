from enum import Enum


class QcStatus(Enum):
    RAW = "raw"
    QC_PASSED = "qc_passed"
    QC_FAILED = "qc_failed"
    QC_SUSPECT = "qc_suspect"


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


class ThresholdSource(Enum):
    AUTHORITY = "authority"
    INFERRED = "inferred"


class ThresholdDirection(Enum):
    ABOVE = "above"
    BELOW = "below"


class RegulationType(Enum):
    UNREGULATED = "unregulated"
    RESERVOIR = "reservoir"
    IRRIGATION_DIVERSION = "irrigation_diversion"
    RUN_OF_RIVER_HYDRO = "run_of_river_hydro"


class StationKind(Enum):
    WEATHER = "weather"
    RIVER = "river"


class ParameterDomain(Enum):
    RIVER = "river"
    WEATHER = "weather"


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


class NotificationChannel(Enum):
    EMAIL = "email"
    SMS = "sms"
    WEBHOOK = "webhook"


class DlqResolution(Enum):
    REPLAYED = "replayed"
    DISCARDED = "discarded"


class AdjustmentType(Enum):
    SHIFT = "shift"
    SCALE = "scale"
    CAP = "cap"
    FLOOR = "floor"


class Calendar(Enum):
    GREGORIAN = "gregorian"
    BIKRAM_SAMBAT = "bikram_sambat"


class UserRole(Enum):
    ORG_ADMIN = "org_admin"
    IT_ADMIN = "it_admin"
    MODEL_ADMIN = "model_admin"
    FORECASTER = "forecaster"


class StationStatus(Enum):
    ONBOARDING = "onboarding"
    OPERATIONAL = "operational"
    SUSPENDED = "suspended"
    DECOMMISSIONED = "decommissioned"


class ObservationSource(Enum):
    MEASURED = "measured"
    RATING_CURVE_DERIVED = "rating_curve_derived"
    MANUAL_IMPORT = "manual_import"


class AuditEventType(Enum):
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


class AuditActorType(Enum):
    USER = "user"
    API_KEY = "api_key"
    SYSTEM = "system"
