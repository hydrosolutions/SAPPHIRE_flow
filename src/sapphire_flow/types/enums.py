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


class ModelCombinationStrategy(Enum):
    PRIMARY = "primary"
    POOLED = "pooled"
    BMA = "bma"
    CONSENSUS = "consensus"


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
    MANUAL_IMPORT = "manual_import"


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


class WeatherSourceStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


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
