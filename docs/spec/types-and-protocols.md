# Types and Protocols Specification

Authoritative reference for Python type definitions and Protocol signatures.
Architecture-context.md owns rationale and DB schemas; this file owns Python signatures.

See also: `docs/conventions.md` for the enum master list and naming rules.

---

## ID types

All entity IDs are `NewType` wrappers. UUID-based IDs appear as `Utf8` strings in
Polars DataFrames; conversion happens at the store boundary.

```python
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

# ModelId wraps str, not UUID — entry point name is the stable TEXT PK
ModelId = NewType("ModelId", str)
StationGroupId = NewType("StationGroupId", UUID)
ForeignForecastId = NewType("ForeignForecastId", UUID)

# pipeline_health and audit_log use BIGSERIAL PK — append-only, never
# referenced by ID from other tables. No NewType wrapper.
```

Module: `types/ids.py`

---

## UtcDatetime

```python
from datetime import datetime, timezone
from typing import NewType

UtcDatetime = NewType("UtcDatetime", datetime)

def ensure_utc(dt: datetime) -> UtcDatetime:
    """Convert a timezone-aware datetime to UTC. Reject naive datetimes."""
    if dt.tzinfo is None:
        raise ValueError(f"Naive datetime not allowed: {dt!r}")
    return UtcDatetime(dt.astimezone(timezone.utc))
```

Module: `types/datetime.py`

---

## Enums

Defined in `types/enums.py`. Values match the DB convention (lowercase `.value`).
See `docs/conventions.md` enum master list for the authoritative value strings.

```python
from enum import Enum

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
    STATION = "station"        # one artifact per (station, model) — conceptual models
    GROUP = "group"            # one artifact per (station_group, model) — ML models

class ModelArtifactStatus(Enum):
    TRAINING = "training"
    PENDING_APPROVAL = "pending_approval"  # v1 — approval gate deferred
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"                  # v1 — approval gate deferred
    # v0: only ACTIVE and SUPERSEDED are used (auto-promote, no approval gate).
    # TRAINING is transient during training flow. PENDING_APPROVAL and REJECTED
    # require the approval gate (v1). See v0-scope.md §A7.

class ForcingType(Enum):
    NWP_ARCHIVE = "nwp_archive"
    REANALYSIS = "reanalysis"

class SkillSource(Enum):
    HINDCAST_NWP_ARCHIVE = "hindcast_nwp_archive"
    HINDCAST_REANALYSIS = "hindcast_reanalysis"
    OPERATIONAL = "operational"
    TRANSFER_VALIDATION = "transfer_validation"  # pre-trained model on untrained station (Flow 5 step 5.11 branch A)

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
    ABOVE = "above"  # alert when value > threshold (flood)
    BELOW = "below"  # alert when value < threshold (low-flow)

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
    MEASURED = "measured"                          # direct sensor reading
    RATING_CURVE_DERIVED = "rating_curve_derived"  # derived via rating curve conversion (Flow 2 step 2.5)
    MANUAL_IMPORT = "manual_import"                # CSV upload (Flow 12 Branch B, Flow 5 step 5.4)

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
    OBSERVATION_REPROCESSED = "observation_reprocessed"  # Flow 12 reprocessing event

class AuditActorType(Enum):
    USER = "user"
    API_KEY = "api_key"
    SYSTEM = "system"

class StationOwnership(Enum):
    OWN = "own"
    FOREIGN = "foreign"

class ForeignForecastStatus(Enum):
    PUBLISHED = "published"
```

---

## Domain value types

### GeoCoord

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class GeoCoord:
    lon: float
    lat: float
    altitude_masl: float | None = None      # meters above mean sea level; None if unknown

    def __post_init__(self) -> None:
        if not (-180.0 <= self.lon <= 180.0):
            raise ValueError(f"longitude {self.lon} out of range [-180, 180]")
        if not (-90.0 <= self.lat <= 90.0):
            raise ValueError(f"latitude {self.lat} out of range [-90, 90]")
```

### ParameterDefinition

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ParameterDefinition:
    name: str                              # canonical name (TEXT PK)
    display_name: str
    unit: str
    parameter_domain: ParameterDomain
    aggregation_method: AggregationMethod
    created_at: UtcDatetime
```

Module: `types/domain.py`

### DangerLevelDefinition

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class DangerLevelDefinition:
    name: str
    display_order: int
    trigger_probability: float
    resolve_probability: float
    min_trigger_duration: timedelta  # time-based, schedule-independent
    min_resolve_duration: timedelta  # time-based, schedule-independent
    direction: ThresholdDirection = ThresholdDirection.ABOVE    # ABOVE = flood, BELOW = low-flow

    # Time-based duration is schedule-independent — works correctly for both
    # 30-min observation cycles and 6-hourly forecast cycles without reconfiguration.

    def __post_init__(self) -> None:
        if not (0.0 < self.trigger_probability <= 1.0):
            raise ValueError(f"trigger_probability must be in (0, 1], got {self.trigger_probability}")
        if not (0.0 < self.resolve_probability < self.trigger_probability):
            raise ValueError(
                f"resolve_probability must be in (0, trigger_probability), "
                f"got {self.resolve_probability} >= {self.trigger_probability}"
            )
        if self.min_trigger_duration < timedelta(0):
            raise ValueError(f"min_trigger_duration must be >= 0, got {self.min_trigger_duration}")
        if self.min_resolve_duration < timedelta(0):
            raise ValueError(f"min_resolve_duration must be >= 0, got {self.min_resolve_duration}")
```

### StationThreshold

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationThreshold:
    station_id: StationId
    danger_level: str          # references DangerLevelDefinition.name
    parameter: str             # "discharge" or "water_level"
    value: float               # threshold value in parameter units
    source: ThresholdSource
    created_at: UtcDatetime
    updated_at: UtcDatetime
```

### QcFlag

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class QcFlag:
    rule_id: str               # e.g. "range_check", "rate_of_change"
    rule_version: str          # e.g. "1.0.0"
    status: QcStatus           # QC_PASSED, QC_SUSPECT, or QC_FAILED — never RAW
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status == QcStatus.RAW:
            raise ValueError("QcFlag.status cannot be RAW — RAW means QC has not run")
        if self.status == QcStatus.MISSING:
            raise ValueError("QcFlag.status cannot be MISSING — MISSING is set directly on observations, not by QC rules")


def aggregate_qc_status(flags: list[QcFlag]) -> QcStatus:
    """Derive aggregate QC status from individual flags.

    Ordering: QC_FAILED > QC_SUSPECT > QC_PASSED.
    Empty flags list after QC completes → QC_PASSED.
    """
    if not flags:
        return QcStatus.QC_PASSED
    severity = {QcStatus.QC_PASSED: 0, QcStatus.QC_SUSPECT: 1, QcStatus.QC_FAILED: 2}
    worst = max(flags, key=lambda f: severity[f.status])
    return worst.status
```

### SeasonDefinition

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class SeasonDefinition:
    name: str                  # e.g. "monsoon", "dry"
    months: frozenset[int]     # 1–12

    def __post_init__(self) -> None:
        if not self.months:
            raise ValueError("months must not be empty")
        if not all(1 <= m <= 12 for m in self.months):
            raise ValueError(f"months must be in [1, 12], got {self.months}")
```

### SkillInterpretationScheme

```python
from datetime import timedelta

@dataclass(frozen=True, kw_only=True, slots=True)
class SkillInterpretationBand:
    lower: float               # inclusive (use float('-inf') for open lower bound)
    upper: float               # exclusive (use float('inf') for open upper bound)
    label: str                 # e.g. "Very good"

@dataclass(frozen=True, kw_only=True, slots=True)
class SkillInterpretationScheme:
    metric: str                # e.g. "nse", "kge"
    time_step: timedelta       # daily vs sub-daily have different thresholds
    bands: tuple[SkillInterpretationBand, ...]  # ordered from worst to best
```

### ExceedanceResult

Intermediate output of threshold checking (Flow 1 steps 1.11–1.12, Flow 2 steps 2.8–2.9).
Consumed by the alert service to raise or resolve alerts.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ExceedanceResult:
    station_id: StationId
    danger_level: str              # references DangerLevelDefinition.name
    parameter: str                 # "discharge" or "water_level"
    threshold_value: float         # the configured threshold
    exceedance_probability: float | None  # P(forecast crosses threshold in configured direction), NULL for observation alerts
    observed_value: float | None   # observed value, NULL for forecast alerts
    exceeded: bool                 # whether the threshold was crossed in the configured direction
```

Module: `types/domain.py`

---

## Entity types

### Observation

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class RawObservation:
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str                 # canonical name
    value: float
    source: ObservationSource      # measured | rating_curve_derived | manual_import
    rating_curve_id: RatingCurveId | None = None  # v1 — set when source = RATING_CURVE_DERIVED. Omit from v0 DB schema.
    rating_curve_correction_version: str | None = None  # v1 — correction param version. Omit from v0 DB schema.

@dataclass(frozen=True, kw_only=True, slots=True)
class Observation:
    id: ObservationId
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float | None            # None when qc_status is MISSING (explicit gap record)
    source: ObservationSource      # measured | rating_curve_derived | manual_import
    rating_curve_id: RatingCurveId | None  # v1 — set when source = RATING_CURVE_DERIVED. Omit from v0 DB schema.
    rating_curve_correction_version: str | None  # v1 — correction param version. Omit from v0 DB schema.
    qc_status: QcStatus
    qc_flags: list[QcFlag]
    qc_rule_version: str | None    # version of the QC ruleset that last evaluated this row
    created_at: UtcDatetime
```

Module: `types/observation.py`

### StationConfig

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationConfig:
    id: StationId
    code: str
    name: str
    location: GeoCoord
    station_kind: StationKind
    basin_id: BasinId | None       # NULL for weather stations without basin assignment
    timezone: str                  # IANA timezone, e.g. "Asia/Kathmandu"
    regulation_type: RegulationType | None  # NULL if unknown
    forecast_target: Literal["discharge", "water_level", "both"] | None  # NULL for weather stations
    measured_parameters: frozenset[str]  # canonical parameter names
    station_status: StationStatus  # lifecycle state — Flow 1 filters to OPERATIONAL only
    created_at: UtcDatetime
    updated_at: UtcDatetime
    network: str                       # e.g., "bafu", "uk_ea", "usgs"
    ownership: StationOwnership        # own = locally managed, foreign = display-only
    wigos_id: str | None               # WMO station ID, format: 0-{country}-{network}-{local}
```

### ModelAssignment

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ModelAssignment:
    station_id: StationId
    model_id: ModelId
    time_step: timedelta           # configured time step for this assignment
    is_active: bool
    priority: int                  # fallback order: 0 = primary
    created_at: UtcDatetime
```

Priority convention: linear regression (0) > ML (1) > conceptual (2). All model types can be active for the same station simultaneously.

### StationGroup

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationGroup:
    id: StationGroupId
    name: str                      # e.g. "swiss_alpine", "nepal_koshi_basin"
    station_ids: frozenset[StationId]
    description: str | None = None
    created_at: UtcDatetime
```

Station groups define the training scope for group-scoped ML models. A station can belong to multiple groups. Groups are managed during station onboarding (Flow 5 step 5.10).

Module: `types/station.py`

### StationWeatherSource

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationWeatherSource:
    station_id: StationId
    nwp_source: str
    extraction_type: SpatialRepresentation  # POINT, BASIN_AVERAGE, or ELEVATION_BAND
    active: bool
```

Module: `types/station.py`

### Basin

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class Basin:
    id: BasinId
    code: str
    name: str
    geometry: Any                        # shapely MultiPolygon
    area_km2: float | None
    attributes: dict[str, Any] | None    # from JSONB — static catchment descriptors
    band_geometries: list[dict] | None   # elevation band definitions (computed in Flow 5 step 5.3)
    created_at: UtcDatetime
    network: str
```

Module: `types/basin.py`

### Alert

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class Alert:
    id: AlertId
    station_id: StationId | None   # NULL for system-wide pipeline alerts (e.g. NWP delivery, disk usage, backup freshness)
    source: AlertSource
    alert_level: str               # for hydrological alerts (forecast | observation): references DangerLevelDefinition.name;
                                   # for pipeline alerts: check-type identifier (e.g. "nwp_delivery", "dead_letter_queue")
    status: AlertStatus
    trigger_probability: float | None  # NULL for observation and pipeline alerts
    trigger_value: float | None    # observed or forecast value that triggered
    triggered_at: UtcDatetime
    acknowledged_at: UtcDatetime | None
    acknowledged_by: UUID | None
    resolved_at: UtcDatetime | None
    first_detected_at: UtcDatetime | None  # when exceedance first detected (before min_trigger_duration elapsed)
    notified_at: UtcDatetime | None  # NULL = notification pending
    created_at: UtcDatetime
```

Module: `types/alert.py`

### ForecastAdjustment

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastAdjustment:
    id: ForecastAdjustmentId
    forecast_id: ForecastId
    forecaster_id: UUID
    adjusted_at: UtcDatetime
    rationale: str
    adjustments: list[dict]        # envelope ops, validated via ForecastAdjustmentItem (see below)
```

#### `ForecastAdjustmentItem` (boundary schema)

Pydantic model validating each element of `ForecastAdjustment.adjustments` at the API boundary.

Individual member adjustment breaks ensemble calibration and rank statistics. Envelope
operations preserve inter-member correlation and are operationally interpretable.

```python
from pydantic import BaseModel

class ForecastAdjustmentItem(BaseModel):
    valid_time: str                                              # ISO 8601 UTC
    lead_time_hours: int
    adjustment_type: Literal["shift", "scale", "cap", "floor"]  # envelope operation
    value: float                                                 # delta for shift, factor for scale,
                                                                 # threshold for cap/floor
```

Adjustment types:
- **shift**: add constant `value` to all members/quantiles at this timestep
- **scale**: multiply all members/quantiles by `value` (factor)
- **cap**: clip all members/quantiles above `value`
- **floor**: clip all members/quantiles below `value`

Module: `schemas/forecast.py`

Module (ForecastAdjustment): `types/forecast.py`

### RatingCurve

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class RatingCurve:
    id: RatingCurveId
    station_id: StationId
    version: int
    valid_from: UtcDatetime
    valid_to: UtcDatetime | None   # NULL = currently active
    points: list[dict]             # list of {"water_level": float, "discharge": float}
    interpolation: Literal["linear", "log-linear"]
    uploaded_by: UUID | None
    created_at: UtcDatetime
```

Module: `types/rating_curve.py`

### FlowRegimeConfig

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class FlowRegimeConfig:
    id: UUID
    station_id: StationId
    q50: float                     # 50th percentile discharge (m³/s)
    q90: float                     # 90th percentile discharge (m³/s)
    computed_at: UtcDatetime
    observation_count: int
    version: int                   # monotonically increasing per station
    created_at: UtcDatetime
```

Module: `types/skill.py`

### PipelineHealthRecord

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class PipelineHealthRecord:
    check_type: PipelineCheckType
    checked_at: UtcDatetime
    status: PipelineHealthStatus
    subject: str                   # station code or NWP source name
    detail: dict                   # check-type-specific payload (validated at boundary)
    cycle_time: UtcDatetime | None
    created_at: UtcDatetime
```

Module: `types/pipeline.py`

### Auth entities

v0 defers auth — these types are defined but unused until v1. See architecture-context.md § Authentication schemas for DB table definitions.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class AccessTokenScope:
    stations: list[StationId] | None     # None = all stations
    parameters: list[str] | None         # None = all parameters
    boundary: dict | None                # GeoJSON boundary or None = no geographic restriction

@dataclass(frozen=True, kw_only=True, slots=True)
class User:
    id: UserId
    username: str                        # email address
    display_name: str
    role: UserRole
    is_active: bool
    created_at: UtcDatetime

@dataclass(frozen=True, kw_only=True, slots=True)
class AccessToken:
    id: AccessTokenId
    consumer_name: str
    scope: AccessTokenScope
    created_by: UserId
    created_at: UtcDatetime
    last_used_at: UtcDatetime | None     # NULL = never used
    revoked_at: UtcDatetime | None       # NULL = active

@dataclass(frozen=True, kw_only=True, slots=True)
class AuditEntry:
    event_type: AuditEventType
    actor_id: UserId | None              # None for system events
    actor_type: AuditActorType
    target_type: str | None
    target_id: str | None
    detail: dict | None
    ip_address: str | None
    created_at: UtcDatetime
```

Module: `types/auth.py`

---

## Ensemble and model types

### ForecastEnsemble

```python
import polars as pl

@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastEnsemble:
    representation: EnsembleRepresentation
    values: pl.DataFrame
    station_id: StationId
    issued_at: UtcDatetime
    parameter: str                 # "discharge" or "water_level" — selects the correct StationThreshold
    units: str                     # e.g. "m3/s", "m" — for display and unit-mismatch guards
    forecast_horizon_steps: int
    time_step: timedelta
```

**DataFrame column contract for `values`:**

| Column | Dtype | Present when |
|--------|-------|-------------|
| `valid_time` | `Datetime(time_zone="UTC")` | always |
| `member_id` | `Int32` | `representation == MEMBERS` |
| `quantile` | `Float64` | `representation == QUANTILES` |
| `value` | `Float64` | always |

Exactly one of `member_id` / `quantile` is present (not both). Validated by the
input preparation service and store layer at their respective boundaries.

Minimum member count: 1 (single member = deterministic forecast). However, operational
threshold evaluation requires `min_operational_ensemble_size` (deployment-configurable,
default 20). Ensembles below this threshold skip threshold evaluation and are flagged
`insufficient_ensemble_size` in forecast metadata.

Minimum quantile levels: 7 for operational use. Operational quantile sets must include
tail coverage: at least one quantile >= 0.95 and one <= 0.05. The `__post_init__` validator
on `ForecastEnsemble` enforces this constraint for operational forecasts. Models producing
fewer than 7 quantile levels or lacking tail coverage skip threshold evaluation and are
flagged in forecast metadata.

#### Construction

`ForecastEnsemble` provides two factory classmethods that validate the DataFrame column contract at construction time:

- `ForecastEnsemble.from_members(station_id, issued_at, parameter, units, time_step, values: pl.DataFrame) -> ForecastEnsemble` — validates: `member_id` column present and `int` dtype, `quantile` column absent, `valid_time` column present and `Datetime` dtype, `value` column present and `Float64` dtype, at least 1 member. Sets `representation = MEMBERS`.

- `ForecastEnsemble.from_quantiles(station_id, issued_at, parameter, units, time_step, values: pl.DataFrame) -> ForecastEnsemble` — validates: `quantile` column present and `Float64` dtype, `member_id` column absent, `valid_time` column present, `value` column present, at least 7 quantile levels with tail coverage (min <= 0.05, max >= 0.95). Sets `representation = QUANTILES`.

Both raise `ValueError` with a descriptive message on validation failure. The standard constructor always runs `__post_init__` validation. For store-layer reconstruction of already-validated data, the validation is idempotent and cheap — no bypass is needed.

### ModelInputs

```python
import xarray as xr

@dataclass(frozen=True, kw_only=True, slots=True)
class ModelInputs:
    station_id: StationId           # identifies the station — used by ML models for station embeddings
    forcing: pl.DataFrame | xr.Dataset
    observations: pl.DataFrame
    static_attributes: pl.DataFrame | None  # scalar catchment descriptors from basins.attributes
    issue_time: UtcDatetime
    forecast_horizon_steps: int
    time_step: timedelta
    warm_up_steps: int | None       # conceptual/hybrid models only; None for pure ML
```

**`forcing` column contract** (when `pl.DataFrame`):
- Columns: `timestamp` (Datetime UTC) + one column per canonical parameter name.
  For elevation-band models, parameter columns are band-qualified: `precipitation_band_1`,
  `temperature_band_2`, etc. Rows = timesteps covering full input window (lookback +
  forecast horizon for ML; warm-up + forecast for conceptual).

**`forcing` schema** (when `xr.Dataset`):
- Dimensions: `time × parameter × y × x`. For GRIDDED models only.

**`observations` column contract:**
- Columns: `timestamp` (Datetime UTC) + observed parameters (`discharge`, `water_level`).
  Rows = timesteps covering the lookback / warm-up period. Always tabular regardless of
  model spatial type.

Models must not use data after `issue_time` from `observations`.

**`static_attributes` column contract** (when `pl.DataFrame`):
- One column per attribute name (e.g. `mean_elev_m`, `mean_slope`, `forest_fraction`).
  Single row. Values are `Float64`. Sourced from `basins.attributes` JSONB during
  input preparation (Flow 1 step 1.7, Flow 6/9 step T.2, Flow 7 step H.4).
- `None` when the model declares no `required_static_attributes` or the station's
  basin has no attributes.
- **Future extension**: gridded static attributes (DEM rasters, soil type grids) will
  use a separate `static_grids: xr.Dataset | None` field — not this one. Scalar and
  gridded statics remain distinct types.

### TrainingData

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class TrainingData:
    forcing: pl.DataFrame
    observations: pl.DataFrame
    targets: pl.DataFrame
    static_attributes: pl.DataFrame | None  # scalar catchment descriptors — same contract as ModelInputs
    time_step: timedelta
    val_start: UtcDatetime | None   # if set, data after this time is validation holdout
```

**`targets` column contract:**
- Columns: `timestamp` (Datetime UTC) + target parameters (`discharge`, `water_level`).

`forcing` and `observations` follow the same contracts as `ModelInputs`.
Training always uses tabular forcing (`pl.DataFrame`), not gridded.

### GroupTrainingData

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class GroupTrainingData:
    group_id: StationGroupId
    station_data: dict[StationId, TrainingData]  # one TrainingData per station in the group
    time_step: timedelta
    val_start: UtcDatetime | None   # group-wide validation split
```

Used by group-scoped ML models. The model receives training data for all stations in the group in a single `train()` call. Each station's `TrainingData` follows the same contracts as single-station training. The model uses `StationId` keys for station embeddings / identification.

Module: `types/model.py`

### ModelParams

```python
ModelParams = dict[str, Any]
```

Opaque at the Protocol level. Each model implementation defines its expected keys.
Validated by the model's `train()` method.

### ModelArtifact

```python
ModelArtifact = Any
```

Opaque to the system. Could be neural network weights, calibrated parameters, or any
model-specific state. Must round-trip through `serialize_artifact()` / `deserialize_artifact()`.

Module: `types/ensemble.py`, `types/model.py`

### ModelRecord

Corresponds to the `models` DB table — the persistent identity of a registered model.
Used by `ModelStore` for CRUD operations.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRecord:
    id: ModelId                       # TEXT PK — entry point name
    display_name: str
    artifact_scope: ArtifactScope     # STATION or GROUP
    description: str | None
    created_at: UtcDatetime
```

Module: `types/model.py`

### ModelRegistryEntry

Runtime metadata for a registered model — includes features, spatial input type,
and supported time steps needed by the pipeline. Superset of `ModelRecord`.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRegistryEntry:
    id: ModelId                       # TEXT PK — entry point name
    display_name: str
    description: str
    artifact_scope: ArtifactScope     # STATION or GROUP — determines training and artifact granularity
    required_features: frozenset[str]
    required_static_attributes: frozenset[str]  # empty frozenset if none needed
    spatial_input_type: SpatialRepresentation
    supported_time_steps: frozenset[timedelta]
    registered_at: UtcDatetime
```

Module: `types/model.py`

### ModelArtifactRecord

Represents the `model_artifacts` DB row — metadata about a trained artifact (status,
training period, promotion audit trail). Distinct from `ModelArtifact = Any`, which is
the opaque serialized model blob.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ModelArtifactRecord:
    id: ArtifactId
    model_id: ModelId
    station_id: StationId | None       # non-null for station-scoped models
    group_id: StationGroupId | None    # non-null for group-scoped models
    status: ModelArtifactStatus
    artifact_path: str                 # relative path to serialized artifact file
    training_period_start: UtcDatetime
    training_period_end: UtcDatetime
    trained_at: UtcDatetime
    promoted_at: UtcDatetime | None    # when status changed to ACTIVE
    promoted_by: UUID | None           # model admin who approved
    superseded_at: UtcDatetime | None  # when a newer artifact replaced this one
    created_at: UtcDatetime
```

Module: `types/model.py`

### OperationalForecast

Wraps the `forecasts` + `forecast_values` join. Contains a `ForecastEnsemble` for the
values payload.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalForecast:
    id: ForecastId
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId
    issued_at: UtcDatetime
    nwp_cycle_reference_time: UtcDatetime
    nwp_cycle_is_fallback: bool
    representation: EnsembleRepresentation
    status: ForecastStatus
    version: int                           # optimistic locking
    warm_up_source: WarmUpSource | None    # NULL for ML models
    warm_up_state_age_hours: float | None
    observation_staleness_hours: float | None
    ensemble: ForecastEnsemble             # the values payload
    created_at: UtcDatetime
    updated_at: UtcDatetime
```

### HindcastForecast

Wraps `hindcast_forecasts` + `hindcast_values`. No publication lifecycle.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class HindcastForecast:
    id: HindcastForecastId
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId
    hindcast_step: UtcDatetime             # the simulated issue time
    forcing_type: ForcingType
    representation: EnsembleRepresentation
    hindcast_run_id: UUID                  # groups all steps of one hindcast execution
    ensemble: ForecastEnsemble             # the values payload
    created_at: UtcDatetime
```

Module: `types/forecast.py`

### ForeignForecast

Published forecast pulled from an upstream SAPPHIRE instance for transboundary display.
DB tables deferred — types and protocols only for v0 (see `v0-scope.md` §B).

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ForeignForecast:
    id: ForeignForecastId
    station_id: StationId              # must reference a foreign-owned station
    upstream_instance_url: str         # e.g., "https://sapphire.kyrgyzstan.gov"
    upstream_station_id: str           # upstream's UUID as string (not our StationId)
    upstream_forecast_id: str          # upstream's forecast UUID as string
    issued_at: UtcDatetime
    valid_from: UtcDatetime            # denormalized from ensemble for range queries
    valid_to: UtcDatetime
    representation: EnsembleRepresentation
    status: ForeignForecastStatus
    ensemble: ForecastEnsemble
    fetched_at: UtcDatetime            # when we pulled it (staleness detection)
    created_at: UtcDatetime
```

Module: `types/forecast.py`

### WeatherForecastRecord

Matches the `weather_forecasts` table — one row per NWP value.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class WeatherForecastRecord:
    id: UUID
    station_id: StationId
    nwp_source: str
    cycle_time: UtcDatetime
    valid_time: UtcDatetime
    parameter: str
    spatial_type: SpatialRepresentation
    band_id: int | None                    # non-null when spatial_type == ELEVATION_BAND
    member_id: int | None
    value: float
    is_gap: bool                           # v1 (Flow 11) — omit from v0 DB schema
    gap_status: Literal["recovered", "unrecoverable"] | None  # v1 (Flow 11) — omit from v0 DB schema
    created_at: UtcDatetime
```

Module: `types/weather.py`

### SkillScore

Narrow/tall design — one row per metric per stratum.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class SkillScore:
    id: UUID
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId
    skill_source: SkillSource
    forcing_type: ForcingType | None       # NULL for operational
    computation_version: int
    computed_at: UtcDatetime
    lead_time_hours: int
    season: str | None                     # NULL = all-season
    flow_regime: FlowRegime | None         # NULL = all-regime
    flow_regime_config_id: UUID | None     # FK → flow_regime_configs.id (NULL when flow_regime is NULL)
    metric: str                            # e.g. "crps", "nse", "kge", "bss", "sharpness_p10_p90", "sharpness_p25_p75", "ensemble_range"
    score: float
    sample_size: int
    is_stale: bool                         # TRUE when underlying data changed; cleared by Flow 10 step S.6
    created_at: UtcDatetime
```

### SkillDiagram

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class SkillDiagram:
    id: UUID
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId
    skill_source: SkillSource
    computation_version: int
    lead_time_hours: int
    season: str | None
    flow_regime: FlowRegime | None
    flow_regime_config_id: UUID | None     # FK → flow_regime_configs.id (NULL when flow_regime is NULL)
    diagram_type: Literal["reliability", "roc", "rank_histogram"]
    threshold_level: str | None            # danger level name (for ROC/BSS diagrams)
    data: dict                             # diagram-specific structure (validated at boundary)
    created_at: UtcDatetime
```

Module: `types/skill.py`

---

## Protocols

### ForecastModel

Moved from architecture-context.md "Model Protocol" section.

Two Protocols split by artifact scope. `StationForecastModel` predicts per-station; `GroupForecastModel` predicts in batch via `predict_batch()`. Both share `serialize_artifact()` and `deserialize_artifact()`.

```python
from typing import Protocol, Any, runtime_checkable
from datetime import timedelta
import random

@runtime_checkable
class StationForecastModel(Protocol):
    """Model trained independently per station (conceptual models like GR4J, HBV)."""
    artifact_scope: ArtifactScope          # must be ArtifactScope.STATION
    required_features: frozenset[str]
    required_static_attributes: frozenset[str]  # e.g. {"mean_elev_m", "mean_slope"} — empty if none needed
    spatial_input_type: SpatialRepresentation
    supported_time_steps: frozenset[timedelta]

    def train(self, data: TrainingData, params: ModelParams, rng: random.Random) -> ModelArtifact: ...
    def predict(
        self,
        artifact: ModelArtifact,
        inputs: ModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[ForecastEnsemble, bytes | None]: ...
    def serialize_artifact(self, artifact: ModelArtifact) -> bytes: ...
    def deserialize_artifact(self, raw: bytes) -> ModelArtifact: ...

@runtime_checkable
class GroupForecastModel(Protocol):
    """Model trained on a group of stations (ML models like LSTM, transformer)."""
    artifact_scope: ArtifactScope          # must be ArtifactScope.GROUP
    required_features: frozenset[str]
    required_static_attributes: frozenset[str]  # e.g. {"mean_elev_m", "area_km2"} — empty if none needed
    spatial_input_type: SpatialRepresentation
    supported_time_steps: frozenset[timedelta]

    def train(self, data: GroupTrainingData, params: ModelParams, rng: random.Random) -> ModelArtifact: ...
    def predict_batch(
        self,
        artifact: ModelArtifact,
        inputs: dict[StationId, ModelInputs],
        rng: random.Random,
    ) -> dict[StationId, tuple[ForecastEnsemble, bytes | None]]: ...
    def serialize_artifact(self, artifact: ModelArtifact) -> bytes: ...
    def deserialize_artifact(self, raw: bytes) -> ModelArtifact: ...

# Union type for the orchestration layer to dispatch on
ForecastModel = StationForecastModel | GroupForecastModel
```

Models are pure functions — no DB, no I/O. Artifact serialization is the model's
responsibility; artifact *persistence* (reading/writing files) is the caller's.

**Key difference — training**: `StationForecastModel.train()` receives single-station
`TrainingData`; `GroupForecastModel.train()` receives `GroupTrainingData` with data for all
stations in the group. The orchestration layer (Flow 6/9 T.2–T.3) checks `artifact_scope`
to dispatch.

**Key difference — prediction**: Orchestration dispatches on `artifact_scope`:
- *Station models* → `predict()` per station. Accepts optional `prior_state` (opaque bytes
  from a previous run). Models that maintain internal state (conceptual, hybrid) return
  `(ensemble, updated_state)`. Stateless models return `(ensemble, None)`.
- *Group models* → single `predict_batch()` call per (model, group). Receives
  `dict[StationId, ModelInputs]`, returns `dict[StationId, tuple[ForecastEnsemble, bytes | None]]`.
  `ModelInputs` includes `station_id` so ML models can use station embeddings. No `prior_state`
  input — ML models are stateless. A single-station group is a single-entry dict (no special case).

The caller persists state via `ModelStateStore`.

Module: `protocols/forecast_model.py`

---

### Store Protocols

One Protocol per entity. All store methods accept and return domain types, never raw
dicts or ORM objects. Methods that accept time ranges use `UtcDatetime`.

Behavioral conventions (apply to all stores unless noted):
- **Not found**: methods returning a single entity return `T | None`. Never raise on missing data.
- **Empty results**: methods returning collections return empty `list[]`. Never raise on no matches.
- **Conflict**: `ForecastStore.transition_status()` raises `ConflictError` when version doesn't match.
- **Idempotent writes**: `store_*` methods use upsert semantics where a natural key exists (e.g. station_id + cycle + model for forecasts). Re-storing the same data is a no-op.

```python
# Module: exceptions.py

class SapphireError(Exception):
    """Base for all SAPPHIRE Flow domain errors."""

class InsufficientDataError(SapphireError):
    """Not enough input data to run a model or service function.
    Flow-level handling: try fallback model."""

class SanityCheckFailure(SapphireError):
    """Model output failed plausibility checks.
    Flow-level handling: try fallback model."""

class ModelLoadError(SapphireError):
    """Failed to deserialize or load a model artifact.
    Flow-level handling: try fallback model."""

class ConflictError(SapphireError):
    """Optimistic locking detected a concurrent modification.
    API-level handling: return 409 Conflict."""

class AdapterError(SapphireError):
    """External data source returned an error or timed out.
    Flow-level handling: retry (via Prefect @task retries), then fallback."""

class ConfigurationError(SapphireError):
    """Invalid or missing configuration.
    Startup-level handling: fail fast with clear message."""
```

#### ObservationStore

```python
class ObservationStore(Protocol):
    def store_observations(self, observations: list[Observation]) -> None: ...
    def store_raw_observations(self, observations: list[RawObservation]) -> list[ObservationId]: ...
        # Inserts raw observations (pre-QC) with qc_status=RAW. Returns assigned IDs.
    def update_qc(self, observation_id: ObservationId, qc_status: QcStatus, qc_flags: list[QcFlag]) -> None: ...
    def fetch_observations(
        self,
        station_id: StationId,
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | None = None,  # None = all statuses
        source: ObservationSource | None = None,  # None = all sources
    ) -> list[Observation]: ...
    def fetch_latest_timestamp(self, station_id: StationId, parameter: str) -> UtcDatetime | None: ...
    def fetch_observations_batch(
        self,
        station_ids: list[StationId],
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        qc_status: QcStatus | None = None,
        source: ObservationSource | None = None,
    ) -> dict[StationId, list[Observation]]: ...
        # Multi-station fetch. Returns dict keyed by station_id.
    def fetch_derived_observations_by_curve(
        self,
        station_id: StationId,
        rating_curve_id: RatingCurveId,
    ) -> list[Observation]: ...
        # v1 — Fetches all RATING_CURVE_DERIVED observations for a specific curve.
        # Used by Flow 12 Branch A to find observations that need reprocessing.
        # Not implemented in v0 (no rating curves). See v0-scope.md §B.
```

#### ForecastStore

```python
class ForecastStore(Protocol):
    def store_forecast(self, forecast: OperationalForecast) -> ForecastId: ...
    def fetch_forecast(self, forecast_id: ForecastId) -> OperationalForecast | None: ...
    def fetch_latest_forecast(
        self,
        station_id: StationId,
        model_id: ModelId | None = None,  # None = any model
    ) -> OperationalForecast | None: ...
    def fetch_forecasts_for_cycle(
        self,
        issued_at: UtcDatetime,
        station_id: StationId | None = None,  # None = all stations
    ) -> list[OperationalForecast]: ...
    def transition_status(
        self,
        forecast_id: ForecastId,
        expected_version: int,
        new_status: ForecastStatus,
    ) -> int: ...
        # Returns new version number. Raises ConflictError if expected_version doesn't match.
    def fetch_forecasts_in_range(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        model_id: ModelId | None = None,
        status: ForecastStatus | None = None,
    ) -> list[OperationalForecast]: ...
```

#### HindcastStore

```python
class HindcastStore(Protocol):
    def store_hindcast(self, hindcast: HindcastForecast) -> HindcastForecastId: ...
    def fetch_hindcasts(
        self,
        station_id: StationId,
        model_id: ModelId,
        start: UtcDatetime,
        end: UtcDatetime,
        forcing_type: ForcingType | None = None,  # None = all types
        hindcast_run_id: UUID | None = None,       # None = all runs
    ) -> list[HindcastForecast]: ...
```

#### WeatherForecastStore

```python
class WeatherForecastStore(Protocol):
    def store_weather_forecasts(self, records: list[WeatherForecastRecord]) -> None: ...
    def fetch_weather_forecasts(
        self,
        station_id: StationId,
        nwp_source: str,
        cycle_time: UtcDatetime,
        parameters: list[str] | None = None,  # None = all parameters
    ) -> list[WeatherForecastRecord]: ...
    def fetch_lookback(
        self,
        station_id: StationId,
        nwp_source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[WeatherForecastRecord]: ...
    def fetch_received_cycles(
        self,
        nwp_source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> list[UtcDatetime]: ...
        # Returns distinct cycle_times received in the given range. Used by Flow 4 for gap detection.
    def mark_gap(self, station_id: StationId, nwp_source: str, cycle_time: UtcDatetime, recoverable: bool) -> None: ...
    def fetch_latest_cycle_time(
        self,
        nwp_source: str,
    ) -> UtcDatetime | None: ...
        # Returns the most recent cycle_time for a given NWP source, or None.
```

#### AlertStore

```python
class AlertStore(Protocol):
    def upsert_alert(self, alert: Alert) -> AlertId: ...
        # Upsert keyed on (station_id, alert_level, source) for active alerts.
    def fetch_active_alerts(
        self,
        station_id: StationId | None = None,  # None = all stations
        source: AlertSource | None = None,
    ) -> list[Alert]: ...
    def resolve_alert(self, alert_id: AlertId) -> None: ...
    def acknowledge_alert(self, alert_id: AlertId, acknowledged_by: UUID) -> None: ...
    def fetch_alert_history(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        source: AlertSource | None = None,
    ) -> list[Alert]: ...
        # Returns all alerts (including resolved) in the time range.
```

#### SkillStore

```python
class SkillStore(Protocol):
    def store_skill_scores(self, scores: list[SkillScore]) -> None: ...
    def store_skill_diagrams(self, diagrams: list[SkillDiagram]) -> None: ...
    def fetch_latest_scores(
        self,
        station_id: StationId,
        model_id: ModelId,
        skill_source: SkillSource | None = None,
    ) -> list[SkillScore]: ...
        # Returns scores for the latest computation_version.
    def fetch_latest_diagrams(
        self,
        station_id: StationId,
        model_id: ModelId,
        diagram_type: Literal["reliability", "roc", "rank_histogram"] | None = None,
    ) -> list[SkillDiagram]: ...
    def fetch_scores_by_regime(
        self,
        station_id: StationId,
        model_id: ModelId,
        flow_regime: FlowRegime,
    ) -> list[SkillScore]: ...
    def mark_stale(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> int: ...
        # Sets is_stale=TRUE on all skill_scores rows for this station
        # whose evaluation period overlaps [start, end].
        # Returns count of rows marked stale.
        # Used by Flows 11 and 12 when underlying data changes.
```

#### ModelArtifactStore

```python
class ModelArtifactStore(Protocol):
    def store_artifact(
        self,
        model_id: ModelId,
        artifact_bytes: bytes,
        training_period_start: UtcDatetime,
        training_period_end: UtcDatetime,
        trained_at: UtcDatetime,
        *,
        station_id: StationId | None = None,   # for station-scoped models
        group_id: StationGroupId | None = None, # for group-scoped models
    ) -> ArtifactId: ...
        # Exactly one of station_id or group_id must be provided.
    def fetch_artifact(self, artifact_id: ArtifactId) -> tuple[ArtifactId, bytes] | None: ...
    def fetch_active_artifact(
        self,
        model_id: ModelId,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> tuple[ArtifactId, bytes] | None: ...
        # Station-scoped: pass station_id. Group-scoped: pass group_id.
        # Returns (id, serialized bytes) for the ACTIVE artifact, or None.
    def fetch_active_artifact_for_station(
        self,
        station_id: StationId,
        model_id: ModelId,
    ) -> tuple[ArtifactId, bytes] | None: ...
        # Convenience: resolves station's group membership for group-scoped models.
        # Checks station-scoped first, then group-scoped. Used by Flow 1 step 1.7.
    def fetch_artifact_record(self, artifact_id: ArtifactId) -> ModelArtifactRecord | None: ...
        # Returns full metadata record (status, training period, audit trail).
        # Use when you need metadata without deserializing the artifact bytes.
    def fetch_artifacts_by_status(
        self,
        model_id: ModelId,
        status: ModelArtifactStatus,
        *,
        station_id: StationId | None = None,
        group_id: StationGroupId | None = None,
    ) -> list[ArtifactId]: ...
    def transition_artifact_status(
        self,
        artifact_id: ArtifactId,
        new_status: ModelArtifactStatus,
        promoted_by: UUID | None = None,
    ) -> None: ...
        # Handles ACTIVE → SUPERSEDED on the old artifact when promoting a new one.
```

#### ModelStore

```python
class ModelStore(Protocol):
    def register_model(self, record: ModelRecord) -> None: ...
        # Upsert keyed on record.id.
    def fetch_model(self, model_id: ModelId) -> ModelRecord | None: ...
    def fetch_all_models(self) -> list[ModelRecord]: ...
```

#### ModelStateStore

```python
class ModelStateStore(Protocol):
    def store_state(
        self,
        station_id: StationId,
        model_id: ModelId,
        issue_time: UtcDatetime,
        state_bytes: bytes,
    ) -> None: ...
    def fetch_latest_state(
        self,
        station_id: StationId,
        model_id: ModelId,
    ) -> tuple[UtcDatetime, bytes] | None: ...
        # Returns (issue_time, state_bytes) of the most recent state, or None.
```

#### StationStore

```python
class StationStore(Protocol):
    def fetch_station(self, station_id: StationId) -> StationConfig | None: ...
    def fetch_station_by_code(self, code: str, network: str) -> StationConfig | None: ...
    def fetch_all_stations(self, kind: StationKind | None = None) -> list[StationConfig]: ...
    def fetch_stations_by_ownership(
        self, ownership: StationOwnership, kind: StationKind | None = None,
    ) -> list[StationConfig]: ...
    def store_station(self, station: StationConfig) -> StationId: ...
    def fetch_thresholds(self, station_id: StationId) -> list[StationThreshold]: ...
    def store_thresholds(self, thresholds: list[StationThreshold]) -> None: ...
        # Upsert keyed on (station_id, danger_level, parameter).
    def fetch_model_assignments(self, station_id: StationId) -> list[ModelAssignment]: ...
    def store_model_assignment(self, assignment: ModelAssignment) -> None: ...
        # Upsert keyed on (station_id, model_id).
    def fetch_weather_sources(self, station_id: StationId) -> list[StationWeatherSource]: ...
    def store_weather_source(self, source: StationWeatherSource) -> None: ...
        # Upsert keyed on (station_id, nwp_source).
```

#### StationGroupStore

```python
class StationGroupStore(Protocol):
    def store_group(self, name: str, station_ids: frozenset[StationId]) -> StationGroupId: ...
        # Upsert keyed on name. Replaces membership.
    def fetch_group(self, group_id: StationGroupId) -> StationGroup | None: ...
    def fetch_group_by_name(self, name: str) -> StationGroup | None: ...
    def fetch_groups_for_station(self, station_id: StationId) -> list[StationGroup]: ...
        # All groups this station belongs to.
    def fetch_groups_for_model(self, model_id: ModelId) -> list[StationGroup]: ...
        # All groups that have an active artifact for this model.
    def add_station_to_group(self, group_id: StationGroupId, station_id: StationId) -> None: ...
    def remove_station_from_group(self, group_id: StationGroupId, station_id: StationId) -> None: ...
```

#### PipelineHealthStore

```python
class PipelineHealthStore(Protocol):
    def append_health_record(self, record: PipelineHealthRecord) -> None: ...
    def fetch_recent(
        self,
        check_type: PipelineCheckType | None = None,
        limit: int = 100,
    ) -> list[PipelineHealthRecord]: ...
```

#### RatingCurveStore

```python
class RatingCurveStore(Protocol):
    def store_rating_curve(self, curve: RatingCurve) -> RatingCurveId: ...
    def fetch_active_curve(self, station_id: StationId) -> RatingCurve | None: ...
        # Returns the curve with valid_to IS NULL, or None.
    def fetch_curve_at(self, station_id: StationId, at: UtcDatetime) -> RatingCurve | None: ...
        # Returns the curve valid at the given time (valid_from <= at < valid_to).
    def supersede_curve(self, curve_id: RatingCurveId, valid_to: UtcDatetime) -> None: ...
        # Sets valid_to on the current active curve when a new one is uploaded.
```

#### FlowRegimeConfigStore

```python
class FlowRegimeConfigStore(Protocol):
    def store_config(self, config: FlowRegimeConfig) -> None: ...
    def fetch_latest(self, station_id: StationId) -> FlowRegimeConfig | None: ...
        # Returns the config with the highest version for this station.
```

#### ForecastAdjustmentStore

```python
class ForecastAdjustmentStore(Protocol):
    def store_adjustment(self, adjustment: ForecastAdjustment) -> ForecastAdjustmentId: ...
        # Append-only — no update or delete.
    def fetch_adjustments(self, forecast_id: ForecastId) -> list[ForecastAdjustment]: ...
        # Returns all adjustments for a forecast, ordered by adjusted_at.
```

#### ForeignForecastStore

```python
@runtime_checkable
class ForeignForecastStore(Protocol):
    def store_foreign_forecast(self, forecast: ForeignForecast) -> ForeignForecastId: ...
    def fetch_foreign_forecast(self, forecast_id: ForeignForecastId) -> ForeignForecast | None: ...
    def fetch_latest_foreign_forecast(self, station_id: StationId) -> ForeignForecast | None: ...
    def fetch_foreign_forecasts_in_range(
        self, station_id: StationId, start: UtcDatetime, end: UtcDatetime,
    ) -> list[ForeignForecast]: ...
```

Module: `protocols/stores.py`

#### BasinStore

```python
class BasinStore(Protocol):
    def fetch_basin(self, basin_id: BasinId) -> Basin | None: ...
    def fetch_basin_by_code(self, code: str, network: str) -> Basin | None: ...
    def fetch_all_basins(self) -> list[Basin]: ...
    def store_basin(self, basin: Basin) -> BasinId: ...
```

#### ParameterStore

```python
class ParameterStore(Protocol):
    def fetch_all(self) -> list[ParameterDefinition]: ...
    def fetch_by_name(self, name: str) -> ParameterDefinition | None: ...
```

Module: `protocols/stores.py` (all store Protocols in one module — they share `ConflictError`).

---

### Adapter Protocols

Adapters perform external I/O and return domain types. They never call services or stores.

#### WeatherForecastSource

```python
class WeatherForecastSource(Protocol):
    def fetch_forecasts(
        self,
        station_configs: list[StationWeatherSource],
        cycle_time: UtcDatetime,
    ) -> GriddedForecast | dict[StationId, WeatherForecastResult]: ...
        # Gridded sources return a single GriddedForecast (passed to GridExtractor for bulk extraction).
        # Pre-extracted sources (Data Gateway, point stations) return station-keyed dict (also fetched in bulk).
```

Two return paths:
- **Gridded NWP** (e.g. ICON-CH2-EPS, ECMWF IFS): returns `GriddedForecast`. The flow layer
  passes this to `GridExtractor.extract()` which bulk-extracts all stations from one grid read.
- **Pre-extracted** (e.g. Data Gateway, point weather stations): returns
  `dict[StationId, WeatherForecastResult]`. Already station-keyed; no extraction step needed.

**Raw NWP grid** (pre-extraction, not per-station):

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class GriddedForecast:
    nwp_source: str
    cycle_time: UtcDatetime
    values: xr.Dataset             # dimensions: time × parameter × y × x
```

`GriddedForecast` is the raw fetch output before extraction. It is not station-keyed —
it represents a single NWP grid that the `GridExtractor` processes for all stations at once.

**Extracted/fetched results** (station-keyed, produced in bulk):

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class PointForecast:
    nwp_source: str
    cycle_time: UtcDatetime
    values: pl.DataFrame           # columns: valid_time, parameter, member_id|quantile, value

@dataclass(frozen=True, kw_only=True, slots=True)
class BasinAverageForecast:
    nwp_source: str
    cycle_time: UtcDatetime
    values: pl.DataFrame           # columns: valid_time, parameter, member_id|quantile, value

@dataclass(frozen=True, kw_only=True, slots=True)
class ElevationBandForecast:
    nwp_source: str
    cycle_time: UtcDatetime
    values: pl.DataFrame           # columns: valid_time, parameter, band_id, member_id|quantile, value

WeatherForecastResult = PointForecast | BasinAverageForecast | ElevationBandForecast
```

`station_id` is not carried inside these types — bulk operations return
`dict[StationId, WeatherForecastResult]` and station identity lives in the dict key.

Defined in `types/weather.py`.

#### StationDataSource

```python
class StationDataSource(Protocol):
    def fetch_observations(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],  # last-seen timestamp per station
    ) -> list[RawObservation]: ...
        # Returns raw observations (pre-QC). Empty list if no new data.
```

`RawObservation` and `Observation` are defined in the Entity types section above.

#### WeatherReanalysisSource

Retained for v1 (Nepal / ERA5-Land). Not implemented in v0.

```python
class WeatherReanalysisSource(Protocol):
    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> dict[StationId, BasinAverageForecast | ElevationBandForecast]: ...
        # Station-keyed dict. Reanalysis is always pre-extracted (no gridded path).
```

#### ForeignForecastSource

Pulls published forecasts from an upstream SAPPHIRE instance. Implementation deferred to v1.

```python
@runtime_checkable
class ForeignForecastSource(Protocol):
    def fetch_published_forecasts(
        self,
        upstream_station_ids: list[str],
        since: UtcDatetime,
    ) -> list[ForeignForecast]: ...
```

Module: `protocols/adapters.py`

#### PipelineStatusSource

Used by Flow 4 step 4.4 to query flow run health. Abstracts the Prefect API behind a Protocol for testability.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class FlowRunStatus:
    flow_name: str
    run_id: str
    state: str                    # e.g. "COMPLETED", "FAILED", "CRASHED", "RUNNING"
    started_at: UtcDatetime | None
    duration_seconds: float | None
    error_message: str | None

@runtime_checkable
class PipelineStatusSource(Protocol):
    def fetch_recent_runs(
        self,
        flow_names: list[str],
        since: UtcDatetime,
    ) -> list[FlowRunStatus]: ...
```

Production implementation wraps the Prefect client. Tests inject a `FakePipelineStatusSource` returning pre-configured states.

`FlowRunStatus` defined in `types/pipeline.py`. Protocol defined in `protocols/adapters.py`.

Module: `protocols/adapters.py`

#### GridExtractor

Bulk extraction from a raw NWP grid for all stations at once. One grid read, all station geometries processed.

```python
@runtime_checkable
class GridExtractor(Protocol):
    def extract(
        self,
        grid: xr.Dataset,
        configs: list[StationWeatherSource],
        basins: dict[StationId, Basin],
        cycle_time: UtcDatetime,
        nwp_source: str,
    ) -> dict[StationId, BasinAverageForecast | ElevationBandForecast]: ...
        # Geometry comes from basins dict: basin.geometry for basin-average,
        # basin.band_geometries for elevation-band. configs carries extraction_type
        # to dispatch. POINT configs are filtered out before calling GridExtractor.
        # Mixed extraction types handled in one grid read.
```

Module: `protocols/grid_extractor.py`

---

### NotificationAdapter

```python
class NotificationAdapter(Protocol):
    def send(
        self,
        channel: NotificationChannel,
        recipients: list[str],         # email addresses, phone numbers, or webhook URLs
        subject: str,
        body: str,
        alert: Alert | None = None,    # structured alert data for rich formatting
    ) -> None: ...
        # Raises on permanent failure. Transient failures are retried by the sweep task.
```

Module: `protocols/notification.py`

---

## DeploymentConfig

Loaded from `config.toml` at startup. Pydantic model (system boundary).

```python
from pydantic import BaseModel

class DeploymentConfig(BaseModel):
    # --- Danger levels ---
    danger_levels: list[DangerLevelDefinition]

    # --- Seasons ---
    seasons: list[SeasonDefinition]

    # --- Skill interpretation ---
    skill_interpretation: list[SkillInterpretationScheme]

    # --- Data retention ---
    # Tiered: hot (PostgreSQL / object store) → cold (Parquet) → delete at max_retention_days.
    # Total lifetime = max_retention_days from creation. Hot window per data class.
    weather_hot_days: int = 180              # weather data hot window (raw grids + extracted NWP + observations)
    forecast_hot_days: int = 548             # runoff forecast + hindcast hot window (~1.5 years)
    max_retention_days: int                  # total lifetime for all data (no default — must be set)
    # Constraint: max_retention_days > forecast_hot_days (validated at load time)

    # --- Observation staleness ---
    observation_staleness_warning_hours: float = 6.0  # Flow 1 step 1.6 warning threshold

    # --- NWP lateness ---
    nwp_max_wait_hours: float = 3.0            # max wait for expected NWP delivery
    nwp_max_fallback_age_hours: float = 12.0   # max age of fallback NWP cycle

    # --- Warm-up state (conceptual models) ---
    warm_up_snapshot_max_age_hours: float = 48.0  # default; season-dependent override below
    warm_up_snapshot_max_age_monsoon_hours: float = 24.0  # shorter during wet season

    # --- Flow regime ---
    flow_regime_q50_percentile: float = 50.0   # customizable percentile boundary
    flow_regime_q90_percentile: float = 90.0

    # --- Per-source alert enablement (v0-scope.md §A8c) ---
    # Per-source flags allow incremental activation: pipeline alerts first,
    # then observation alerts, then forecast alerts. All default false for v0.
    enable_forecast_alerts: bool = False         # gates Flow 1 Phase C (steps 1.11-1.13)
    enable_observation_alerts: bool = False      # gates Flow 2 steps 2.8-2.10
    enable_pipeline_alerts: bool = False         # gates Flow 4 steps 4.6-4.7
    threshold_check_mode: Literal["raw", "published", "both"] = "raw"  # v0: raw only

    # --- Threshold inference ---
    infer_missing_thresholds: bool = False      # v1+ only

    # --- Skill promotion ---
    min_skill_samples: int = 100               # minimum forecast-observation pairs
    min_skill_seasons: int = 2                 # must cover wet + dry

    # --- Display ---
    default_display_timezone: str = "UTC"      # IANA timezone for API/dashboard default

    # --- Calendar ---
    calendar: Literal["gregorian", "bikram_sambat"] = "gregorian"
    # Nepal uses Bikram Sambat for official reporting. When configured, API and
    # bulletin generation convert Gregorian dates to BS for display. Internal
    # storage remains UTC Gregorian.
```

This is the deployment-wide config. Adapter-specific config (adapter types, cache ages,
archive flags) lives in the `[adapters]` section of `config.toml` and is loaded separately
by each adapter — not part of `DeploymentConfig`.

Module: `config/deployment.py`

### Dependency injection rule

`DeploymentConfig` is always passed as a parameter to service functions and flow tasks — never imported as a module-level singleton. Loading from `config.toml` happens once at application startup (in `flows/` or `api/` entrypoints) and the resulting object is threaded through all calls.

Pattern:
```python
# WRONG — untestable
from sapphire_flow.config import get_config
def check_thresholds(ensemble: ForecastEnsemble) -> list[ExceedanceResult]:
    config = get_config()  # hidden dependency
    ...

# RIGHT — testable
def check_thresholds(ensemble: ForecastEnsemble, config: DeploymentConfig) -> list[ExceedanceResult]:
    ...
```

Tests construct `DeploymentConfig(...)` directly with only the fields relevant to the behavior under test.

---

## Test fakes

Test fakes are first-class infrastructure — in-memory implementations of Store and Adapter Protocols used by service-layer unit tests. Fakes are stored in `tests/fakes/` and are `runtime_checkable`-verified against their Protocol in their own test file.

### Directory structure

```
tests/fakes/
  fake_stores.py          # In-memory implementations of all Store Protocols
  fake_adapters.py        # Deterministic implementations of Adapter Protocols
  fake_models.py          # Deterministic ForecastModel returning fixed ensembles
  fake_clock.py           # Fixed or stepping clock for UtcDatetime injection
  fake_pipeline_status.py # Fake PipelineStatusSource for Flow 4 tests
```

### Fake store contracts

All fake stores use `dict[ID, Entity]` or `list[Entity]` as backing storage. They implement the full Protocol interface:
- `FakeObservationStore` — keyed on `(station_id, parameter, timestamp)`. Raises on duplicate natural keys.
- `FakeForecastStore` — keyed on `ForecastId`. Tracks status transitions; raises `ConflictError` on version mismatch (same as production).
- `FakeAlertStore` — keyed on `AlertId`. Enforces partial unique index (one active alert per station/alert_level/source) in memory.
- `FakeWeatherForecastStore` — backed by `list[WeatherForecastRecord]`. Implements `fetch_lookback` with simple range scan.
- `FakeSkillStore` — backed by `list[SkillScore]`. Supports `computation_version` queries.
- `FakeParameterStore` — backed by `dict[str, ParameterDefinition]`. Seeded from canonical parameter list.

### Fake adapter contracts

- `FakeWeatherForecastSource` — returns a pre-configured `GriddedForecast` or `dict[StationId, WeatherForecastResult]` regardless of input. Configurable via constructor.
- `FakeStationDataSource` — returns a pre-configured list of `RawObservation`.
- `FakeNotificationAdapter` — records calls to `send()` as `list[SentNotification]` for assertion. Never actually sends.
- `FakePipelineStatusSource` — returns pre-configured `list[FlowRunStatus]`.

### Fake model contract

- `FakeForecastModel` — `predict()` returns a deterministic `ForecastEnsemble` (configurable member count, constant values). `train()` returns a trivial artifact. Declares `required_static_attributes = frozenset()` (no static attributes needed). Ignores `static_attributes` in inputs. Useful for testing the orchestration and service layers without real ML.

### Verification

Each fake has a test: `assert isinstance(FakeObservationStore(), ObservationStore)` — ensures Protocol conformance. These tests run in CI and catch any Protocol signature drift.

---

## Module map

Summary of where each type and Protocol lives in the source tree:

```
src/sapphire_flow/
├── types/
│   ├── ids.py              # StationId, ModelId, ObservationId, ForecastAdjustmentId, etc.
│   ├── datetime.py         # UtcDatetime, ensure_utc()
│   ├── enums.py            # All enums
│   ├── domain.py           # GeoCoord, ParameterDefinition, DangerLevelDefinition, QcFlag,
│   │                       #   SeasonDefinition, ExceedanceResult, SkillInterpretationScheme, etc.
│   ├── ensemble.py         # ForecastEnsemble
│   ├── model.py            # ModelInputs, TrainingData, ModelParams, ModelArtifact, ModelArtifactRecord
│   ├── observation.py      # Observation, RawObservation
│   ├── forecast.py         # OperationalForecast, HindcastForecast, ForecastAdjustment, ForeignForecast
│   ├── weather.py          # WeatherForecastRecord, PointForecast, BasinAverageForecast, etc.
│   ├── alert.py            # Alert
│   ├── skill.py            # SkillScore, SkillDiagram, FlowRegimeConfig
│   ├── station.py          # StationConfig, ModelAssignment, StationWeatherSource
│   ├── basin.py            # Basin
│   ├── rating_curve.py     # RatingCurve
│   ├── pipeline.py         # PipelineHealthRecord
│   └── auth.py             # User, AccessToken, AccessTokenScope, AuditEntry
├── schemas/
│   └── forecast.py         # ForecastAdjustmentItem (Pydantic boundary validation)
├── protocols/
│   ├── forecast_model.py   # ForecastModel
│   ├── stores.py           # All store Protocols + ConflictError
│   │                       #   (ObservationStore, ForecastStore, HindcastStore,
│   │                       #    WeatherForecastStore, AlertStore, SkillStore,
│   │                       #    ModelArtifactStore, StationStore, PipelineHealthStore,
│   │                       #    RatingCurveStore, FlowRegimeConfigStore,
│   │                       #    ForecastAdjustmentStore, ForeignForecastStore, BasinStore,
│   │                       #    ModelStateStore, ModelStore, ParameterStore)
│   ├── adapters.py         # WeatherForecastSource, StationDataSource, WeatherReanalysisSource,
│   │                       #   ForeignForecastSource
│   ├── grid_extractor.py   # GridExtractor
│   └── notification.py     # NotificationAdapter
└── config/
    └── deployment.py       # DeploymentConfig
```
