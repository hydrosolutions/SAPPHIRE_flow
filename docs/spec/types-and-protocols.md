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
ObservationVersionId = NewType("ObservationVersionId", UUID)  # v1 — Plan 035 Task 3
FormulaId = NewType("FormulaId", UUID)  # v1 — Plan 015 calculated-station formula row
ForecastAdjustmentId = NewType("ForecastAdjustmentId", UUID)
UserId = NewType("UserId", UUID)
AccessTokenId = NewType("AccessTokenId", UUID)
RefreshTokenId = NewType("RefreshTokenId", UUID)

# ModelId wraps str, not UUID — entry point name is the stable TEXT PK
ModelId = NewType("ModelId", str)
StationGroupId = NewType("StationGroupId", UUID)
ForeignForecastId = NewType("ForeignForecastId", UUID)
HistoricalForcingId = NewType("HistoricalForcingId", UUID)

# PackageId wraps str, not UUID — the producer-declared basin/static package
# identifier (manifest.json "package_id"), v1 — Plan 120 Task 0A
PackageId = NewType("PackageId", str)
BasinVersionId = NewType("BasinVersionId", UUID)  # v1 — Plan 120 Task 0A

# TenantId — Plan 147 Slice A (v1.0 tenant-model foundation, live in v0
# already). Canonical on stations.tenant_id (R4 LOCKED).
TenantId = NewType("TenantId", UUID)

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
    VIRTUAL = "virtual"        # sentinel combination models (_pooled, _bma, _consensus) — no real artifact

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

class ModelCombinationStrategy(Enum):
    PRIMARY = "primary"      # highest-priority model only
    POOLED = "pooled"        # grand ensemble from all models
    BMA = "bma"              # Bayesian Model Averaging (skill-weighted)
    CONSENSUS = "consensus"  # per-model threshold check, then vote

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
    # Semi-open: the DB column is TEXT with no CHECK constraint. Deployments may
    # register parameters with domains not in this enum — the system accepts them
    # but logs a structured warning (known_domain=false). This enum defines the
    # "known" set for which downstream behavior (thresholds, alerting, skill
    # computation) has been validated.

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
    # NOTE: types/enums.py additionally carries FORECAST_STATION_DARK,
    # ALERT_SUPPRESSED_FALLBACK, PRIORITY_MIGRATION_AUDIT,
    # CLIMATOLOGY_THRESHOLD_REVIEW, BAFU_FORECAST_FRESHNESS (pre-existing doc
    # drift, not introduced here).
    WEATHER_HISTORY_INGEST = "weather_history_ingest"
        # Plan 115b4 §6A — ingest_weather_history_flow's health-by-EFFECT
        # check (§6B): CRITICAL when zero stations are bound, or when EVERY
        # source this run targeted shows a NON-ADVANCING MAX(valid_time) —
        # snapshotted via fetch_latest_valid_time BEFORE the fetch/store step
        # and compared against the same snapshot taken AFTER (never
        # rows_stored, and never a single post-run "does a row exist" check,
        # which is trivially true once the rolling window has ever been
        # populated by a PRIOR run and is blind to a stuck duplicate
        # re-fetch). OK when at least one targeted source advances.
    BAFU_OBSERVATION_FRESHNESS = "bafu_observation_freshness"
        # Plan 136 — collect-bafu-observations heartbeat. NETWORK-level
        # freshness (newest measurement_time across all archived gauges),
        # never a per-gauge minimum (a dead gauge can sit in the LINDAS graph
        # for >1 year). OK = a non-empty successful whole-graph fetch whose
        # network-newest measurement_time is within the freshness threshold
        # (~3h). CRITICAL = an empty response, an HTTP/parse/schema-drift
        # error, a truncated fetch (len(bindings) >= LIMIT), OR a non-empty
        # fetch whose network-newest measurement_time is stale (a frozen
        # feed; this path still archives and does not re-raise). WARNING (a
        # future fresh-fraction-below-threshold degradation signal) is
        # reserved but not emitted in this first cut.
    OBSERVATION_INGEST_FETCH = "observation_ingest_fetch"
        # Plan 175 D8/T3 — ingest_observations_flow's per-station FETCH
        # outcome, written IMMEDIATELY after fetch reconciliation (before
        # store/QC) so a later storage/QC failure can never suppress this
        # signal. Deliberately distinct from BAFU_OBSERVATION_FRESHNESS (the
        # quarantined Plan 136 archive collector's own heartbeat, which the
        # watchdog queries) and from OBSERVATION_FRESHNESS (per-station
        # staleness) — reusing either would contaminate a check the watchdog
        # already reads. Locked `detail` keys: `stations_polled`,
        # `stations_fetch_failed`, `failure_counts_by_cause` (a dict keyed by
        # `FetchOutcomeCause.value`), `failed_station_ids` (sorted str list).
        # OK when nothing failed; CRITICAL when every polled station failed;
        # WARNING otherwise (an absolute-count rule, not a percentage — see
        # Plan 175 § Residual forks #2 for why a percentage threshold is
        # noise at a handful of stations). No watchdog probe wired yet
        # (Plan 175 § Residual forks #1) — visible today only via
        # `/api/v1/health/detail` and the dashboard.

class FetchOutcomeCause(Enum):
    # Plan 175 D9 — sapphire_flow.types.enums. The per-station LINDAS fetch
    # failure taxonomy `HydroScraperAdapter.fetch_observations_batch` (see
    # `StationDataSource` below) reports on `StationFetchOutcome.
    # failure_cause`. `NO_DATA` and `MALFORMED_RESPONSE` are deliberately
    # split: the pre-Plan-175 `_parse_bindings` collapsed a bad timestamp and
    # a legitimately-empty response into the same `[]`.
    RATE_LIMITED = "rate_limited"       # 429 after the shared limiter's retry budget was exhausted
    HTTP_STATUS_ERROR = "http_status_error"  # any other non-2xx, carries the status code in failure_detail
    TRANSPORT_ERROR = "transport_error"  # httpx.HTTPError after retry exhaustion, no response at all
    MALFORMED_RESPONSE = "malformed_response"  # unparseable measurementTime, or a rejected site_code
    NO_DATA = "no_data"                 # well-formed response with no usable bindings

class NotificationChannel(Enum):
    EMAIL = "email"
    SMS = "sms"
    WEBHOOK = "webhook"

class StationStatus(Enum):
    ONBOARDING = "onboarding"
    OPERATIONAL = "operational"
    SUSPENDED = "suspended"
    DECOMMISSIONED = "decommissioned"

class GaugingStatus(Enum):
    GAUGED = "gauged"
    UNGAUGED = "ungauged"
    CALCULATED = "calculated"

class ObservationSource(Enum):
    MEASURED = "measured"                          # direct sensor reading
    RATING_CURVE_DERIVED = "rating_curve_derived"  # derived via rating curve conversion (Flow 2 step 2.5)
    COMPONENT_DERIVED = "component_derived"         # derived from calculated-station formulas (v1 — plan 015)
    MANUAL_IMPORT = "manual_import"                # CSV upload (Flow 12 Branch B, Flow 5 step 5.4)

class AuditActorType(Enum):
    USER = "user"
    API_KEY = "api_key"
    SYSTEM = "system"

class AccessTokenRole(Enum):    # Plan 147 Slice C: v1.0 headless HTTP role model — exactly 2 roles, both GET-only
    CONSUMER = "consumer"       # read, station-scoped (access_token_stations)
    ADMIN = "admin"             # read, unscoped + CLI token/tenant management

class AuditEventType(Enum):     # Plan 147 Slice B: promoted from design-intent to runtime
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
    STATION_ONBOARDED = "station_onboarded"     # additive (Plan 147 Slice B)
    MODEL_ASSIGNED = "model_assigned"           # additive (Plan 147 Slice B)

class StationOwnership(Enum):
    OWN = "own"
    FOREIGN = "foreign"

class ForeignForecastStatus(Enum):
    PUBLISHED = "published"

class NwpCycleSource(Enum):
    PRIMARY = "primary"          # snapped operational cycle, published
    FALLBACK = "fallback"        # adapter walked back >=1 cycle step
    RUNOFF_ONLY = "runoff_only"  # no NWP forcing (weather forecast disabled)

class WeatherSourceStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class WeatherSourceRole(Enum):
    FORECAST = "forecast"      # operational NWP forecast binding
    REANALYSIS = "reanalysis"  # historical forcing binding

class SkillFreshness(Enum):
    CURRENT = "current"
    STALE = "stale"

class ModelAssignmentStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class ForcingProvenance(Enum):
    NWP_DIRECT = "nwp_direct"                          # direct NWP model output
    OBSERVED = "observed"                                # from station observations
    INTERPOLATED = "interpolated"                        # temporal interpolation between known values
    GAP_FILLED_CLIMATOLOGY = "gap_filled_climatology"    # filled from climatological mean
    GAP_FILLED_PERSISTENCE = "gap_filled_persistence"    # filled with last known value
    REANALYSIS = "reanalysis"                            # from reanalysis product (v1: ERA5-Land)
    DERIVED = "derived"                                  # computed from other parameters
    UNKNOWN = "unknown"                                  # provenance not tracked (legacy data)

class OnboardingOutcome(Enum):                           # in-memory only, no DB column
    PROMOTED = "promoted"
    GATE_REJECTED = "gate_rejected"
    SKIPPED_COMPAT = "skipped_compat"
    SKIPPED_NO_DATA = "skipped_no_data"
    SKIPPED_INSUFFICIENT_EVAL = "skipped_insufficient_eval"  # zero strata with >= min_skill_samples valid pairs
    FAILED_SMOKE_TEST = "failed_smoke_test"              # model raised exception on random-data predict() call
    FAILED_TRAINING = "failed_training"
    FAILED_HINDCAST = "failed_hindcast"
    FAILED_SKILL = "failed_skill"
    FAILED_ASSIGNMENT = "failed_assignment"
```

---

## Enums — v1 / deferred (not implemented in v0)

These enums appear in v1 design but are not implemented in `src/sapphire_flow/types/enums.py`.
Deferred per `docs/v0-scope.md:464` (UserRole, AdjustmentType, Calendar) and by lack of consumer infrastructure (DlqResolution). Implementers must not import them from `sapphire_flow.types.enums` — the symbols do not exist at runtime. (`AuditEventType` was deferred here too, but Plan 147 Slice B promoted it to runtime — see the enums section above.)

```python
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

```

`AuditEventType` was previously listed here as design-intent only; Plan 147 Slice B promoted it
to `types/enums.py` (see the runtime enums section above) — it is no longer deferred.

---

## Type aliases

```python
from typing import Literal

ForecastParameter = Literal["discharge", "water_level"]
```

Module: `types/domain.py`

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
    parameter: Literal["discharge", "water_level"]
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

### InputQualityLevel, InputQualityCategory, InputQualityFlag

```python
class InputQualityLevel(Enum):
    FULL = "full"
    PARTIAL = "partial"
    DEGRADED = "degraded"

class InputQualityCategory(Enum):
    OBSERVATION = "observation"
    NWP = "nwp"
    WARM_UP = "warm_up"
```

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class InputQualityFlag:
    category: InputQualityCategory
    level: InputQualityLevel      # PARTIAL or DEGRADED (never FULL — FULL flags are not emitted)
    detail: str
```

```python
def aggregate_input_quality(flags: list[InputQualityFlag]) -> InputQualityLevel:
    """Derive aggregate InputQualityLevel from individual flags.

    Ordering: DEGRADED > PARTIAL > FULL.
    Empty flags list → FULL.
    """
    ...
```

Module: `types/domain.py`

### QcRuleParams

Per-rule threshold parameters. Each rule has a set of thresholds that may vary by
parameter and time step. The time-step dimension allows the same QC service to handle
both 10-minute operational data and daily historical data with appropriate thresholds.

```python
from datetime import timedelta

@dataclass(frozen=True, kw_only=True, slots=True)
class QcRuleParams:
    rule_id: QcRuleId              # Literal["range_check", "rate_of_change", "spike", "gross_outlier", "frozen_sensor"]
    rule_version: str              # e.g. "1.0.0"
    parameter: str                 # canonical parameter name (e.g. "discharge", "water_level")
    time_step: timedelta           # observation time step these thresholds apply to
    thresholds: dict[str, float]   # rule-specific thresholds, e.g. {"value_min": 0, "value_max": 5000}
```

**Threshold keys by rule**:
- `range_check`: `value_min`, `value_max`
- `rate_of_change`: `max_rate` (units per second)
- `frozen_sensor`: `tolerance`, `min_consecutive` (int stored as float), `exclude_at_or_below`
  (optional — a value at or below it never starts or extends a frozen run, e.g. `0.0` so
  precipitation's dry spells are not flagged; absent ⇒ every value is eligible, today's behaviour)
- `spike`: `tolerance`
- `gross_outlier`: `k_sigma`

### QcRuleSet

A versioned collection of QC rules for a deployment. Loaded from `config.toml` `[qc_rules]`
section (see `config-reference.toml`).

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class QcRuleSet:
    version: str                           # ruleset version (e.g. "1.0.0")
    rules: tuple[QcRuleParams, ...]        # all rules for all parameters and time steps

    def rules_for(self, parameter: str, time_step: timedelta) -> tuple[QcRuleParams, ...]:
        """Filter rules matching this parameter and time step."""
        return tuple(r for r in self.rules if r.parameter == parameter and r.time_step == time_step)
```

### StationQcOverride

Per-station override of specific QC rule thresholds. Fields set to `None` inherit from
the deployment-level `QcRuleSet`. Loaded from station onboarding TOML; v1 migrates to
DB (dashboard-editable).

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationQcOverride:
    station_id: StationId
    rule_id: str
    parameter: str
    time_step: timedelta
    thresholds: dict[str, float | None]    # None = inherit deployment default
```

### ForecastQcRuleParams

Per-rule threshold parameters for forecast output QC. Parallel to `QcRuleParams` for observation
QC, but operates on ensemble forecasts rather than individual observations.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastQcRuleParams:
    rule_id: ForecastQcRuleId      # Literal["negative_value", "range_check", "flat_ensemble", "ensemble_spread", "climatology_outlier", "temporal_consistency", "quantile_crossing"]
    rule_version: str
    parameter: str                 # "discharge" or "water_level"
    time_step: timedelta
    thresholds: dict[str, float]
```

**Threshold keys by rule**:
- `negative_value`: `value_min`
- `range_check`: `value_min`, `value_max`
- `flat_ensemble`: `tolerance`
- `ensemble_spread`: `min_spread_ratio`, `max_spread_ratio` (ratio to climatic std)
- `climatology_outlier`: `k_sigma`
- `temporal_consistency`: `max_rate` (units per timestep)
- `quantile_crossing`: *(no thresholds — structural check)*

### ForecastQcRuleSet

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastQcRuleSet:
    version: str
    rules: tuple[ForecastQcRuleParams, ...]

    def rules_for(self, parameter: str, time_step: timedelta) -> tuple[ForecastQcRuleParams, ...]:
        return tuple(r for r in self.rules if r.parameter == parameter and r.time_step == time_step)
```

### StationForecastQcOverride

Per-station override of forecast QC rule thresholds. Fields set to `None` inherit from
the deployment-level `ForecastQcRuleSet`.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationForecastQcOverride:
    station_id: StationId
    rule_id: str
    parameter: str
    time_step: timedelta
    thresholds: dict[str, float | None]
```

Module: `types/domain.py`

### ClimBaseline

Rolling climatological mean and standard deviation, pre-computed during station
onboarding (Flow 5 step 5.8). Used by the gross outlier QC rule.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ClimBaseline:
    station_id: StationId
    parameter: str
    day_of_year: int               # 1–366
    rolling_mean: float
    rolling_std: float
    sample_count: int              # number of years contributing to this estimate
```

Module: `types/domain.py`

### QualityChecker Protocol

```python
@runtime_checkable
class QualityChecker(Protocol):
    def check(
        self,
        observations: list[Observation],
        rule_set: QcRuleSet,
        overrides: list[StationQcOverride],
        baselines: list[ClimBaseline],
    ) -> dict[ObservationId, list[QcFlag]]: ...
        # Returns QC flags per observation. Empty list = all rules passed (QC_PASSED).
        # The caller aggregates flags via aggregate_qc_status() and calls
        # ObservationStore.update_qc() to persist.
```

Module: `protocols/stores.py` (alongside other service-adjacent Protocols).

### ForecastQualityChecker Protocol

```python
@runtime_checkable
class ForecastQualityChecker(Protocol):
    def check(
        self,
        ensemble: ForecastEnsemble,
        rule_set: ForecastQcRuleSet,
        overrides: list[StationForecastQcOverride],
        baselines: list[ClimBaseline],
    ) -> list[QcFlag]: ...
        # Returns QC flags for the forecast. Empty list = all rules passed.
        # The caller aggregates via aggregate_qc_status() and sets qc_status/qc_flags
        # on the OperationalForecast before storage.
        # Rules that depend on ClimBaseline (ensemble_spread, climatology_outlier)
        # skip gracefully when no baseline is available.
```

Module: `protocols/stores.py`

**Flow 1 integration note** — Step 1.10: Forecast output QC. Runs `ForecastQualityChecker.check()` on each ensemble. Aggregate `QC_FAILED` raises `SanityCheckFailure` (flow tries fallback model). `QC_PASSED` or `QC_SUSPECT` results are stored on the `OperationalForecast`. For hindcasts, `QC_FAILED` flags the hindcast but does not trigger fallback.

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

Intermediate output of threshold checking (Flow 1 steps 1.12–1.13, Flow 2 steps 2.8–2.9).
Consumed by the alert service to raise or resolve alerts.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ExceedanceResult:
    station_id: StationId
    danger_level: str              # references DangerLevelDefinition.name
    parameter: ForecastParameter
    threshold_value: float         # the configured threshold
    exceedance_probability: float | None  # P(forecast crosses threshold in configured direction), NULL for observation alerts
    observed_value: float | None   # observed value, NULL for forecast alerts
    exceeded: bool                 # whether the threshold was crossed in the configured direction
    model_ids: tuple[ModelId, ...] = ()                        # models that contributed
    strategy: ModelCombinationStrategy = ModelCombinationStrategy.PRIMARY   # which strategy produced this result

    def __post_init__(self) -> None:
        # Invariant: a crossed threshold must carry its probability.
        if self.exceeded and self.exceedance_probability is None:
            raise ValueError("exceedance_probability must be set when exceeded=True")
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
    source: ObservationSource      # measured | rating_curve_derived | manual_import | component_derived
    rating_curve_id: RatingCurveId | None = None  # v1 — set when source = RATING_CURVE_DERIVED. Omit from v0 DB schema.
    rating_curve_correction_version: str | None = None  # v1 — correction param version. Omit from v0 DB schema.

@dataclass(frozen=True, kw_only=True, slots=True)
class Observation:
    id: ObservationId
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float | None            # None when qc_status is MISSING (explicit gap record)
    source: ObservationSource      # measured | rating_curve_derived | manual_import | component_derived
    rating_curve_id: RatingCurveId | None  # v1 — set when source = RATING_CURVE_DERIVED. Omit from v0 DB schema.
    rating_curve_correction_version: str | None  # v1 — correction param version. Omit from v0 DB schema.
    qc_status: QcStatus
    qc_flags: list[QcFlag]
    qc_rule_version: str | None    # version of the QC ruleset that last evaluated this row
    created_at: UtcDatetime


# v1 — Plan 035 Task 3: value superseded by a rating-curve reprocessing
@dataclass(frozen=True, kw_only=True, slots=True)
class ArchivedObservationValue:
    id: ObservationVersionId
    observation_id: ObservationId
    station_id: StationId
    timestamp: UtcDatetime
    parameter: str
    value: float | None                    # None if the superseded obs was MISSING
    rating_curve_id: RatingCurveId         # curve that produced the archived value
    superseded_at: UtcDatetime
    superseded_by_curve_id: RatingCurveId  # curve that replaced it
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
    forecast_targets: frozenset[str] | None  # NULL for weather stations; e.g. frozenset({"discharge", "water_level"})
    measured_parameters: frozenset[str]  # canonical parameter names
    station_status: StationStatus  # lifecycle state — Flow 1 filters to OPERATIONAL only
    created_at: UtcDatetime
    updated_at: UtcDatetime
    network: str                       # e.g., "bafu", "uk_ea", "usgs"
    ownership: StationOwnership        # own = locally managed, foreign = display-only
    wigos_id: str | None               # WMO station ID, format: 0-{country}-{network}-{local}
    gauging_status: GaugingStatus = GaugingStatus.GAUGED
    tenant_id: TenantId = DEFAULT_TENANT_ID  # Plan 147 Slice A — canonical tenant ownership (R4 LOCKED)
```

### ModelAssignment

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ModelAssignment:
    station_id: StationId
    model_id: ModelId
    time_step: timedelta           # configured time step for this assignment
    status: ModelAssignmentStatus
    priority: int                  # run order / alert-selection priority within the model tier. Tier is derived separately via ModelTier.
    created_at: UtcDatetime

@dataclass(frozen=True, kw_only=True, slots=True)
class GroupModelAssignment:
    group_id: StationGroupId
    model_id: ModelId
    time_step: timedelta
    status: ModelAssignmentStatus
    priority: int                  # run order / alert-selection priority within the model tier. Expanded to per-station entries by Phase B.
    created_at: UtcDatetime
```

Priority convention: linear regression (0) > ML (1) > conceptual (2). All model types can be active for the same station simultaneously.

### StationGroup

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationGroup:
    id: StationGroupId
    name: str                      # e.g. "swiss_alpine", "nepal_koshi_basin" — UNIQUE PER TENANT, not globally (Plan 147 Slice A)
    station_ids: frozenset[StationId]
    description: str | None = None
    created_at: UtcDatetime
    tenant_id: TenantId = DEFAULT_TENANT_ID  # Plan 147 Slice A — a group belongs to exactly one tenant
```

Station groups define the training scope for group-scoped ML models. A station can belong to multiple groups. Groups are managed during station onboarding (Flow 5 step 5.10).

Module: `types/station.py`

### Tenant

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class Tenant:
    id: TenantId
    code: str    # human/config handle, e.g. "sapphire", "dhm" — UNIQUE
    name: str
    created_at: UtcDatetime
```

Plan 147 Slice A: the v1.0 tenant-model foundation, landed as a root data-model
slice (no auth yet). `stations.tenant_id`/`station_groups.tenant_id` are
canonical and `NOT NULL`; `station_group_members.tenant_id` participates in
two composite FKs — `(station_id, tenant_id) -> stations(id, tenant_id)` and
`(group_id, tenant_id) -> station_groups(id, tenant_id)` — so a membership row
whose station and group disagree on tenant is structurally unrepresentable.
A tenant CODE string (from `config.toml`'s `[deployment]` block or a
`--tenant` CLI arg) is parsed into a `TenantId` once, at the config/CLI
boundary (`services/tenant_boundary.py::resolve_tenant_code`), by resolving
it against the `tenants` table — an unknown code is a hard error. Migration
0041 seeds a default `sapphire` tenant at the fixed id
`types/tenant.py::DEFAULT_TENANT_ID`; existing single-tenant Swiss data
backfills onto it (migrations 0042-0044).

Module: `types/tenant.py`

### StationWeatherSource

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationWeatherSource:
    station_id: StationId
    nwp_source: str
    extraction_type: SpatialRepresentation  # POINT, BASIN_AVERAGE, or ELEVATION_BAND
    status: WeatherSourceStatus
    role: WeatherSourceRole  # FORECAST or REANALYSIS — required, no default (Plan 115a)
```

One `nwp_source` string serves exactly one role for a station: the `(station_id, nwp_source)` primary key means a name holding two roles would silently overwrite on upsert, so `role` is required and keyword-only rather than defaulted or inferred.

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
    regional_basin: str | None = None    # display grouping label (e.g. "Karnali", "Aare")
    band_geometries: list[dict] | None   # elevation band definitions (computed in Flow 5 step 5.3)
    created_at: UtcDatetime
    network: str
    package_id: PackageId | None = None  # basin/static package provenance; NULL for
                                          #   legacy/non-package basins (v1 — Plan 120 Task 0A)
```

Module: `types/basin.py`

**Namespaced attribute sources (Plan 155 D15).** `attributes` may hold keys from
more than one source. A second source is namespaced under a `<source>:` prefix
(e.g. `caravan:` for Caravan's CAMELS-CH attributes) rather than merged bare —
a colon cannot appear in a canonical (HydroATLAS-coded or classic-CAMELS) attribute
name, so collision with the FIRST source is structurally impossible. Resolution
into a model's own declared name is additive and model-scoped
(`services/caravan_statics.py::project_declared_static_attributes`) — never a
blanket unprefixed merge, which would silently change an INCUMBENT model's inputs.
See Plan 155 for the full rule and `store/basin_store.py::PgBasinStore
.merge_namespaced_attributes` for the write-side guard.

**Whether resolution applies at all is the MODEL's own declaration (Plan 155
D16, post-implementation-review fix).** `types/enums.py::StaticNaming`
(`NATIVE` | `CARAVAN`) is a class attribute a model declares exactly like
`model_tier` / `alert_eligibility` (`services/model_registry.py`), read via
`services/caravan_statics.py::declared_static_naming`. `NATIVE` is the
default — every incumbent model that does not declare `static_naming` keeps
today's raw-bare-key behaviour byte-for-byte, with NO union against the
`caravan:` namespace. Only a model that declares `StaticNaming.CARAVAN` gets
D15's strict no-bare-fallback resolution. This exists because inference from
the alias table cannot work: 29 of PT's 50 statics are DIRECT names (e.g.
`area`), textually indistinguishable from an incumbent's own same-named bare
attribute — only the model itself can say which regime it wants.
`project_declared_static_attributes` additionally enforces T2's collision
semantics for an ALIASED name that ships both its raw HydroATLAS code and its
bare Caravan name: equal FINITE values resolve silently, differing values (or
equal infinities) raise `ConfigurationError` naming station, both keys and
both values.

**`static_naming` must be forwarded across the FI adapter boundary (Plan 155
fixer round 2).** `adapters/forecast_interface.py::ForecastInterfaceAdapter`
does not forward arbitrary attributes from the wrapped model; discovery
(`services/model_registry.py::_assert_model_classification_declared`) copies
`static_naming` from the RAW model onto the ADAPTED instance once, at
discovery time — the same place `model_tier`/`alert_eligibility` already
survive this boundary. A present-but-invalid `static_naming` (not a
`StaticNaming` member) raises `ConfigurationError` rather than silently
defaulting to `NATIVE`; only a genuinely absent declaration defaults.

**A shared static frame across co-assigned models resolves per-model, not
per-representative.** `services/operational_inputs.py::
assemble_station_operational_inputs`'s `requirements_override` fallback-chain
path can union `static_features` across every model assigned to a station;
`services/caravan_statics.py::resolve_shared_static_frame` scopes Caravan
resolution to only the names a `CARAVAN`-declaring co-assigned model itself
declares, leaves names exclusively declared by `NATIVE` co-assignments
untouched, and raises `ConfigurationError` if two co-assigned models declare
the identical bare name under differing `StaticNaming` regimes — there is no
single correct shared value for that case.

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
    model_ids: tuple[ModelId, ...] = ()                        # models that contributed; () for observation/pipeline alerts
    alert_model_strategy: ModelCombinationStrategy | None = None      # strategy; None for observation/pipeline alerts
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
    adjustment_type: Literal["shift", "scale", "cap", "floor"]  # envelope operation; Literal is used here for Pydantic boundary compatibility; AdjustmentType enum is the v1 canonical form (see *Enums — v1 / deferred*)
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
    interpolation: InterpolationMethod   # enum: linear | log_linear (Plan 035 Task 1)
    uploaded_by: UUID | None
    created_at: UtcDatetime
```

Module: `types/rating_curve.py`

```python
# v1 — Plan 131: pure level→discharge (h→Q) conversion service (services/rating_conversion.py)
class RatingRange(Enum):          # which side of the tabulated stage domain a level fell on
    IN_RANGE; BELOW; ABOVE

@dataclass(frozen=True, kw_only=True, slots=True)
class ConversionResult:
    discharge: float
    range_flag: RatingRange       # caller maps a non-IN_RANGE flag to a QC flag

class RatingConverter:            # built once per curve (validates + sorts), then pure
    @classmethod
    def from_curve(cls, curve: RatingCurve) -> RatingConverter: ...
    def convert(self, level: float) -> ConversionResult: ...
        # linear | log_linear interpolation; out-of-range clamps to the nearest endpoint
        # and reports range_flag (no extrapolation). Raises RatingConversionError on an
        # invalid curve/level.
```

Module: `services/rating_conversion.py`

### ComponentWeight

```python
# v1 — Plan 015: one (component station, weight) row of a calculated station's formula.
# Q_virtual = Σ(wᵢ · Qᵢ) over the component rows for a (calculated_station, parameter)
# and validity window. Weights are signed physical scaling factors (negative allowed for
# difference formulas) and need not sum to 1.
@dataclass(frozen=True, kw_only=True, slots=True)
class ComponentWeight:
    id: FormulaId
    calculated_station_id: StationId
    component_station_id: StationId
    parameter: str
    weight: float                  # nonzero, finite, |w| < 1e6 (validated in __post_init__)
    effective_from: UtcDatetime
    effective_to: UtcDatetime | None   # None = current; non-None = superseded
    created_at: UtcDatetime
        # __post_init__: rejects zero/non-finite weight and self-reference
        # (calculated_station_id == component_station_id).
```

A DB-level eligibility trigger (`trg_csf_component_eligibility`) backstops the invariant:
on insert / relation-changing update the target must be `gauging_status='calculated'` and
each component `gauging_status='gauged'` AND `station_status='operational'`. A closure-only
update (sets `effective_to`, leaves relation columns unchanged) is exempt so a component can
be decommissioned. A partial-unique index (`uq_csf_current`) allows at most one current row
per `(calculated_station_id, component_station_id, parameter)`.

Module: `types/calculated_station.py`

### FlowRegimeConfig

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class FlowRegimeConfig:
    id: UUID
    station_id: StationId
    parameter: str                 # canonical parameter name, e.g. "discharge"
    p50: float                     # 50th percentile of forecast target parameter
    p90: float                     # 90th percentile of forecast target parameter
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

### API/dashboard model visibility

```python
class ModelTier(StrEnum):
    SKILL = "skill"
    FALLBACK = "fallback"

FALLBACK_MODEL_IDS: frozenset[ModelId] = frozenset(
    {ModelId("climatology_fallback"), ModelId("persistence_fallback")}
)

def model_tier_for_model_id(model_id: str | ModelId | None) -> ModelTier: ...
```

Module: `api/model_visibility.py`. This is a query/render-time visibility facet,
not a DB column. Station `no_floor` is likewise derived at query time from active
`climatology_fallback` artifact presence.

### FlowRunState

Enum representing the state of a Prefect flow run.

```python
class FlowRunState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CRASHED = "crashed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
```

Uses `StrEnum` (Python 3.11+). Members compare equal to their string value via `==` and `in`; `isinstance(v, str)` is `True`.

Module: `types/enums.py`

### FlowRunStatus

Snapshot of a Prefect flow run's current state, used by the pipeline monitoring subsystem (Flow 4).

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class FlowRunStatus:
    flow_name: str
    run_id: str
    state: FlowRunState
    started_at: UtcDatetime | None
    duration_seconds: float | None
    error_message: str | None
```

Module: `types/pipeline.py`

### Auth entities

v0 defers auth. `AuditEntry` (below) is now **implemented** (Plan 147 Slice B, `types/auth.py`) — it
is the append-only `audit_log` row type. `AccessToken` (below) is ALSO now **implemented** (Plan 147
Slice C, `types/auth.py` + `types/enums.py::AccessTokenRole`) — but with a shape that **supersedes**
the v1.x-design-intent sketch that used to live here (`consumer_name`/`AccessTokenScope`/`created_by`/
`revoked_at`): v1.0 is headless (no `users` table yet), R1 LOCKED the hash to HMAC-SHA-256+pepper (not
bcrypt), and R2 LOCKED the scope carrier to a normalized `access_token_stations` join (not a JSONB
`AccessTokenScope`, and station-axis only — parameter/geographic scope are v1.x). `User` remains
design intent only (v1.x) — do not import it.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class User:
    id: UserId
    username: str                        # email address
    display_name: str
    role: UserRole
    is_active: bool
    created_at: UtcDatetime
```

**Status** (`User` above): v1.x — deferred per Plan 042. Not implemented yet. Design intent only; do
not import it.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class AccessToken:
    """The `access_tokens` row (v1.0 headless, Plan 147 Slice C — supersedes
    the old consumer_name/AccessTokenScope/created_by/revoked_at sketch)."""

    id: AccessTokenId
    token_hash: str                      # HMAC-SHA-256(pepper, raw_secret) hex digest — R1
    key_prefix: str                      # fast pre-verification lookup key
    name: str
    role: AccessTokenRole                # CONSUMER (station-scoped) | ADMIN (unscoped)
    tenant_id: TenantId | None           # None = unscoped global-admin token
    pepper_version: int                  # v1.x dual-pepper rotation forward hook
    expires_at: UtcDatetime              # mandatory (Plan 042:96)
    disabled_at: UtcDatetime | None      # None = active
    created_at: UtcDatetime
    last_used_at: UtcDatetime | None     # None = never used
    station_ids: frozenset[StationId]    # access_token_stations scope join (R2); consumer-only
```

Module: `types/auth.py`. **Status**: **implemented** (Plan 147 Slice C, 2026-07-24). Table + migration
0047 (`access_tokens` + `access_token_stations`); store `store/access_token_store.py::PgAccessTokenStore`
(scope-membership validated against the token's own `tenant_id` at `create_token`, raising
`CrossTenantScopeError` on a cross-tenant station id); the FastAPI auth boundary
(`api/security.py::require_principal`/`require_admin`) resolves a Bearer header to a `Principal`
(a request-scoped API-boundary type, distinct from the persisted `AccessToken` row) on the request's
own read connection. CLI: `python -m sapphire_flow.cli.access_tokens {create,list,revoke,create-admin}`.
Deferred to v1.x: `AccessTokenScope`'s parameter/geographic axes, in-place rotation, dashboard key
management.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class AuditEntry:
    event_type: AuditEventType
    actor_id: UserId | AccessTokenId | None  # UserId (user) | AccessTokenId (api_key) | None (system)
    actor_type: AuditActorType
    target_type: str | None
    target_id: str | None
    detail: dict | None
    ip_address: str | None
    created_at: UtcDatetime
```

Module: `types/auth.py`

**Status**: **implemented** (Plan 147 Slice B, 2026-07-24). The `audit_log` table (migration 0045)
+ its role-independent append-only guard (migration 0046) + the `PgAuditLogStore` writer
(`store/audit_log_store.py`) are live. Call sites: token create/revoke (Slice C); onboard/promote/
assign successes AND their tenant-mismatch rejections (Slice E, 2026-07-24 —
`services/onboarding.py::onboard_from_camelsch`, `services/training.py::promote_artifact`/
`store_and_promote_artifact`, `services/model_onboarding.py::create_station_assignment`/
`create_group_assignment`, the scheduled `flows/train_models.py::train_models_flow`'s foreign-tenant
unit skip).

### `WritePrincipal` / `PrincipalId` (Plan 147 Slice E, R5/G6 LOCKED)

```python
PrincipalId = NewType("PrincipalId", str)  # config-declared operator handle; NOT a UserId/AccessTokenId

@dataclass(frozen=True, kw_only=True, slots=True)
class WritePrincipal:
    id: PrincipalId | None
    tenant_id: TenantId | None  # None = unscoped/global-admin
```

Modules: `types/ids.py` (`PrincipalId`), `types/write_principal.py` (`WritePrincipal`). **Status**:
**implemented** (Plan 147 Slice E, 2026-07-24). A THIRD principal kind — distinct from the two HTTP
read roles (`AccessTokenRole.CONSUMER`/`ADMIN`) and never materialized from an `access_tokens` row
(read-only, G4) or from the target row being written. Built ONLY from config + a validated run
identity: `services/write_principal.py::resolve_run_principal` resolves the `[deployment]` config
block (`config/deployment_identity.py::DeploymentIdentityConfig` — `writable_tenants`/`global_admin`
+ optional `operator`) plus an interactive `--tenant`/`--operator` override or the scheduled flow's
single config-selected tenant (resolved BEFORE unit selection — never from
`unit.station_id`/`unit.group_id`). `services/write_principal.py::enforce_tenant_isolation` is the
single enforcement chokepoint every write path calls: a mismatched target raises
`exceptions.TenantIsolationError` BEFORE any domain-state write and persists a `system`-actor
`audit_log` rejection row; an unscoped principal bypasses. `model_artifacts.promoted_by` (legacy
nullable UUID) stays `NULL` under this principal — a config-string `PrincipalId` does not fit a UUID
column; promotion provenance is instead the `MODEL_PROMOTED` `audit_log` row.

`AuditEntry.__post_init__` enforces the `actor_type`/`actor_id` pairing at construction (`SYSTEM` ⇒
`actor_id=None`; `USER`/`API_KEY` ⇒ `actor_id` present) — `NewType` alone cannot distinguish `UserId`
from `AccessTokenId` at runtime, so this validates presence/absence only. Migration 0045 also carries
a matching `ck_audit_log_actor_id_matches_actor_type` DB CHECK constraint as a backstop for writers
that bypass the domain type. Prefer the `AuditEntry.system(...)` / `.user(...)` / `.api_key(...)`
typed constructors over calling `AuditEntry(...)` directly.

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
    units: str                     # e.g. "m³/s", "m" — for display and unit-mismatch guards
    forecast_horizon_steps: int
    time_step: timedelta
    model_id: ModelId | None = None    # set during forecast cycle; None for test/legacy

    @property
    def member_count(self) -> int:
        match self.representation:
            case EnsembleRepresentation.MEMBERS:
                return self.values["member_id"].n_unique()
            case EnsembleRepresentation.QUANTILES:
                return self.values["quantile"].n_unique()
            case _:
                raise ValueError(f"Unknown representation: {self.representation}")
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

- `ForecastEnsemble.from_members(station_id, issued_at, parameter, units, time_step, forecast_horizon_steps, values: pl.DataFrame, model_id: ModelId | None = None) -> ForecastEnsemble` — validates: `member_id` column present and `Int32` dtype, `quantile` column absent, `valid_time` column present and `Datetime` dtype, `value` column present and `Float64` dtype, at least 1 member. Sets `representation = MEMBERS`.

- `ForecastEnsemble.from_quantiles(station_id, issued_at, parameter, units, time_step, forecast_horizon_steps, values: pl.DataFrame, model_id: ModelId | None = None) -> ForecastEnsemble` — validates: `quantile` column present and `Float64` dtype, `member_id` column absent, `valid_time` column present, `value` column present, at least 7 quantile levels with tail coverage (min <= 0.05, max >= 0.95). Sets `representation = QUANTILES`.

Both raise `ValueError` with a descriptive message on validation failure. The standard constructor always runs `__post_init__` validation. For store-layer reconstruction of already-validated data, the validation is idempotent and cheap — no bypass is needed.

### StationInputData / StationModelInputs / GroupModelInputs

Input containers passed to model `predict()` / `predict_batch()` calls.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationInputData:
    """Feature matrices for a single station inference call."""
    past_targets: pl.DataFrame       # timestamp + target parameter columns (lookback window)
    past_dynamic: pl.DataFrame       # timestamp + dynamic forcing columns (lookback window)
    future_dynamic: pl.DataFrame     # timestamp + dynamic forcing columns (forecast horizon)
    static: pl.DataFrame | None      # single-row scalar catchment attributes; None if not needed

@dataclass(frozen=True, kw_only=True, slots=True)
class StationModelInputs:
    """Full inference payload for a single-station model."""
    station_id: StationId            # used by ML models for station embeddings
    data: StationInputData
    issue_time: UtcDatetime
    forecast_horizon_steps: int
    time_step: timedelta
    forcing_route: ForcingRoute = ForcingRoute.LEGACY_SUPERSET  # Plan 151 D10 discriminant
```

`forcing_route` is the **explicit** legacy-vs-per-track discriminant (Plan 151 D10) — set by
whichever assembler built the payload, never inferred from an absent contract:

- `LEGACY_SUPERSET` (default) — `services/operational_inputs.py`. ONE frame per station sized to
  the **MAX** horizon across the station's co-assigned models, so a shorter-horizon model is
  over-delivered **by design**; `adapters/forecast_interface.py` therefore delivers (and NaN-gates)
  the whole frame, preserving `models/nwp_regression.py`'s "over-delivery ... is tolerated and
  forecast in full" contract.
- `PER_TRACK` — `services/track_assembly.py`. Per-assignment, per-feature contracted frames; the
  FI boundary applies D9's per-variable `future_steps` slice.

```python

@dataclass(frozen=True, kw_only=True, slots=True)
class GroupModelInputs:
    """Batch inference payload for a group-scoped model.

    DataFrames are stacked with a `station_id` (Utf8) column prepended.
    ``for_station()`` slices back to per-station ``StationInputData`` for
    models that need to iterate station-by-station internally.
    """
    group_id: StationGroupId
    station_ids: tuple[StationId, ...]
    past_targets: pl.DataFrame       # stacked: station_id + timestamp + target columns
    past_dynamic: pl.DataFrame       # stacked: station_id + timestamp + dynamic columns
    future_dynamic: pl.DataFrame     # stacked: station_id + timestamp + dynamic columns
    static: pl.DataFrame | None      # stacked: station_id + attribute columns; None if not needed
    issue_time: UtcDatetime
    forecast_horizon_steps: int
    time_step: timedelta

    def for_station(self, station_id: StationId) -> StationInputData:
        """Slice stacked DataFrames for one station."""
        ...
```

**Column contracts:**

`past_targets` / `past_dynamic` / `future_dynamic`:
- First column: `timestamp` (Datetime UTC). Subsequent columns: one per canonical parameter name.
- For elevation-band models, parameter columns are band-qualified: `precipitation_band_1`,
  `temperature_band_2`, etc.
- For each parameter column `{param}`, a companion `{param}_provenance` column (Polars `Enum`
  built from `ForcingProvenance` values) tracks data origin. Helper functions in `types/model.py`:
  - `PROVENANCE_SUFFIX = "_provenance"` — suffix constant
  - `parameter_columns(df)` — returns parameter columns (excludes timestamp and provenance)
  - `forcing_provenance_columns(df)` — returns provenance columns
  - `validate_forcing_provenance(df)` — raises `ValueError` if provenance columns are incomplete or orphaned

`static`:
- One column per attribute name (e.g. `mean_elev_m`, `mean_slope`, `forest_fraction`).
  Single row per station. Values are `Float64`. Sourced from `basins.attributes` JSONB.
- `None` when the model declares no `static_features` or the station's basin has no attributes.
- **Future extension**: gridded static attributes will use a separate `static_grids: xr.Dataset | None` field.

**Stacked DataFrames (`GroupModelInputs`):** The same column contracts apply, with `station_id`
(Utf8) prepended as the first column. Column order: `station_id`, `timestamp`, then parameter
columns with companion provenance columns. For `static`: `station_id`, then attribute columns
(no timestamp). `parameter_columns(df)` excludes both `timestamp` and `station_id`.
**P9 target state**: `stack_model_inputs()` (in `types/model.py`) currently constructs
`GroupModelInputs` from `dict[StationId, ModelInputs]` — the old pre-P9 input type.
In P9 this function will be removed or replaced: `GroupModelInputs` will be assembled
directly from `dict[StationId, StationModelInputs]` (the renamed input container).

### StationTrainingData / GroupTrainingData

Training data containers passed to model `train()` calls.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationTrainingData:
    """Training features and targets for a single station."""
    past_targets: pl.DataFrame       # timestamp + target parameter columns
    past_dynamic: pl.DataFrame       # timestamp + dynamic forcing columns
    future_dynamic: pl.DataFrame     # timestamp + dynamic forcing columns (shifted for teacher forcing)
    static: pl.DataFrame | None      # single-row scalar catchment attributes; None if not needed
    time_step: timedelta
    val_start: UtcDatetime | None    # if set, data after this time is validation holdout

@dataclass(frozen=True, kw_only=True, slots=True)
class GroupTrainingData:
    """Stacked training data for a group-scoped model.

    DataFrames are stacked with a `station_id` (Utf8) column prepended.
    ``for_station()`` slices back to per-station ``StationTrainingData``.
    """
    group_id: StationGroupId
    station_ids: tuple[StationId, ...]
    past_targets: pl.DataFrame       # stacked: station_id + timestamp + target columns
    past_dynamic: pl.DataFrame       # stacked: station_id + timestamp + dynamic columns
    future_dynamic: pl.DataFrame     # stacked: station_id + timestamp + dynamic columns
    static: pl.DataFrame | None      # stacked: station_id + attribute columns; None if not needed
    time_step: timedelta
    val_start: UtcDatetime | None    # group-wide validation split

    def for_station(self, station_id: StationId) -> StationTrainingData:
        """Slice stacked DataFrames for one station."""
        ...
```

Column contracts for `past_targets`, `past_dynamic`, `future_dynamic`, and `static` match
those described above for `StationInputData`. Training always uses tabular data (no `xr.Dataset`).

### ModelDataRequirements

Declares what data a model needs. Stored on the Protocol as `data_requirements` and in
`ModelRegistryEntry`. Used by the input preparation service and model onboarding to match
stations to models.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ModelDataRequirements:
    target_parameters: frozenset[str]          # e.g. frozenset({"discharge"})
    past_dynamic_features: frozenset[str]      # e.g. frozenset({"precipitation", "temperature"})
    future_dynamic_features: frozenset[str]    # e.g. frozenset({"precipitation"})
    static_features: frozenset[str]            # empty frozenset if none needed
    supported_time_steps: frozenset[timedelta]
    lookback_steps: int
    forecast_horizon_steps: int
    spatial_input_type: SpatialRepresentation
    ensemble_mode: EnsembleMode = EnsembleMode.SINGLE   # SINGLE: model emits one trajectory; ENSEMBLE: forcing carries member-suffixed columns, fanned out per member
```

`ensemble_mode` (`EnsembleMode` enum: `SINGLE` | `ENSEMBLE`, mirrors the ForecastInterface `FutureKnownVariable.ensemble_mode` values) marks a model whose future-known forcing is delivered as member-suffixed columns (`precipitation_0`, `precipitation_1`, …). The FI adapter projects `ENSEMBLE` when any `future_known` variable declares it; the operational and conformance paths then fan such a model out over the members (see `services/ensemble_fanout.py`), assembling one N-member ensemble from N single-trajectory predictions. Defaulted to `SINGLE` so native single-trajectory models are unaffected. The hindcast path never fans out (reanalysis is a single teacher-forced trajectory).

**`supported_time_steps` for FI-adapted models (Plan 156):** the FI adapter projects this as the
`time_step` branch(es) of `InputRequirement.dynamic` whose `future_known` is non-empty — never a
past-only branch — so a downstream `next(iter(model.data_requirements.supported_time_steps))` (e.g.
`services/model_onboarding.py`, `services/onboarding.py`) cannot land on a resolution the model has
no forecast horizon at. A requirement is rejected at construction (`UnsupportedModelRequirementError`)
if more than one branch declares non-empty `future_known` — SAP3 domain types are single-resolution,
so this set has at most one member for an FI-adapted model. A past-only second branch (no
`future_known`) stays constructible and is simply excluded from `supported_time_steps`. Native (non-FI)
models are unaffected — they declare `supported_time_steps` directly.

**Every `next(iter(supported_time_steps))` call site is guarded (Plan 156 follow-up):**
`resolve_single_supported_time_step()` (`services/model_onboarding.py`) replaces the five
sites that used to pick an element via `next(iter(...))` — an arbitrary, non-deterministic
choice whenever a model declared more than one supported time step. It requires exactly one
element and raises `ConfigurationError` naming the ambiguous set otherwise. Native models with
more than one genuinely supported time step (§ `ModelDataRequirements` above) still work with
`validate_compatibility`/`validate_compatibility_for_unit` (membership, not "pick one"); only
the automatic-selection call sites (synthetic smoke-test data generation, and Flow 0's
automatic model assignment/training-scope resolution when no per-station time step is
configured yet) require the singleton.

**Delivery-time guard for the accepted one-future-forced-plus-past-only shape (Plan 156
follow-up):** `ForecastInterfaceAdapter` accepts a requirement with one future-forced branch
plus a past-only branch at *construction* (needed for Plan 151's per-branch accessors), but
`_station_inputs_from_frames` — the actual predict/train delivery path — builds only ONE
`StationInputs.dynamic` entry (the branch matching the caller's `time_step`). A past-only
second branch's variables (e.g. a daily `soil_moisture`) are fetched into `past_dynamic` and
NaN-checked (both flatten across all branches), but were being silently OMITTED from what the
model actually received. `_assert_single_deliverable_dynamic_branch()` now raises
`UnsupportedModelRequirementError` the moment `predict()`/`train()`/`predict_batch()` is
called on such a model, instead of silently dropping the non-active branch. Real
multi-resolution delivery (populating every declared branch) is Plan 153.

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
    description: str
    created_at: UtcDatetime
```

Module: `types/model.py`

### ModelRegistryEntry

Runtime metadata for a registered model — includes `data_requirements` (feature sets,
static features, spatial input type) and supported time steps needed by the pipeline.
Superset of `ModelRecord`.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ModelRegistryEntry:
    id: ModelId                       # TEXT PK — entry point name
    display_name: str
    description: str
    artifact_scope: ArtifactScope     # STATION or GROUP — determines training and artifact granularity
    data_requirements: ModelDataRequirements
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
    sha256_hash: str                   # hex digest of artifact bytes (OWASP A08 integrity control)
    training_period_start: UtcDatetime
    training_period_end: UtcDatetime
    trained_at: UtcDatetime
    promoted_at: UtcDatetime | None    # when status changed to ACTIVE
    promoted_by: UUID | None           # model admin who approved
    superseded_at: UtcDatetime | None  # when a newer artifact replaced this one
    created_at: UtcDatetime
```

Module: `types/model.py`

### ModelArtifactProvenance

Plan 157 T3 (G4/G7): marks a `model_artifacts` row as EXTERNALLY IMPORTED
rather than trained by SAP3. Presence of a `model_artifact_provenance` row
keyed by `artifact_id` is the provenance signal — absence means SAP3-trained.
`training_period_start`/`training_period_end`/`trained_at` on
`ModelArtifactRecord` stay non-nullable for an import too (see
`services/model_import.py`); `imported_at` here is a deliberately SEPARATE
field from `trained_at` — conflating them would backdate the import event to
the model's external training-completion instant.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ModelArtifactProvenance:
    artifact_id: ArtifactId
    source_repository: str | None
    source_commit: str | None
    config_hash: str | None
    imported_at: UtcDatetime
    imported_by: str | None
    notes: str | None
```

Module: `types/model.py`

### OperationalForecast

Wraps the `forecasts` + `forecast_values` join. Contains a `ForecastEnsemble` for the
values payload.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastProvenance:
    """Forward-compatible NWP provenance value object (epic-088 M4).

    RUNOFF_ONLY carries a null reference time (there is no NWP cycle). Kept
    extensible so future degradation facts fold in here rather than sprawling
    across flat forecast fields.
    """
    nwp_cycle_source: NwpCycleSource
    nwp_cycle_reference_time: UtcDatetime | None


@dataclass(frozen=True, kw_only=True, slots=True)
class OperationalForecast:
    id: ForecastId
    station_id: StationId
    model_id: ModelId
    model_artifact_id: ArtifactId | None   # NULL for combined forecasts (no single artifact)
    issued_at: UtcDatetime
    nwp_cycle_reference_time: UtcDatetime | None  # NULL in runoff-only mode (no NWP cycle)
    nwp_cycle_source: NwpCycleSource
    representation: EnsembleRepresentation
    status: ForecastStatus
    version: int                           # optimistic locking
    warm_up_source: WarmUpSource | None    # NULL for ML models
    warm_up_state_age_hours: float | None
    observation_staleness_hours: float | None
    ensemble: ForecastEnsemble             # the values payload
    created_at: UtcDatetime
    updated_at: UtcDatetime
    qc_status: QcStatus = QcStatus.RAW            # aggregate forecast QC status
    qc_flags: tuple[QcFlag, ...] = ()              # individual rule results
    input_quality: InputQualityLevel = InputQualityLevel.FULL
    input_quality_flags: tuple[InputQualityFlag, ...] = ()
    combination_strategy: str | None = None        # NULL for individual; "pooled"|"bma"|"consensus" for combined
    source_model_ids: list[ModelId] | None = None  # NULL for individual; contributing model IDs for combined
    rating_curve_id: RatingCurveId | None = None   # v1 — curve active at issued_at; NULL for direct-discharge stations (Plan 035 Task 2/4)

    @property
    def provenance(self) -> ForecastProvenance:  # read-only view over the flat provenance fields
        ...
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
    qc_status: QcStatus = QcStatus.RAW            # aggregate forecast QC status
    qc_flags: tuple[QcFlag, ...] = ()              # individual rule results
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
    is_gap: bool = False                   # v1 (Flow 11): omit from v0 DB schema; default allows v0 construction without these fields
    gap_status: Literal["recovered", "unrecoverable"] | None = None  # v1 (Flow 11): default allows v0 construction
    created_at: UtcDatetime
```

Module: `types/weather.py`

### RawHistoricalForcing / HistoricalForcingRecord

Input and persistent forms of historical weather forcing used for model training and hindcasts.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class RawHistoricalForcing:
    station_id: StationId
    source: str              # "camels-ch", "era5", "era5-land", "smn"
    version: str             # dataset version tag
    valid_time: UtcDatetime
    parameter: str
    spatial_type: SpatialRepresentation
    band_id: int | None      # non-null when spatial_type == ELEVATION_BAND
    member_id: int | None    # None=deterministic, 0=control, 1..N=ensemble members
    value: float

@dataclass(frozen=True, kw_only=True, slots=True)
class HistoricalForcingRecord:
    id: HistoricalForcingId
    station_id: StationId
    source: str
    version: str
    valid_time: UtcDatetime
    parameter: str
    spatial_type: SpatialRepresentation
    band_id: int | None
    member_id: int | None
    value: float
    created_at: UtcDatetime
```

`RawHistoricalForcing` is the unpersisted form (adapter output / pre-insert). `HistoricalForcingRecord` is the persisted form with DB-assigned `id` and `created_at`. The natural key is `(station_id, source, version, valid_time, parameter, spatial_type, band_id, member_id)`.

Module: `types/historical_forcing.py`

### SkillScore

Narrow/tall design — one row per metric per stratum.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class SkillScore:
    id: UUID
    station_id: StationId
    model_id: ModelId
    parameter: str
    model_artifact_id: ArtifactId | None   # NULL for combined-model skill rows
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
    freshness: SkillFreshness              # STALE when underlying data changed; cleared by Flow 10 step S.6
    eval_period_start: UtcDatetime
    eval_period_end: UtcDatetime
    created_at: UtcDatetime
```

### SkillDiagram

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class SkillDiagram:
    id: UUID
    station_id: StationId
    model_id: ModelId
    parameter: str
    model_artifact_id: ArtifactId | None   # NULL for combined-model skill rows
    skill_source: SkillSource
    computation_version: int
    lead_time_hours: int
    season: str | None
    flow_regime: FlowRegime | None
    flow_regime_config_id: UUID | None     # FK → flow_regime_configs.id (NULL when flow_regime is NULL)
    diagram_type: Literal["reliability", "roc", "rank_histogram"]
    threshold_level: str | None            # danger level name (for ROC/BSS diagrams)
    data: dict                             # diagram-specific structure (validated at boundary)
    eval_period_start: UtcDatetime
    eval_period_end: UtcDatetime
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
    data_requirements: ModelDataRequirements

    def train(self, data: StationTrainingData, params: ModelParams, rng: random.Random) -> ModelArtifact: ...
    def predict(
        self,
        artifact: ModelArtifact,
        inputs: StationModelInputs,
        rng: random.Random,
        prior_state: bytes | None = None,
    ) -> tuple[dict[str, ForecastEnsemble], bytes | None]: ...
    def serialize_artifact(self, artifact: ModelArtifact) -> bytes: ...
    def deserialize_artifact(self, raw: bytes) -> ModelArtifact: ...

@runtime_checkable
class GroupForecastModel(Protocol):
    """Model trained on a group of stations (ML models like LSTM, transformer)."""
    artifact_scope: ArtifactScope          # must be ArtifactScope.GROUP
    data_requirements: ModelDataRequirements

    def train(self, data: GroupTrainingData, params: ModelParams, rng: random.Random) -> ModelArtifact: ...
    def predict_batch(
        self,
        artifact: ModelArtifact,
        inputs: GroupModelInputs,
        rng: random.Random,
    ) -> dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]: ...
    def serialize_artifact(self, artifact: ModelArtifact) -> bytes: ...
    def deserialize_artifact(self, raw: bytes) -> ModelArtifact: ...

# Union type for the orchestration layer to dispatch on
ForecastModel = StationForecastModel | GroupForecastModel
```

Models are pure functions — no DB, no I/O. Artifact serialization is the model's
responsibility; artifact *persistence* (reading/writing files) is the caller's.

**`predict()` / `predict_batch()` return type** — `dict[str, ForecastEnsemble]` maps target
parameter name (e.g. `"discharge"`) to its ensemble. Multi-target models populate multiple
keys. Single-target models return a single-entry dict.

**Key difference — training**: `StationForecastModel.train()` receives single-station
`StationTrainingData`; `GroupForecastModel.train()` receives `GroupTrainingData` with stacked
data for all stations in the group. The orchestration layer (Flow 6/9 T.2–T.3) checks
`artifact_scope` to dispatch.

**Key difference — prediction**: Orchestration dispatches on `artifact_scope`:
- *Station models* → `predict()` per station. Accepts optional `prior_state` (opaque bytes
  from a previous run). Models that maintain internal state (conceptual, hybrid) return
  `(ensembles, updated_state)`. Stateless models return `(ensembles, None)`.
- *Group models* → single `predict_batch()` call per (model, group). Receives
  `GroupModelInputs` (stacked), returns `dict[StationId, tuple[dict[str, ForecastEnsemble], bytes | None]]`.
  ML models access station identity via `GroupModelInputs.station_ids` or the `station_id`
  argument passed to `for_station()`. No `prior_state` input — ML models are stateless. A
  single-station group is a single-key result dict (no special case).

The caller persists state via `ModelStateStore`.

Module: `protocols/forecast_model.py`

### ForecastInterfaceAdapter

`ForecastInterfaceAdapter` is SAP3's single conformance boundary for external
`forecastinterface.ForecastModel` implementations. The wrapper satisfies
`StationForecastModel` or `GroupForecastModel` depending on FI `artifact_scope`,
projects FI `InputRequirement` into SAP3 `ModelDataRequirements`, converts SAP3
training/prediction inputs into FI `ModelInputs`, converts FI `VariableOutput`
into `ForecastEnsemble`, and bridges FI gauge-code station keys back to SAP3
`StationId` values.

Total FI failures (`ModelFailure`, empty `ModelOutput.variables`, or all
`VariableStatus.FAILURE` outputs) raise `ModelOutputError` (defined below) so
callers see the same total-failure signal as native SAP3 models. FI-wrapped
GROUP models run through the operational GROUP path in Flow 1.

Module: `adapters/forecast_interface.py`

---

### Recap Gateway polygon metadata and client boundary

SAP3-owned typed metadata and structural Protocols for the `recap-dg-client` Data
Gateway forcing adapters. These let `adapters/recap_gateway.py` name **no**
`recap-dg-client` symbol — the injected client is duck-typed structurally, and returned
DataFrames are validated at the boundary. Empirically grounded in the private
`hydrosolutions/recap-dg-client` clone; the authoritative, empirically-grounded adapter
contract lives in Plan 081 (§Contract-Fit Review and §Adapter Decisions).

```python
GatewayHruName = NewType("GatewayHruName", str)      # registered HRU/gpkg filename
GatewayPolygonName = NewType("GatewayPolygonName", str)  # per-polygon Gateway feature name

@dataclass(frozen=True, kw_only=True, slots=True)
class GatewayPolygonRef:
    hru_name: GatewayHruName
    polygon_name: GatewayPolygonName
    station_id: StationId
    spatial_type: SpatialRepresentation
    band_id: int | None  # None for BASIN_AVERAGE (Nepal v1 is basin-average-only)

@runtime_checkable
class GatewayPolygonResolver(Protocol):
    def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef | None: ...
```

`GatewayPolygonResolver` is a 1:1, basin-average-only resolver (a basin-average station
occupies exactly one polygon, `band_id is None`). A `None` return is a resolver **miss**
(unmappable / not-yet-onboarded station) that the adapter **skips-and-logs**; the concrete
production resolver is owned by DHM onboarding (Flow 5), not by Plans 081/082. The
elevation-band widening (list return) is a deferred future seam.

```python
@runtime_checkable
class EcmwfApiLike(Protocol):
    def ifs_forecast(self, *, variable: str, run_date: object, hru_code: str,
                     ifs_type: str, member: str | None = None,
                     **kwargs: object) -> object: ...
    def era5_land_reanalysis(self, *, variable: str, start_date: object,
                             end_date: object | None = None, hru_code: str,
                             **kwargs: object) -> object: ...

@runtime_checkable
class SnowApiLike(Protocol):
    def reanalysis(self, *, hru_code: str, variable: str, start_date: object,
                   end_date: object, **kwargs: object) -> object: ...

@runtime_checkable
class RecapClientLike(Protocol):
    ecmwf: EcmwfApiLike
    snow: SnowApiLike
```

`RecapClientLike` (+ its `EcmwfApiLike` / `SnowApiLike` sub-Protocols) describes exactly the
injected-client call surface both adapters use. All four are `@runtime_checkable`. The
`recap-dg-client` error classes are intentionally **not** SAP3 types — the error mapper reads
their discriminators structurally via `getattr` and takes a plain `BaseException`.

`GatewayResolutionError(AdapterError)` carries a typed `station_id` (the `DiskSoftLimitError`
typed-kwargs precedent) and is raised by an adapter **only** when every station in a batch is
unmappable; per-station resolver misses are skipped-and-logged, not raised.

Module: `adapters/recap_gateway.py`

### RecapGatewayForecastAdapter

Wraps an injected `RecapClientLike` and satisfies `WeatherForecastSource`
(`fetch_forecasts(...) -> dict[StationId, WeatherForecastResult]`) without changing the
Protocol signature. `NWP_SOURCE: ClassVar[str] = "ifs_ecmwf"` — the forecast storage key that
every produced forecast record's `nwp_source` must equal (correct-by-construction: this is the
single forecast-path value). Fetches ECMWF IFS forecasts (HRES `fc` = `member_id=0`, `pf`
1..50 = `member_id=1..50` → 51-member ENS), converting units at the boundary (K→°C, m→mm).

Module: `adapters/recap_gateway.py`

### RecapGatewayReanalysisAdapter

Wraps an injected `RecapClientLike` and satisfies `WeatherReanalysisSource`
(`fetch_reanalysis(...) -> list[RawHistoricalForcing]`) without changing the Protocol
signature. `NWP_SOURCE: ClassVar[str] = "era5_land"` — the reanalysis selector Flow 6 keys on.
Fetches ERA5-Land (`ecmwf.era5_land_reanalysis`) and historical Snowmapper (`snow.reanalysis`)
forcing, tagging endpoint provenance into `RawHistoricalForcing.source`
(`recap_era5_land_reanalysis` / `recap_snow_reanalysis`).

**Why two adapter classes, not one.** A single adapter carrying both Protocols would need one
`NWP_SOURCE` that is simultaneously the IFS forecast storage key and the ERA5-Land reanalysis
selector — a dual identity impossible to satisfy honestly. Splitting them mirrors the Swiss
path (`MeteoSwissNwpAdapter` forecast-only, `MeteoSwissOpenDataReanalysisAdapter`
reanalysis-only) and makes each `NWP_SOURCE` unambiguous.

`fetch_snow_reanalysis(station_configs, start, end, variables=None) -> SnowReanalysisFetchResult`
(Plan 146 D5) is a NEW method, deliberately NOT part of `WeatherReanalysisSource` — mirrors
`RecapGatewayForecastAdapter.fetch_snow_forecast`'s reasoning: per-`(hru, variable)`
containment of a Gateway `source_data_missing`/`subscription_not_found` gap, which the
Protocol-locked `list[RawHistoricalForcing]` return of `fetch_reanalysis` cannot express.
`SnowReanalysisFetchResult` (`types/weather.py`) additionally carries `attempted` (the
per-HRU request denominator), `resolved` (station → HRU, authoritative), and `skipped`
(station → drop reason) — the dedicated `ingest-recap-reanalysis` flow's health
classification and station-resolution reconciliation. `RawHistoricalForcing.source` for a
Snowmapper row is `recap_snow_reanalysis` — the persisted literal backing the
`ForcingSource.RECAP_SNOW_REANALYSIS` provenance member (`types/forcing_sources.py`).

Module: `adapters/recap_gateway.py`

### §5a mapping table + store-backed resolver (Plan 082 Task 2D)

`GatewayPolygonResolver` (above) has a Protocol only in Plan 081. Plan 082 ships the
concrete production resolver, backed by an additive persistence table
(`docs/requirements/04-basin-static-artifact-contract.md` §5a):

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class GatewayPolygonBindingRow:
    station_id: StationId
    basin_id: BasinId
    gateway_hru_name: str
    name: str
    spatial_type: SpatialRepresentation
    band_id: int | None

@runtime_checkable
class GatewayPolygonBindingStore(Protocol):
    def fetch_bindings_for_station(self, station_id: StationId) -> list[GatewayPolygonBindingRow]: ...
    def store_binding(self, binding: GatewayPolygonBindingRow) -> None: ...

class StoreBackedGatewayPolygonResolver:
    def __init__(self, store: GatewayPolygonBindingStoreLike) -> None: ...
    def resolve(self, source: StationWeatherSource) -> GatewayPolygonRef | None: ...
```

Table `recap_gateway_polygon_bindings`, keyed `station_id + gateway_hru_name + name`
(migration `0032`). Schema + reader owned by Plan 082; **rows populated by Plan 120**'s
basin/static package importer — until populated, `resolve()` returns `None` for every
station (fixture-tested now; production readiness gated on Plan 120). Only
`BASIN_AVERAGE` bindings resolve (Recap v1 is basin-average-only); a station with only
`ELEVATION_BAND` rows resolves to `None`.

Modules: `types/station.py` (`GatewayPolygonBindingRow`), `protocols/stores.py`
(`GatewayPolygonBindingStore`), `store/recap_gateway_polygon_store.py`
(`RecapGatewayPolygonStore`), `adapters/recap_gateway.py`
(`StoreBackedGatewayPolygonResolver`).

### RecapAuthError + snow-forecast fetch (Plan 082 Task 2G / 2H-snow)

`RecapAuthError(AdapterError)` carries `status_code: int | None`; `_map_recap_error` maps
`getattr(exc, "status_code", None) in (401, 403)` to it (checked after the
`source_data_missing`/config-error discriminators, so a structured validation error with
an incidental 401 still maps to `RecapConfigurationError`, the more specific category).

`SnowApiLike` widens with `forecast(*, hru_code, variable, run_date, run_hour: int = 0,
**kwargs) -> object`, matching the client's `snow.forecast` (0/6/12/18Z).
`RecapGatewayForecastAdapter.fetch_snow_forecast(station_configs, cycle_time,
required_snow: Mapping[StationId, frozenset[str]] | None = None) ->
SnowForecastFetchResult` (`types/weather.py`) is a SEPARATE method, **not** part of
`WeatherForecastSource` — exposed to callers via the narrow `SnowForecastSource`
capability Protocol (`protocols/adapters.py`, `isinstance`-gated, requires
`required_snow`). Snow forecasts are deterministic (`member_id=None`), fetched
independently from the 51-member IFS `fetch_forecasts`. `required_snow` (Plan 145
wiring, review fold-in) scopes each HRU's Gateway calls to the UNION of canonical
variables its resolved stations actually need — a station requiring only `swe` never
triggers an `hs`/`rof` call; `None` (the default) preserves the unscoped
fetch-every-variable behaviour for callers without a precomputed map. No
resample/broadcast happens in the adapter; `services/operational_inputs.py
._broadcast_deterministic_features_to_members` performs the daily-snow → sub-daily
51-member IFS broadcast at model-input-assembly time. Wired into the Flow-1 Phase-A
task (`_fetch_nwp_task`, `flows/run_forecast_cycle.py`) behind the `SnowForecastSource`
capability check; storage + broadcast + the `snow_unavailable` cycle outcome are all
live (Plan 145).

Module: `adapters/recap_gateway.py`, `protocols/adapters.py`, `types/weather.py`,
`services/operational_inputs.py`, `flows/run_forecast_cycle.py`.

### RecapGatewayConfig + coverage manifest (Plan 082 Tasks 2A / Phase 3)

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class RecapGatewayConfig:
    base_url: str
    timeout_s: int
    verify_tls: bool
    staleness_threshold_hours: float
    hru_metadata_source: str
    max_retries: int
    # Optional; default DEFAULT_MAX_CYCLE_AGE_HOURS = 18.0 (3 IFS cycles).
    # The fallback probe bound: how far `resolve_latest_cycle` walks back from
    # the nominal IFS cycle before degrading to runoff-only (Task 2B/2D).
    max_cycle_age_hours: float = 18.0

def load_recap_api_key(*, secret_path: Path | None = None) -> str: ...
def build_recap_client_config(*, api_key: str, config: RecapGatewayConfig) -> ApiClientConfig: ...
def load_recap_gateway_config(config_path: Path) -> RecapGatewayConfig: ...
```

`load_recap_api_key` reads the `sapphire_dg_api_key` Docker secret file (default
`/run/secrets/sapphire_dg_api_key`), falling back to the `RECAP_API_KEY` env var for
local dev — never logged. `[adapters.recap_gateway]` TOML section is validated
independently of `[adapters.weather_forecast]`'s MeteoSwiss-only fields (Task 2C's
`type` selector branches on this).

Coverage (Gateway exposes none — SAP3-side supervised manifest):

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class GatewayCoverageKey:  # member_id deliberately NOT part of the key
    gateway_hru_name: str
    name: str
    dataset: str
    variable: str
    band_id: int | None

@dataclass(frozen=True, kw_only=True, slots=True)
class GatewayCoverageSpan:
    start: UtcDatetime
    end: UtcDatetime

def coverage_spans_window(manifest, requested_window, required_keys: list[GatewayCoverageKey]) -> bool: ...
def assert_returned_span_covers_request(requested: GatewayCoverageSpan, returned: GatewayCoverageSpan) -> None: ...
```

`coverage_spans_window` is the **training-readiness gate** (Flow 6): a required key
absent from the manifest is refused, never inferred. `GatewayCoverageManifest` has no
constructor path that derives a span from row counts/non-empty data — every entry
requires an explicit `start`/`end` in the supervised manifest row. Short auto-coverage
is a signal, not an irreversible block: the existing `promote_artifact` manual-promotion
authority still applies. `assert_returned_span_covers_request` HARD-BLOCKS (raises) only
on the **training** path; the **operational** forecast path logs a WARNING and continues
(matching the `operational_inputs.no_nwp` graceful-degrade precedent) — it must never call
this assertion.

Module: `config/recap_gateway.py`, `services/gateway_coverage.py`.

---

### ModelAlertStrategy Protocol

Pluggable strategy for combining or selecting model ensembles before threshold evaluation.
Implementations correspond to `ModelCombinationStrategy` enum values. Registered via `DeploymentConfig.alert_model_strategy`.

```python
@runtime_checkable
class ModelAlertStrategy(Protocol):
    def evaluate(
        self,
        station_id: StationId,
        parameter: ForecastParameter,
        model_ensembles: dict[ModelId, ForecastEnsemble],
        thresholds: list[StationThreshold],
        danger_levels: list[DangerLevelDefinition],
        priorities: dict[ModelId, int],
    ) -> list[ExceedanceResult]: ...
```

Module: `protocols/alert_strategy.py`

---

### Station onboarding types

Result type for Flow 5 station onboarding.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class OnboardingResult:
    stations_created: int
    stations_skipped: int
    basins_created: int
    basins_skipped: int
    observations_imported: int
    forcing_records_imported: int
    observations_qc_passed: int
    observations_qc_failed: int
    observations_qc_suspect: int
    baselines_computed: int
    flow_regimes_computed: int
    errors: list[str]
    model_assignments_created: int = 0
    models_trained: int = 0
    stations_marked_operational: int = 0
    stations_updated: int = 0          # stations whose mutable metadata was updated (idempotent re-runs)
```

Module: `types/onboarding.py`

---

### Model onboarding types

Result types for Flow 13 model onboarding.

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class CompatibilityReport:
    model_id: ModelId
    station_id: StationId | None        # non-null for station-scoped compatibility checks
    group_id: StationGroupId | None     # non-null for group-scoped compatibility checks
    protocol_conforms: bool
    missing_target_parameters: frozenset[str]   # params station needs but model can't provide
    missing_past_dynamic: frozenset[str]         # features model needs but station lacks
    missing_future_dynamic: frozenset[str]
    missing_static_features: frozenset[str]
    time_step_compatible: bool

    def __post_init__(self) -> None:
        if (self.station_id is None) == (self.group_id is None):
            raise ValueError("Exactly one of station_id or group_id must be set")

    @property
    def is_compatible(self) -> bool:
        return (
            self.protocol_conforms
            and not self.missing_target_parameters
            and not self.missing_past_dynamic
            and not self.missing_future_dynamic
            and not self.missing_static_features
            and self.time_step_compatible
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class SkillGateResult:
    artifact_id: ArtifactId
    metric_scores: tuple[tuple[str, float], ...]  # metric_name → score
    thresholds: tuple[tuple[str, float], ...]     # metric_name → required threshold
    failing_metrics: frozenset[str]               # metrics that did not meet threshold

    def __post_init__(self) -> None:
        score_keys = {k for k, _ in self.metric_scores}
        if len(score_keys) != len(self.metric_scores):
            raise ValueError("Duplicate metric name in metric_scores")
        thresh_keys = {k for k, _ in self.thresholds}
        if len(thresh_keys) != len(self.thresholds):
            raise ValueError("Duplicate metric name in thresholds")

    @property
    def passed(self) -> bool:
        return not self.failing_metrics


# OnboardingUnit has been removed. Use TrainingUnit from types/training.py instead.
# TrainingUnit carries the same fields (model_id, station_id | None, group_id | None,
# station_ids, training_period_start, training_period_end, time_step) with the XOR
# invariant: "Exactly one of station_id or group_id must be set".


@dataclass(frozen=True, kw_only=True, slots=True)
class OnboardingUnitResult:
    unit: TrainingUnit
    outcome: OnboardingOutcome
    compatibility: CompatibilityReport
    artifact_id: ArtifactId | None
    hindcast_steps: tuple[HindcastStepResult, ...]
    skill_gate: SkillGateResult | None
    error: str | None = None


ONBOARDING_FAILED_OUTCOMES = frozenset({
    OnboardingOutcome.FAILED_SMOKE_TEST,
    OnboardingOutcome.FAILED_TRAINING,
    OnboardingOutcome.FAILED_HINDCAST,
    OnboardingOutcome.FAILED_SKILL,
    OnboardingOutcome.FAILED_ASSIGNMENT,
})
ONBOARDING_SKIPPED_OUTCOMES = frozenset({
    OnboardingOutcome.SKIPPED_COMPAT,
    OnboardingOutcome.SKIPPED_NO_DATA,
    OnboardingOutcome.SKIPPED_INSUFFICIENT_EVAL,
})


@dataclass(frozen=True, kw_only=True, slots=True)
class ModelOnboardingResult:
    model_id: ModelId
    units: tuple[OnboardingUnitResult, ...]

    def __len__(self) -> int:
        return len(self.units)

    def promoted_count(self) -> int:
        return sum(1 for u in self.units if u.outcome == OnboardingOutcome.PROMOTED)

    def failed_count(self) -> int:
        return sum(1 for u in self.units if u.outcome in ONBOARDING_FAILED_OUTCOMES)

    def skipped_count(self) -> int:
        return sum(1 for u in self.units if u.outcome in ONBOARDING_SKIPPED_OUTCOMES)

    def gate_rejected_count(self) -> int:
        return sum(1 for u in self.units
                   if u.outcome == OnboardingOutcome.GATE_REJECTED)
```

Module: `types/model_onboarding.py`

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

class ModelOutputError(SapphireError):
    """Model ran but produced zero convertible ensembles.
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

class UnsupportedModelRequirementError(SapphireError):
    """A ForecastInterface model's InputRequirement declares non-empty
    future_known in more than one time_step branch (Plan 156) — SAP3 domain
    types are single-resolution. Deliberately NOT a ConfigurationError
    subclass: discover_models() re-raises ConfigurationError for every entry
    point (a registry-wide blackout); this is instead skipped per entry
    point so one unsupported model does not darken discovery for the rest."""

class ModelSmokeTestError(SapphireError):
    """Model raised an exception during smoke test (predict() on random-shaped data).
    Flow 13 handling: unit outcome = FAILED_SMOKE_TEST; remaining units continue."""

class ArtifactIntegrityError(SapphireError):
    """SHA-256 hash of fetched artifact bytes does not match stored hash.
    Flow-level handling: do not deserialize; raise to trigger task failure."""
```

#### ObservationStore

```python
class ObservationStore(Protocol):
    def store_observations(self, observations: list[Observation]) -> None: ...
    def store_raw_observations(self, observations: list[RawObservation]) -> list[ObservationId]: ...
        # Inserts raw observations (pre-QC) with qc_status=RAW. Returns IDs of newly inserted
        # rows; rows matching an existing natural key (station_id, timestamp, parameter, source)
        # are silently skipped via ON CONFLICT DO NOTHING.
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
        parameter: str | None = None,
    ) -> OperationalForecast | None: ...
    def fetch_forecasts_for_cycle(
        self,
        issued_at: UtcDatetime,
        station_id: StationId | None = None,  # None = all stations
        parameter: str | None = None,
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
        parameter: str | None = None,
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
        parameter: str | None = None,              # None = all parameters
    ) -> list[HindcastForecast]: ...
    def fetch_hindcasts_by_station(
        self,
        station_id: StationId,
        parameter: str,
        start: UtcDatetime,
        end: UtcDatetime,
        forcing_type: ForcingType | None = None,
    ) -> dict[ModelId, list[HindcastForecast]]: ...
        # Returns all models' hindcasts for a station, grouped by model_id.
        # Used by combined skill computation (step S.4b) to retrieve multi-model hindcasts.
        # Excludes sentinel/virtual model IDs. None forcing_type = all types.
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
        parameter: str | None = None,
    ) -> list[SkillScore]: ...
        # Returns scores for the latest computation_version.
    def fetch_latest_diagrams(
        self,
        station_id: StationId,
        model_id: ModelId,
        diagram_type: Literal["reliability", "roc", "rank_histogram"] | None = None,
        parameter: str | None = None,
    ) -> list[SkillDiagram]: ...
    def fetch_scores_by_regime(
        self,
        station_id: StationId,
        model_id: ModelId,
        flow_regime: FlowRegime,
        parameter: str | None = None,
    ) -> list[SkillScore]: ...
    def fetch_skill_scores(
        self,
        model_id: ModelId,
        model_artifact_id: ArtifactId,
        parameter: str | None = None,  # None = all parameters
    ) -> list[SkillScore]: ...
        # Artifact-scoped query — returns all skill scores for a specific artifact.
        # Distinct from fetch_latest_scores (station-scoped by computation_version) and
        # fetch_scores_by_regime (station + regime scoped). Used by Flow 13 skill gate
        # to evaluate scores produced for the just-trained artifact.
    def mark_stale(
        self,
        station_id: StationId,
        start: UtcDatetime,
        end: UtcDatetime,
        parameter: str | None = None,
    ) -> int: ...
        # Sets freshness=STALE on all skill_scores rows for this station
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
        status: ModelArtifactStatus = ModelArtifactStatus.TRAINING,  # explicit default; callers may pass TRAINING
    ) -> StoredArtifact: ...
        # Exactly one of station_id or group_id must be provided.
        # Returns a StoredArtifact (types/model.py) -- iterates as the historical
        # (id, sha256_hash) 2-tuple for every pre-existing 2-value-unpacking call
        # site; also exposes .artifact_path, known atomically with the write
        # (Plan 157 T3 fixer round), so a caller needing it for failure cleanup
        # no longer needs a separate fetch_artifact_record query.
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

##### Basin lineage write (Plan 120 Task 2D)

`record_artifact_basin_lineage(conn, artifact_id, trained_station_ids)`
(`sapphire_flow.store.model_artifact_lineage`) is a **standalone helper, not
a `ModelArtifactStore` method** — it deliberately does not widen that
Protocol. Flows call it right after `store_artifact`/`store_and_promote_artifact`
returns, on the same connection, writing one `model_artifact_basin_versions`
row per basin the artifact actually trained on (the TRAINED subset —
`trained_station_ids`, not every station requested):

- `stations.basin_id IS NULL` → **skip** that station's lineage row
  (INFO-logged, no raise). The D-UP prerequisite gate in
  `services/training_data.py` means a model that *requires* static features
  can never reach this helper with a null basin, so this branch only fires
  when static features were never required.
- A basin with no current (`superseded_at IS NULL`) `basin_versions` row
  (dangling `basin_id`, or a Task 0A invariant violation) → **raise**
  (`ValueError`). Silently swallowing this would defeat the Decision-B
  stale-basin retrain SLA.
- **Non-atomic and log-loud on failure, deliberately**: matches the
  pre-existing store+promote relationship, which is already non-atomic under
  the AUTOCOMMIT connection flows run on in production.

`PgArtifactLineageWriter(conn)` is the thin flow-facing adapter (`.record(...)`)
that `train_models_flow`/`onboard_model_flow` inject as `lineage_writer`.
`services.model_onboarding.onboard_model` accepts the same optional
`lineage_writer` and calls it right after `store_artifact` — `services.
onboarding._run_onboarding`/`onboard_from_camelsch` thread it through from
`flows/onboard.py::onboard_stations_flow`'s `stores["lineage_writer"]`, so the
station-onboarding path records lineage too, not just the two Prefect-flow
call sites. Tests inject `tests.fakes.fake_stores.FakeArtifactLineageWriter`
with the same shape.

##### External-artifact import + provenance write (Plan 157 T3)

`record_artifact_provenance(conn, provenance)`
(`sapphire_flow.store.model_artifact_provenance`) is, like the basin-lineage
helper above, a **standalone helper, not a `ModelArtifactStore` method**.
`PgArtifactProvenanceStore(conn)` is its thin flow-facing adapter
(`.record(...)`/`.fetch(...)`); tests inject
`tests.fakes.fake_stores.FakeArtifactProvenanceStore` with the same shape.

`import_external_artifact` (`sapphire_flow.services.model_import`) is the
executable entry point that closes G4/G7 — Flow 13
(`flows/onboard_model.py`) has no path for an artifact SAP3 did not train.
Before any write it: derives the target tenant from the station/group being
imported into (never trusts a caller-supplied tenant id); asserts the
discovered model's declared `artifact_scope` matches the station_id/group_id
actually supplied (a GROUP-scoped model imported against a station_id, or
vice versa, is rejected — it would otherwise report success while being
invisible to whichever lookup path queries by the OTHER scope); registers
the model in `models` if this is its first import (an idempotent
`ON CONFLICT DO NOTHING`, mirroring `register_models`, followed by a
re-read of the authoritative row — a concurrent worker could have inserted
a DIFFERENT scope between the initial check and this insert, and a stale
in-hand assumption must not survive that race), or — if a `models` row
already exists — verifies its `artifact_scope` still matches what the
model declares today, rejecting registry drift across a shim upgrade;
requires BOTH the model's own declared `config_hash` and the caller-
supplied `expected_config_hash` to be present and equal — there is no
warning-only bypass; a missing or mismatched value fails loudly, before any
write (Plan 157 T3 fixer round closed a review finding that a caller who
simply omitted `expected_config_hash` got NO drift check at all); and calls
`model.deserialize_artifact`. Only then — with a unit of work that is
ALWAYS present (a real `AuditedWriter`, `store/audited_writer.py`, extended
here with `provenance_store` AND `model_store` entries, in production; an
in-memory snapshot/restore wrapper around the caller's fakes,
`_InMemoryAuditedWriter`, in the test/replay fallback — no caller,
including a test using fakes, can bypass all-or-nothing) — does it run
model registration + `store_artifact` + the provenance write +
`promote_artifact` in ONE unit of work: on any failure no artifact row, no
provenance row, no `models` row (for a fresh model), and no orphaned
artifact file survive (`PgModelArtifactStore.store_artifact` also
self-cleans: if its own INSERT fails — e.g. an FK violation on `model_id`
— it deletes the file it just wrote before re-raising, so a caller who
never learns the generated artifact id still cannot leak a file). It never
calls `model.train`.

`training_period_start`/`training_period_end` (the REAL external training
window) are separate, REQUIRED parameters, distinct from `trained_at` (the
external training-COMPLETION instant) and `imported_at` (this import's own
instant, `clock()`) — none of the three is ever synthesized from another;
earlier code fabricated a zero-length training period at `trained_at`,
which this closes.

An FI model's `config_hash` (a SAP3-side convention, not part of the FI
protocol) is forwarded through `ForecastInterfaceAdapter.config_hash` — a
property added specifically so the drift check above sees what the wrapped
model declares rather than silently reading `None` off the adapter.

`ModelArtifactStore.store_artifact` returns a `StoredArtifact` value
(`types/model.py`) rather than a bare `(ArtifactId, str)` tuple — it
iterates as that same 2-tuple for every pre-existing 2-value-unpacking call
site, but also exposes `.artifact_path`, known atomically with the write,
so `import_external_artifact`'s failure-cleanup path no longer needs a
separate (fallible) `fetch_artifact_record` query just to learn what to
delete.

Deployed as the standalone `import-model-artifact` Prefect deployment
(`flows/import_model_artifact.py`, no cron — `cli/register_deployments.py`,
`forecast-cycle` pool — see `docs/standards/cicd.md` § The `forecast-cycle`
pool for why). The deployment's top-level parameter is `artifact_base64:
str`, not `artifact_bytes: bytes` — a Prefect deployment's parameters cross
the wire as JSON, and a `bytes`-typed parameter does NOT base64-decode a
JSON string (pydantic's JSON-mode `bytes` validation does `str.encode()`),
so it cannot transport an arbitrary binary checkpoint. The flow decodes
strictly (`base64.b64decode(..., validate=True)`) before calling the
service.

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

#### TenantStore

```python
class TenantStore(Protocol):
    # Plan 147 Slice A: the tenant-model foundation root.
    def fetch_tenant(self, tenant_id: TenantId) -> Tenant | None: ...
    def fetch_tenant_by_code(self, code: str) -> Tenant | None: ...
    def fetch_all_tenants(self) -> list[Tenant]: ...
    def store_tenant(self, tenant: Tenant) -> TenantId: ...
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
    def update_station(self, station: StationConfig) -> None: ...
        # Updates mutable metadata fields (name, location, measured_parameters, forecast_targets)
        # for an existing station. Identity fields (id, code, network) are not modified.
    def fetch_thresholds(self, station_id: StationId) -> list[StationThreshold]: ...
    def store_thresholds(self, thresholds: list[StationThreshold]) -> None: ...
        # Upsert keyed on (station_id, danger_level, parameter).
    def fetch_model_assignments(self, station_id: StationId) -> list[ModelAssignment]: ...
    def store_model_assignment(self, assignment: ModelAssignment) -> None: ...
        # Upsert keyed on (station_id, model_id).
    def fetch_weather_sources(self, station_id: StationId) -> list[StationWeatherSource]: ...
        # Returns all bindings regardless of role. Display-only (api/routes/api_stations.py);
        # routing consumers use the role-scoped accessors below (Plan 115a).
    def store_weather_source(self, source: StationWeatherSource) -> None: ...
        # Upsert keyed on (station_id, nwp_source).
    def fetch_forecast_binding(self, station_id: StationId) -> StationWeatherSource: ...
        # Exactly one FORECAST binding, else raises ConfigurationError (0 or >=2 bindings).
    def fetch_reanalysis_bindings(self, station_id: StationId) -> list[StationWeatherSource]: ...
        # 0..n REANALYSIS bindings. No status filter — an INACTIVE binding is still selected
        # (deliberate; deactivation semantics are a separate, later decision).
    def assign_basin(self, station_id: StationId, basin_id: BasinId) -> None: ...
        # Plan 120 fixer round: single-column UPDATE binding a station's operational
        # identity (stations.basin_id) to an imported/corrected basin. Conflict/no-op
        # decisions live in the caller (store/basin_importer.py::_assign_station_basin),
        # not here.
```

#### StationGroupStore

```python
class StationGroupStore(Protocol):
    def store_group(self, group: StationGroup) -> None: ...
        # Upsert keyed on group_id. Replaces membership.
    def fetch_group(self, group_id: StationGroupId) -> StationGroup | None: ...
    def fetch_group_by_name(self, tenant_id: TenantId, name: str) -> StationGroup | None: ...
        # Plan 147 Slice A: name is unique PER TENANT, not globally.
    def fetch_groups_for_station(self, station_id: StationId) -> list[StationGroup]: ...
        # All groups this station belongs to.
    def fetch_groups_for_model(self, model_id: ModelId) -> list[StationGroup]: ...
        # All groups that have an active group-level assignment for this model.
    def add_station_to_group(self, group_id: StationGroupId, station_id: StationId) -> None: ...
    def remove_station_from_group(self, group_id: StationGroupId, station_id: StationId) -> None: ...
    def fetch_group_model_assignments(self, group_id: StationGroupId) -> list[GroupModelAssignment]: ...
    def store_group_model_assignment(self, assignment: GroupModelAssignment) -> None: ...
        # Upsert keyed on (group_id, model_id).
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

#### AuditLogStore

Plan 147 Slice B: the append-only audit substrate. ONLY an insert — no update/delete method, matching
the role-independent DB guard (migration 0046).

```python
class AuditLogStore(Protocol):
    def append_entry(self, entry: AuditEntry) -> None: ...
```

#### AccessTokenStore

Plan 147 Slice C: `access_tokens` + `access_token_stations` scope. `create_token` validates every
scoped station belongs to the token's own `tenant_id` (R2), raising `CrossTenantScopeError` (a
`ValueError` subclass) on a cross-tenant station id — never silently dropping it.

```python
class AccessTokenStore(Protocol):
    def create_token(self, token: AccessToken, *, station_ids: frozenset[StationId]) -> None: ...
    def fetch_by_key_prefix(self, key_prefix: str) -> AccessToken | None: ...
    def fetch_token(self, token_id: AccessTokenId) -> AccessToken | None: ...
    def fetch_all_tokens(self) -> list[AccessToken]: ...
    def revoke_token(self, token_id: AccessTokenId, *, revoked_at: UtcDatetime) -> None: ...
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
    def fetch_curves_in_range(self, station_id: StationId, start: UtcDatetime, end: UtcDatetime) -> list[RatingCurve]: ...
        # Curves overlapping the half-open [start, end) window (Flow 8/10 epoch queries). Plan 035 Task 1.
    def fetch_active_curves_batch(self, station_ids: list[StationId]) -> dict[StationId, RatingCurve]: ...
        # Active curve (valid_to IS NULL) per station, one query (Flow 1 batch lookup). Plan 035 Task 1.
    def fetch_active_curves_batch_at(self, station_ids: list[StationId], at: UtcDatetime) -> dict[StationId, RatingCurve]: ...
        # Curve active AT `at` (valid_from <= at < valid_to) per station — issued-at-aware
        # binding at forecast storage (Flow 1). Plan 035 Task 4.
```

#### ObservationVersionStore

```python
class ObservationVersionStore(Protocol):  # v1 — Plan 035 Task 3
    def archive_observation_values(self, observations: Sequence[Observation], superseded_by_curve_id: RatingCurveId) -> int: ...
        # Archive rating-curve-derived observations before Flow 12 Branch A reprocessing.
        # Idempotent per (observation_id, rating_curve_id); returns rows actually inserted.
    def fetch_archived_values(self, station_id: StationId, parameter: str, start: UtcDatetime, end: UtcDatetime, rating_curve_id: RatingCurveId | None = None) -> Sequence[ArchivedObservationValue]: ...
        # Archived values in half-open [start, end), optionally filtered by producing curve.
```

#### FormulaStore

```python
class FormulaStore(Protocol):  # v1 — Plan 015: calculated-station weighted-sum formulas
    def store_formula(self, rows: Sequence[ComponentWeight]) -> None: ...
        # Insert the component-weight rows of one formula version. All rows share the same
        # calculated_station_id + parameter + effective_from.
    def close_formula(self, calculated_station_id: StationId, parameter: str, effective_to: UtcDatetime) -> int: ...
        # Close the current (effective_to IS NULL) rows for a station+parameter; returns rows closed.
    def fetch_current_formula(self, calculated_station_id: StationId, parameter: str) -> Sequence[ComponentWeight]: ...
        # The current (effective_to IS NULL) rows for a station+parameter.
    def fetch_formula_at(self, calculated_station_id: StationId, parameter: str, at: UtcDatetime) -> Sequence[ComponentWeight]: ...
        # Formula valid at `at`: per component the row with the greatest effective_from <= at
        # whose validity covers `at` (deterministic latest-wins).
    def fetch_formulas_for_stations(self, station_ids: list[StationId]) -> dict[tuple[StationId, str], list[ComponentWeight]]: ...
        # Current formulas for the given calculated stations, grouped by (station_id, parameter).
        # One query for the Flow 2 step-2.5 pre-fetch.
```

#### FlowRegimeConfigStore

```python
class FlowRegimeConfigStore(Protocol):
    def store_config(self, config: FlowRegimeConfig) -> None: ...
    def fetch_latest(self, station_id: StationId, parameter: str) -> FlowRegimeConfig | None: ...
        # Returns the config with the highest version for this station+parameter.
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

#### HistoricalForcingStore

Persistence layer for historical weather forcing used by model training (Flow 6) and hindcast generation (Flow 7). Supports multi-source, multi-version, and ensemble reanalysis storage.

```python
@runtime_checkable
class HistoricalForcingStore(Protocol):
    def store_forcing(self, records: list[HistoricalForcingRecord]) -> None: ...
        # Upsert keyed on natural key (station_id, source, version, valid_time,
        # parameter, spatial_type, band_id, member_id).
    def fetch_forcing(
        self,
        station_id: StationId,
        source: str,
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str] | None = None,   # None = all parameters
        version: str | None = None,            # None = latest version
        member_id: int | None = None,          # None = all members
    ) -> list[HistoricalForcingRecord]: ...
    def fetch_forcing_as_dataframe(
        self,
        station_id: StationId,
        source: str,
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str] | None = None,
        version: str | None = None,
    ) -> pl.DataFrame | None: ...
        # Returns None if no data found. DataFrame columns: valid_time + one column
        # per parameter (deterministic) or valid_time + parameter + member_id (ensemble).
    def fetch_available_sources(self, station_id: StationId) -> list[str]: ...
        # Returns distinct source strings for the station. Used during training scope
        # determination to verify forcing is available before launching a training run.
    def fetch_covered_days(
        self,
        station_ids: list[StationId],
        source: str,
        parameter: str,
        spatial_type: SpatialRepresentation,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> dict[StationId, set[date]]: ...
        # Plan 115b2 §3C — resumable gap-detection presence check for the
        # chunked MeteoSwiss backfill. For each station_id, the set of
        # calendar days (UTC, from valid_time) already stored for
        # (source, parameter, spatial_type) within [start, end) — regardless of
        # version (a day counts as "covered" if ANY version exists for it).
        # Every requested station_id is present in the result (empty set if it
        # has no rows). SCOPE NOTE: the plan's full LOGICAL key also includes
        # band_id + member_id, but this method serves only the BASIN_AVERAGE
        # backfill where both are always None, so it narrows the key to
        # (station_id, source, parameter, spatial_type). Extend the signature
        # before reusing it for an elevation-band or ensemble source.
    def fetch_latest_valid_time(
        self,
        station_ids: list[StationId],
        source: str,
        start: UtcDatetime,
        end: UtcDatetime,
    ) -> UtcDatetime | None: ...
        # Plan 115b4 §6B — health-by-EFFECT: the single latest valid_time
        # stored for `source` across ALL of station_ids within [start, end) —
        # an O(1) aggregate query (SELECT MAX(valid_time) ... WHERE
        # station_id IN (...)), NOT an O(stations) loop over fetch_forcing.
        # None when nothing is stored for this source/window. Used by
        # ingest_weather_history_flow to detect a run with zero EFFECT even
        # when the run "successfully" re-persisted already-covered rows
        # (rows_stored would look healthy; this would not).
```

Module: `protocols/stores.py`

#### BasinStore

```python
class BasinStore(Protocol):
    def fetch_basin(self, basin_id: BasinId) -> Basin | None: ...
    def fetch_basin_by_code(self, code: str, network: str) -> Basin | None: ...
    def fetch_all_basins(self) -> list[Basin]: ...
    def store_basin(
        self,
        basin: Basin,
        *,
        package_id: PackageId | None = None,
        gateway_mapping: list[dict[str, Any]] | None = None,
    ) -> BasinId: ...
```

`store_basin` is the SINGLE basin-creation path (v1 — Plan 120 Task 0A): it
atomically writes the `basins` projection row AND its paired `version=1,
superseded_at IS NULL` `basin_versions` row in ONE data-modifying CTE, so
the pair is atomic even on an AUTOCOMMIT connection. Called with
`package_id=None` by station onboarding (the legacy/non-package sentinel)
and with `package_id` set by the basin/static package importer.

#### ParameterStore

```python
class ParameterStore(Protocol):
    def fetch_all(self) -> list[ParameterDefinition]: ...
    def fetch_by_name(self, name: str) -> ParameterDefinition | None: ...
    def register(self, definition: ParameterDefinition) -> None: ...
    # Idempotent upsert. Called by Flow 0 step 0.6 to register deployment-
    # specific parameters from config TOML. Updates display_name, unit, and
    # aggregation_method if the parameter already exists; does not delete
    # parameters absent from config (seed data is preserved).
```

#### NwpGridStore

Filesystem-backed store for gridded NWP archives (Zarr format). Used by Flow 1 steps 1.2 (archive) and 1.1-fallback (load previous cycle).

```python
@runtime_checkable
class NwpGridStore(Protocol):
    def archive(self, forecast: GriddedForecast, base_path: Path) -> Path: ...
        # Writes the GriddedForecast to Zarr under base_path.
        # Returns the path to the written Zarr store.
    def load(
        self, base_path: Path, nwp_source: str, cycle_time: UtcDatetime
    ) -> GriddedForecast: ...
        # Loads a previously archived GriddedForecast from Zarr.
        # Raises AdapterError if no archive exists for the given nwp_source + cycle_time.
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
        # Callers discriminate via isinstance(result, GriddedForecast).
```

Two return paths:
- **Gridded NWP** (e.g. ICON-CH2-EPS, ECMWF IFS): returns `GriddedForecast`. The flow layer
  passes this to `GridExtractor.extract()` which bulk-extracts all stations from one grid read.
- **Pre-extracted** (e.g. Data Gateway, point weather stations): returns
  `dict[StationId, WeatherForecastResult]`. Already station-keyed; no extraction step needed.

Callers discriminate between the two return types using `isinstance(result, GriddedForecast)` — the canonical pattern used in `run_forecast_cycle.py`.

> **Forecast-cycle redesign — widened source contract + new types (planned, 2026-07-24).** `docs/design/forecast-cycle-redesign.md` adds a **requirement-aware** capability via a SEPARATE Protocol (adding a required method to `WeatherForecastSource` would make every legacy adapter/fake non-conforming). The adapter factory's **return type** is the dispatch contract — migrated adapters satisfy both; legacy adapters satisfy only `WeatherForecastSource`; the flow selects the per-track path via `isinstance(source, CandidateAwareForecastSource)`. Walk-back **policy** is a pure `services/` concern, never inside the adapter.
>
> ```python
> class CandidateAwareForecastSource(Protocol):
>     def fetch_requirement(
>         self, track: ForcingTrackKey, stations: list[StationWeatherSource], nominal_cycle: UtcDatetime,
>     ) -> RawFetchOutcome: ...   # fetch EXACTLY nominal_cycle; NO adapter-side walk-back.
>       # D31-candidate-ownership (Plan 151 T1 spec edit, owner-ratified 2026-08-11): the
>       # ADAPTER classifies TRANSPORT only (fetched / absent-at-cycle) and never judges
>       # completeness — the threshold is a services/-side ResolvedTrackRequest.fetch_horizons
>       # the adapter cannot see. Returns the raw fetch outcome below, NOT
>       # CandidateFetchResult; services/ constructs the immutable CandidateFetchResult after
>       # applying the per-station completeness predicate (D8). A transient transport
>       # failure or an auth/config error is RAISED, never returned as a status.
>     def expected_member_ids(self, track: ForcingTrackKey) -> frozenset[int]: ...
>       # The source's raw member identity is source-DERIVED, never a literal (Ruling 1):
>       # ECMWF-IFS is fc member 0 + 50 pf = 51; a 21-member ICON-CH2-EPS source would
>       # derive 21. Exact-member-set completeness (D8) is checked against THIS, never a
>       # hardcoded constant.
>
> FeatureName = NewType("FeatureName", str)
>
> @dataclass(frozen=True, kw_only=True, slots=True)
> class FutureSteps:                      # validated wrapper — NOT a NewType (must enforce > 0)
>     value: int
>     def __post_init__(self) -> None:
>         if self.value <= 0: raise ValueError(f"FutureSteps must be > 0, got {self.value}")
>
> # three DISTINCT horizon types (not interchangeable ints)
> FeatureFetchHorizons = Mapping[FeatureName, FutureSteps]
> @dataclass(frozen=True, kw_only=True, slots=True)
> class InputFrameHorizon: steps: FutureSteps
> @dataclass(frozen=True, kw_only=True, slots=True)
> class OutputHorizon:     steps: FutureSteps
> # D23-output-horizon-deferred: OutputHorizon is NOT created by Plan 151 Phase 3 — it
> # has no consumer there (its consumer is the write-side output-horizon follow-on,
> # Plan 148's deferred write-side work). Declared here for completeness of the locked
> # shape; a zero-caller dataclass would be dead code if built early.
> # --- Phase 3 refinement (2026-08): dedup KEY vs per-assignment horizon CARRIER ---
> # ForcingTrackKey is the HASHABLE dedup key: exactly the axes at which ONE recap
> # fetch operates and below which requirements are genuinely equivalent. It carries
> # NO horizon VALUES. Two reasons the old `feature_horizons: FeatureFetchHorizons`
> # field was wrong: (1) it DEFEATED dedup — a 5-day and a 10-day model needing the
> # same feature set at the same source/mode/time_step/spatial got DIFFERENT keys, so
> # the expensive 51-member fetch ran twice; (2) a `Mapping` field makes a
> # frozen/slots dataclass UNHASHABLE, so it could not be a dedup key at all. Horizon
> # is not even a recap fetch axis: one `ecmwf.ifs_forecast(...)` call is keyed by
> # (variable, run_date+run_hour, hru_code, ifs_type=fc|pf, member) and returns the
> # FULL series (recap_gateway.py:899-911) — SAP3 SLICES to a horizon post-fetch.
> #
> # Granularity (grounded in recap fetch + FI InputRequirement):
> #  - ONE ensemble_mode per track. exact-member-set completeness (source-derived, see
> #    expected_member_ids below — never a literal 51) applies to EVERY feature on an
> #    ENSEMBLE track, so a SINGLE-mode feature (fc only) cannot ride it; a mixed-mode
> #    requirement is REJECTED at construction, raising UnsupportedModelRequirementError
> #    (D4 as corrected 2026-08-18; D22 — exactly ONE forcing track per assignment, so
> #    per-mode splitting is a follow-on, not this contract). FI carries ensemble_mode
> #    per future_known variable (forecast_interface.py:474).
> #  - ONE spatial_representation per track (distinct extraction/HRU); FI declares one
> #    spatial per dynamic spec and v1 rejects multi-spatial (forecast_interface.py:488).
> #  - ONE concrete time_step (FI `req.dynamic` is keyed by time_step, :519,:1096).
> #  - feature SET as a frozenset (hashable). Requirements dedup iff their feature sets
> #    are EQUAL. (In v0/v1 the NWP allowlist is {tp, t_2m}, so feature sets are
> #    homogeneous and this dedups every real case.) Sub/superset feature UNION across
> #    tracks is a DEFERRED optimization, NOT this contract — see residual note below.
> @dataclass(frozen=True, kw_only=True, slots=True)
> class ForcingTrackKey:                  # HASHABLE dedup key — NO horizon values
>     nwp_source: str                      # the repo's existing str (types/station.py:106); no
>                                           # NwpSource type exists in the repo (D12)
>     ensemble_mode: EnsembleMode          # existing enum — exactly ONE mode per track
>     time_step: timedelta                 # hashable; the resolved operational step
>     spatial_representation: SpatialRepresentation  # existing enum — exactly ONE per track
>     features: frozenset[FeatureName]     # hashable feature SET — NO per-feature horizons
>
> # ForcingRequired — the per-assignment PROJECTION (before dedup): the track an
> # assignment needs + its OWN per-feature fetch horizons. Invariant:
> # set(assignment_horizons) == key.features.
> @dataclass(frozen=True, kw_only=True, slots=True)
> class ForcingRequired:
>     key: ForcingTrackKey
>     assignment_horizons: FeatureFetchHorizons   # THIS assignment's per-feature horizons
>     assignment: object                   # (station_id|group_id, model_id) key; concrete in build plan
>
> # ResolvedTrackRequest — the dedup RESULT (exactly one per distinct key).
> # fetch_horizons = per-feature MAX FutureSteps across all assignments sharing the
> # key (the cycle is fetched + completeness-checked at this MAX); `assignments`
> # retains each assignment's ORIGINAL horizons for later per-assignment slicing (≤ max).
> @dataclass(frozen=True, kw_only=True, slots=True)
> class ResolvedTrackRequest:
>     key: ForcingTrackKey
>     fetch_horizons: FeatureFetchHorizons          # per-feature MAX across assignments
>     assignments: tuple[ForcingRequired, ...]       # retained, each with its own horizons
>
> # Reducer contract — pure, deterministic (services/), the projection→fetch bridge:
> #   resolve_tracks(required: list[ForcingRequired]) -> list[ResolvedTrackRequest]
> #   Group `required` by `key`. Per group emit ONE ResolvedTrackRequest whose
> #   fetch_horizons[f] = max(a.assignment_horizons[f] for a in group) for every
> #   f in key.features, and whose `assignments` is the group (retaining each
> #   assignment's own horizons). Deterministic output order (sorted by key). Every
> #   group member satisfies set(assignment_horizons) == key.features (asserted).
> #
> # Completeness gate keys off ResolvedTrackRequest.fetch_horizons (the MAX): the RAW
> # candidate is accepted iff, for every f in key.features, the series reaches
> # fetch_horizons[f] AND carries member_ids == source.expected_member_ids(key) at that
> # horizon (source-derived, NEVER a literal {0..50} or a bare "any member present" —
> # see expected_member_ids above) — so "5-day + 10-day dedup onto one 10-day track" is
> # completeness-checked at 10-day. Applies to BOTH modes: ENSEMBLE checks the full
> # expected set (Plan 151 T5 review fold-in); SINGLE checks its own one-element
> # identity (Plan 151 T8a review fold-in) — a stray/foreign member_id the source
> # never declared must not count toward either mode's completeness.
> # Per-assignment assembly then slices each assignment's own (≤ max) horizons out of
> # the fetched-at-max frame.
> #
> # RESIDUAL (human confirm): feature-set UNION dedup (fetch {tp,t_2m} once to serve a
> # {tp}-only assignment) is intentionally NOT here — it would drop `features` from the
> # equality axis and require the completeness gate + per-assignment slicer to handle
> # superset frames. Moot under the v0/v1 {tp,t_2m} allowlist; revisit if heterogeneous
> # feature sets appear.
>
> # StationTrackOutcome — discriminated union (NOT a nullable payload)
> @dataclass(frozen=True, kw_only=True, slots=True)
> class StationTrackAvailable:
>     cycle: UtcDatetime
>     records: object                      # station-keyed records (typed in the build plan)
>     provenance: NwpCycleSource
> @dataclass(frozen=True, kw_only=True, slots=True)
> class StationTrackUnavailable:
>     reason: StationUnavailableReason     # enum: NO_DATA_AT_CYCLE | EXTRACTION_EMPTY | NOT_SUBSCRIBED |
>                                           # INCOMPLETE_AT_CYCLE | MISSING_POLYGON_COLUMN (D8, D27 additive)
> StationTrackOutcome = StationTrackAvailable | StationTrackUnavailable
>
> @dataclass(frozen=True, kw_only=True, slots=True)
> class TrackFetchResult:
>     resolved_cycle: UtcDatetime
>     station_outcomes: Mapping[StationId, StationTrackOutcome]
>
> class CandidateFetchStatus(Enum):        # candidate-fetch outcome taxonomy
>     COMPLETE = auto()                    # accept
>     ABSENT_INCOMPLETE = auto()           # walk-back eligible
>     TRANSIENT = auto()                   # retry, then walk-back
>     AUTH_CONFIG = auto()                 # flow-fatal (misconfiguration, not a data gap)
>     STORE = auto()                       # flow-fatal
>     # (per-station availability is NOT a candidate status — it lives in StationTrackOutcome)
>
> @dataclass(frozen=True, kw_only=True, slots=True)
> class CandidateFetchResult:              # candidate-LOCAL ownership: fresh per candidate, never reused/mutated;
>     status: CandidateFetchStatus         # completeness predicate validates the RAW result BEFORE any persist
>     cycle: UtcDatetime
>     raw: GriddedForecast | dict[StationId, WeatherForecastResult] | None
> # CandidateFetchResult is constructed SERVICES-side only (D31) — never returned by
> # fetch_requirement. The adapter's own return type is the raw fetch outcome below.
>
> class RawFetchStatus(Enum):              # the ADAPTER's transport-only classification
>     FETCHED = auto()                     # >=1 station committed rows this cycle
>     ABSENT_AT_CYCLE = auto()             # walk-back-eligible absence (total loss after
>                                           # per-HRU containment, OR well-formed-empty)
>
> @dataclass(frozen=True, kw_only=True, slots=True)
> class RawFetchOutcome:                   # fetch_requirement's return type (D31)
>     status: RawFetchStatus
>     cycle: UtcDatetime
>     stations: Mapping[StationId, WeatherForecastResult]
>     missing_polygon_column: frozenset[StationId] = frozenset()   # D27: explicitly
>       # excluded stations, never silently dropped — services/ later attributes
>       # StationTrackUnavailable(MISSING_POLYGON_COLUMN) to each.
>     absent_detail: str | None = None     # diagnostic text for ABSENT_AT_CYCLE only
>
> # Completeness gate precision (D10-completeness-pre-extract): for a pre-extracted
> # (station-keyed dict, not GriddedForecast) candidate the gate has no single global
> # member axis, so it is evaluated per (station, feature, member, horizon) — a
> # candidate-wide member-id union is NOT the predicate. See design D8 for the full
> # per-station verdict + candidate-verdict-from-per-station-verdicts rule.
>
> # AssignmentFailureCause — FORWARD FI-BOUNDARY GROUPING (target). The three COARSE buckets the concrete
> # assignment-level enum (below, landed by Plan 150) rolls up into once the FI-adapter follow-on stops flattening
> # ModelFailure. NOT the code enum today; kept side-by-side with the landed one per Plan 150 D5.
> class AssignmentFailureCause(Enum):      # all three are assignment-local (advance the fallback chain)
>     MISSING_CONTEXT = auto()             # track/context absent -> model NOT called (expected)
>     MODEL_FAILURE = auto()               # returned FI ModelFailure -> ModelFailure.cause preserved structurally
>     UNEXPECTED_EXCEPTION = auto()        # SAP3 backstop
> ```
> `ModelRunContext` stays **service-local** (defined in `services/`; shape per Plan 148). Enum/NewType names above
> reuse existing symbols where they exist (`EnsembleMode`, `NwpCycleSource`, `StationId`/`ModelId`/`GroupId`).
>
> **`AssignmentFailureCause` — landed concrete, assignment-level (Plan 150, service-local in
> `services/run_station_forecast.py`).** Placed **side-by-side** with the coarse FI-boundary grouping above (per
> Plan 150 D5 — one is not replacing the other). This is the enum Plan 150 **lands in code**: eight concrete causes
> at the assignment level, extensible by Phase 3 with **no runner/type rework** (new members + new production sites
> only).
> ```python
> class AssignmentFailureCause(Enum):      # assignment-level: "why did THIS assignment fail" (advance the fallback chain)
>     MODEL_NOT_FOUND = auto()
>     INSUFFICIENT_COVERAGE = auto()
>     NO_ARTIFACT = auto()
>     WARM_UP_LOAD_FAILED = auto()
>     UNSUPPORTED_STATEFUL_ENSEMBLE = auto()   # two return sites, one cause
>     PREDICT_FAILED = auto()                  # conflates FI ModelFailure + unexpected-in-predict FOR NOW (Phase 2-FI follow-on splits it -> FI MODEL_FAILURE | UNEXPECTED_EXCEPTION)
>     QC_FAILED = auto()
>     UNEXPECTED_EXCEPTION = auto()            # loop-level SAP3 backstop only (maps 1:1 to the FI-side UNEXPECTED_EXCEPTION bucket)
>     # Phase 3 ADDS (purely additive): MISSING_CONTEXT, TRACK_UNAVAILABLE
>
> @dataclass(frozen=True, kw_only=True, slots=True)
> class AssignmentSuccess:
>     result: StationForecastResult
> @dataclass(frozen=True, kw_only=True, slots=True)
> class AssignmentFailure:
>     cause: AssignmentFailureCause
>     detail: str
> AssignmentOutcome = AssignmentSuccess | AssignmentFailure
> ```
> `MultiModelForecastResult.failed_models` (see "Multi-model combination types and services" below) changes from
> `dict[ModelId, str]` to `dict[ModelId, AssignmentFailure]` with Plan 150. Mapping to the forward FI-boundary
> grouping: "model not called before/around
> predict" (`MODEL_NOT_FOUND`, `INSUFFICIENT_COVERAGE`, `NO_ARTIFACT`, `WARM_UP_LOAD_FAILED`,
> `UNSUPPORTED_STATEFUL_ENSEMBLE`) vs "SAP3 backstop / graceful post-predict reject" (`PREDICT_FAILED`, `QC_FAILED`,
> `UNEXPECTED_EXCEPTION`); Phase 3's `MISSING_CONTEXT`/`TRACK_UNAVAILABLE` land on the FI-side `MISSING_CONTEXT`
> bucket.
>
> **`ModelRunContext` / `WarmUpState` (Plan 148, Phase 1 — shipped, READ side).** Both are **service-local**
> (defined in `services/operational_inputs.py`, next to `OperationalInputMetadata`) — this spec documents their
> *shape*, not their import path, per the locked convention above.
> ```python
> @dataclass(frozen=True, kw_only=True, slots=True)
> class WarmUpState:                       # returned by load_warm_up_state(store, station_id, model_id, clock)
>     prior_state: bytes | None
>     warm_up_source: WarmUpSource         # existing enum: FRESH | SNAPSHOT | COLD_START
>     warm_up_state_age_hours: float | None
>
> @dataclass(frozen=True, kw_only=True, slots=True)
> class ModelRunContext:                   # the per-assignment run unit, keyed by (station_id, model_id)
>     station_id: StationId
>     model_id: ModelId
>     inputs: StationModelInputs           # shared in Phase 1 (referenced, not copied) — per-assignment inputs
>                                           # are a later phase (component 3 above)
>     observation_staleness_hours: float | None   # shared non-state scalar, copied from OperationalInputMetadata
>     nwp_age_hours: float | None                 # shared non-state scalar, copied from OperationalInputMetadata
>     prior_state: bytes | None            # per-assignment (Plan 148's fix: no longer shared across assignments)
>     warm_up_source: WarmUpSource         # per-assignment
>     warm_up_state_age_hours: float | None       # per-assignment
> ```
> **Ownership**: per-assignment run unit + warm-up bundle. `ModelRunContext` deliberately does **not** embed
> `OperationalInputMetadata` — that would expose the *representative* assignment's `warm_up_source`/age under the
> same field names as the context's own per-assignment ones, reintroducing the shared-provenance bug this plan
> fixes. `OperationalInputMetadata.warm_up_source`/`warm_up_state_age_hours` **stay** on that type as shared
> *provenance* because the GROUP path (`assemble_group_operational_inputs`, one call per station with its own
> single group `model_id` — never shared across assignments) still reads them; `OperationalInputMetadata` no
> longer carries `prior_state` at all (removed Plan 148 T2) — the state *bytes* travel only via each assignment's
> own `ModelRunContext`.
>
> **FI boundary vs SAP3-native boundary.** The ForecastInterface's `predict(prior_state=...)` contract
> (`:1733-1761`) is **state-free at the protocol level** — it passes `bytes | None` with no provenance fields; FI
> models never see `warm_up_source`/`warm_up_state_age_hours`. SAP3's own `StationForecastModel.predict` protocol
> (`protocols/forecast_model.py:32`) mirrors that same `prior_state: bytes | None` at the SAP3↔model boundary — the
> provenance fields (`warm_up_source`, `warm_up_state_age_hours`) are a **SAP3-native addition**, carried on
> `ModelRunContext` (station-cycle path) / `OperationalInputMetadata` (GROUP path) for forecast provenance and
> input-quality assessment, never passed into `predict` itself and never part of the FI contract.
>
> **Write side (not yet built).** Phase 1 is READ-side only: the flow still persists only the primary/combination
> model's `new_state` per station-cycle. Per-assignment state *persistence* is a later phase's responsibility (see
> `forecast-cycle-redesign.md` build sequence item 1 / Non-goals).

**Raw NWP grid** (pre-extraction, not per-station):

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class GriddedForecast:
    nwp_source: str
    cycle_time: UtcDatetime
    values: xr.Dataset             # dimensions: member × valid_time × latitude × longitude; weather parameters are data variables, not a dimension coordinate
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

**Plan 175 T3 — `fetch_observations_batch` / `BatchStationDataSource` (NOT part of
this Protocol).** `fetch_observations` stays exactly as above — the locked shape
every `StationDataSource` caller (the recorder, every test fake) depends on.
`ingest_observations_flow` calls `fetch_observations_batch` EXCLUSIVELY (T3), so
any adapter constructed as its `adapter=` argument must offer it — this is a
narrow ADDITIONAL capability, not a `StationDataSource` requirement, captured by
its own Protocol so a type checker can verify it instead of relying on an
`AttributeError` at runtime:

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class StationFetchOutcome:
    station_id: StationId
    observations: tuple[RawObservation, ...]
    failure_cause: FetchOutcomeCause | None   # None = clean poll (possibly zero obs)
    failure_detail: str | None                # None iff failure_cause is None

@dataclass(frozen=True, kw_only=True, slots=True)
class HydroScraperBatchResult:
    outcomes: tuple[StationFetchOutcome, ...]
    # .observations -> flat list[RawObservation] (what fetch_observations() returns)
    # .failed -> tuple[StationFetchOutcome, ...] where failure_cause is not None

class BatchStationDataSource(Protocol):
    def fetch_observations_batch(
        self,
        station_configs: list[StationConfig],
        since: dict[StationId, UtcDatetime],
    ) -> HydroScraperBatchResult: ...

class HydroScraperAdapter:
    def fetch_observations_batch(...) -> HydroScraperBatchResult: ...
        # fetch_observations() delegates to this and returns .observations —
        # both go through the SAME shared LindasRateLimiter (D6), so no
        # caller of either method escapes pacing.

class ReplayStationAdapter:
    def fetch_observations_batch(...) -> HydroScraperBatchResult: ...
        # Plan 175 round 2: a thin per-station regrouping of the results
        # fetch_observations() already computes — NOT a second parse/filter
        # implementation. Added because `ingest_observations_flow` calling
        # this method exclusively meant a `StationDataSource` that only
        # implemented the base Protocol (this adapter, pre-round-2) raised
        # `AttributeError` if ever handed to the flow as `adapter=`.
```

`FetchOutcomeCause` is defined above alongside `PipelineCheckType`. Widening the
`StationDataSource` Protocol itself to carry this shape was the explicit
alternative and was rejected — it would force every `StationDataSource`
implementation to grow this method (or a default/`NotImplementedError` stub)
whether or not it is ever used as `ingest_observations_flow`'s adapter, entangling
the base fetch contract with one flow's typed per-station accounting.
`BatchStationDataSource` gives that flow's parameter an honest type without that
cost — `HydroScraperAdapter` and `ReplayStationAdapter` both satisfy it; a
`StationDataSource` implementation that does not is a caller bug if handed to
`ingest_observations_flow`, not a Protocol violation.

#### WeatherReanalysisSource

Retained for v1 (Nepal / ERA5-Land). `MeteoSwissOpenDataReanalysisAdapter`
implements it for v0 (Swiss). **Plan 183** adds a second concrete
implementation, `Era5LandReanalysisAdapter` (`adapters/era5_land_reanalysis.py`)
— reads ERA5-Land basin-averaged forcing DIRECTLY from
`s3://sloth-dynamic/v1/era5/`, the same store aquacast's training lineage
(`aquaire`) reads, restricted to `{"precipitation", "temperature"}` (radiation
deferred). This exists to VALIDATE v0 Swiss models against the training
lineage (Plan 183 T3 — Caravan climate-index parity), and is selectable via
`DeploymentConfig.reanalysis_source = "era5_land"`
(`adapters/hybrid_reanalysis_factories.py::select_reanalysis_source`) — it is
distinct from the eventual Data-Gateway-mediated ERA5-Land path
`architecture-context.md` describes for production Nepal ingest, which
remains unimplemented.

```python
class WeatherReanalysisSource(Protocol):
    def fetch_reanalysis(
        self,
        station_configs: list[StationWeatherSource],
        start: UtcDatetime,
        end: UtcDatetime,
        parameters: list[str],
    ) -> list[RawHistoricalForcing]: ...
        # Returns flat list of raw forcing records — callers group by station_id as needed.
        # Reanalysis is always pre-extracted (no gridded path). Supports ensemble reanalysis
        # (e.g. ERA5 ensemble members) via member_id field.
```

**Writer-side product-scoped fetch (Plan 115b1 §1F).** The concrete
`MeteoSwissOpenDataReanalysisAdapter` adds a second, product-keyed entry point
used ONLY by the writer side (Flow 6 ingest and the 115b2 backfill) — the
read-side `WeatherReanalysisSource` protocol above is unchanged:

```python
def fetch_products(
    self,
    products: list[ForcingSource],   # exact product tags to fetch
    station_configs: list[StationWeatherSource],
    start: UtcDatetime,
    end: UtcDatetime,
    parameters: list[str],           # additionally restricts by canonical parameter
) -> list[RawHistoricalForcing]: ...
```

`fetch_products` selects EXACTLY the given `products` (both the product tag AND
the canonical parameter must match), so it is never ambiguous the way the
parameter-keyed path becomes once >1 product serves one parameter. Archive-backed
products (RhiresD + the ch01r temperature/sunshine grids) route through the
yearly archive/last family so a historical product-year fetch selects the
per-year NetCDF instead of gapping out; only `RprelimD` (the preliminary live
tail) is daily-only.

**Fail-closed precipitation rule (Plan 115b1 §1F).** Once two precipitation
products are registered (RhiresD + RprelimD), the parameter-keyed
`fetch_reanalysis(..., ["precipitation"])` **raises `ConfigurationError`** — it
cannot disambiguate which product the caller wants. Precipitation is served ONLY
via `fetch_products`; the other four canonical parameters (one product each)
still resolve on the parameter path unchanged.

**Per-product high-water-mark discovery (Plan 115b2 §3A/§3C).** Generalises
1D's RhiresD-only `discover_rhiresd_boundary()` (kept as a thin wrapper) to
every product:

```python
def discover_product_boundary(self, product: ForcingSource) -> UtcDatetime | None: ...
    # Latest date any asset of `product` has been published, scanning the
    # archive/last-monthly/daily STAC asset families. None = no asset yet
    # (never substituted with "today" — the caller must handle it).
```

**MeteoSwiss reanalysis binding + chunked backfill (Plan 115b2, `services/reanalysis_backfill.py`).**
Bind-before-backfill: the adapter only processes configs declaring its own
`nwp_source`, so a backfill with no binding does nothing and reports success.

```python
def eligible_meteoswiss_configs(
    stations: list[StationConfig], basin_store: BasinStore,
) -> list[StationWeatherSource]: ...
    # §3D — every station with a VALID basin polygon (Polygon/MultiPolygon);
    # a station lacking one is logged and excluded, never silently dropped.

def bind_meteoswiss_reanalysis_fleet(
    station_store: StationStore, basin_store: BasinStore,
) -> BindingBackfillResult: ...
    # §2A — one-shot binding backfill for the EXISTING fleet. Idempotent.

def discover_backfill_spans(adapter: MeteoSwissBackfillAdapter) -> list[BackfillSpan]: ...
    # §3A split rule as half-open [start, end) windows bounded by EACH
    # product's own high-water mark (never a single shared T): RhiresD over
    # [1981-01-01, R+1d), RprelimD over [R+1d, hwm(rprelimd)+1d), and one
    # span each for TabsD/TminD/TmaxD/SrelD over [1981-01-01, hwm(p)+1d).
    # A product with no published asset yet is OMITTED, not substituted.

def run_backfill(
    *, adapter: MeteoSwissBackfillAdapter, forcing_store: HistoricalForcingStore,
    station_configs: list[StationWeatherSource], spans: list[BackfillSpan] | None = None,
    station_batch_size: int = 50,
) -> BackfillResult: ...
    # §3A-§3C — chunked (product, year, station-batch) work units, each
    # persisted before the next (never holds the full series in memory).
    # Resumable: fetch_covered_days() gap-detects on the LOGICAL key
    # (excluding version) BEFORE fetching, so an already-covered chunk is
    # skipped with ZERO network calls, and a re-run over complete data is a
    # no-op.
```

Station onboarding (`services/onboarding.py`) wires this: Step 4c writes the
MeteoSwiss binding for every eligible station resolved that run (§2B), then —
when a `reanalysis_adapter` is supplied (production onboarding always
supplies one; unit tests using fakes are unaffected) — runs the per-station
backfill and withholds OPERATIONAL promotion (Step 8) from a MeteoSwiss-eligible
station until its backfill has landed at least one row (§2C).

**Full availability-range discovery (Plan 115b3 §4C).** Generalises
`discover_product_boundary` (latest END only) to the FULL range, reusing the
same STAC-scanning infrastructure:

```python
def discover_product_availability_range(
    self, product: ForcingSource,
) -> tuple[date, date] | None: ...
    # (earliest_start, latest_end) across every asset published for
    # `product`, scanning the archive/last-monthly/daily STAC asset
    # families. None = no asset yet.
```

**Validation gate — reference comparison + live-tail residual (Plan 115b3
§4A-§4D, `services/validation_gate.py`).** A GO/NO-GO analysis gate, not a
production step: reads `historical_forcing`, writes nothing. Runs after the
115b2 backfill and before the 115b4 reader flip.

```python
class GateVerdict(StrEnum):
    PASS = "pass"
    FLAG = "flag"
    ESCALATE = "escalate"
    DATA_QUALITY_ESCALATE = "data_quality_escalate"  # coverage gap / degenerate denominator

def classify_precip_rel_bias(rel_bias: float) -> GateVerdict: ...
    # |rel_bias| <=5% pass, >5% flag, >20% escalate (SIGNED input, gated on
    # the ABSOLUTE VALUE — a large negative bias must not falsely pass).

def classify_temperature(mean_bias: float, rmse: float) -> GateVerdict: ...
    # BOTH mean_bias and rmse thresholded in degC: pass <=> |mean_bias|<=0.5
    # AND rmse<=1.0; escalate <=> |mean_bias|>1.0 OR rmse>2.0.

def evaluate_precip_basin(
    station_id: StationId, code: str,
    ours: dict[date, float], camels: dict[date, float],
    expected_dates: frozenset[date],
) -> BasinPrecipResult: ...
def evaluate_temperature_basin(
    station_id: StationId, code: str,
    ours: dict[date, float], camels: dict[date, float],
    expected_dates: frozenset[date],
) -> BasinTemperatureResult: ...
    # §4A/§4B — `expected_dates` is the FULL comparison-window date set; a
    # basin/date present on one side but not the other (or absent from either
    # vs expected_dates) is a coverage gap: forces DATA_QUALITY_ESCALATE,
    # never silently inner-joined.
    # A non-positive CAMELS total does the same (never divides by it).

def run_reference_comparison(
    store: HistoricalForcingStore, stations: list[StationConfig],
) -> ReferenceComparisonReport: ...
    # Per station: meteoswiss_rhiresd/precipitation vs camels-ch/precipitation,
    # meteoswiss_tabsd/temperature vs camels-ch/temperature, over
    # [1981-01-01, 2021-01-01).

def discover_overlap_window(adapter: MeteoSwissBoundaryAdapter) -> OverlapWindow | None: ...
    # §4C — the STAC date INTERSECTION of RhiresD and RprelimD availability
    # (discover_product_availability_range on both). None if either product
    # has no asset yet, or the ranges don't overlap.

def fetch_overlap_products(
    adapter: MeteoSwissBoundaryAdapter, station_configs: list[StationWeatherSource],
    window: OverlapWindow,
) -> tuple[list[RawHistoricalForcing], list[RawHistoricalForcing]]: ...
    # One-off measurement fetch (RhiresD, RprelimD) over the SAME overlap
    # window — separate from the 115b2 archive backfill.

def compute_live_tail_residual(
    rhiresd_rows: list[RawHistoricalForcing], rprelimd_rows: list[RawHistoricalForcing],
    window: OverlapWindow,
) -> LiveTailResidualResult: ...
    # §4D — the one genuinely attributable number. Compares ONLY paired
    # (station, date) rows; unpaired rows are excluded and counted, never
    # silently dropped.
```

`scripts/validate_forcing_reference.py` runs 4A-4D against a live database +
STAC and prints the per-basin report (read-only; the 4C/4D fetch is never
persisted).

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

`FlowRunStatus` is defined in `types/pipeline.py` (see § FlowRunStatus above).

```python
@runtime_checkable
class PipelineStatusSource(Protocol):
    def fetch_recent_runs(
        self,
        flow_names: list[str],
        since: UtcDatetime,
    ) -> list[FlowRunStatus]: ...
```

Production implementation wraps the Prefect client. Tests inject a `FakePipelineStatusSource` returning pre-configured states.

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

    # --- Model onboarding ---
    skill_gate_thresholds: dict[str, float] = {}  # metric_name → minimum value; empty = pass-through (auto-promote)
    available_nwp_parameters: frozenset[str] = frozenset({"precipitation", "temperature"})
        # Forecast/future-dynamic availability: NWP parameters this deployment's NWP
        # source actually delivers. Used by model onboarding compatibility (M.2) to
        # validate future_dynamic_features. v0 default: ICON-CH2-EPS provides
        # precipitation and temperature.
    available_past_only_nwp_parameters: frozenset[str] = frozenset({"relative_sunshine_duration"})
        # Past-dynamic-ONLY reanalysis parameters (Plan 115b1 §1E): parameters with a
        # self-derived MeteoSwiss reanalysis product but no forecast counterpart (e.g.
        # SrelD — ICON-CH2-EPS fetches only precipitation/temperature). Advertising
        # these in available_nwp_parameters would let a model declare them as
        # future-dynamic, which can never be delivered operationally.

    @property
    def available_past_nwp_parameters(self) -> frozenset[str]:
        # Past-dynamic availability = available_nwp_parameters | available_past_only_nwp_parameters.
        # validate_compatibility (and validate_compatibility_for_unit) now take
        # available_past_features and available_future_features SEPARATELY —
        # past_dynamic_features is checked against this property, future_dynamic_features
        # against available_nwp_parameters. Before Plan 115b1 a single conflated set served
        # both checks.
        ...

    # --- NWP lateness ---
    nwp_max_wait_hours: float = 3.0            # max wait for expected NWP delivery
    nwp_max_fallback_age_hours: float = 12.0   # max age of fallback NWP cycle

    # --- Warm-up state (conceptual models) ---
    warm_up_snapshot_max_age_hours: float = 48.0  # default; season-dependent override below
    warm_up_snapshot_max_age_monsoon_hours: float = 24.0  # shorter during wet season

    # --- Flow regime ---
    flow_regime_p50_percentile: float = 50.0   # customizable percentile boundary
    flow_regime_p90_percentile: float = 90.0

    # --- Per-source alert enablement (v0-scope.md §A8c) ---
    # Per-source flags allow incremental activation: pipeline alerts first,
    # then observation alerts, then forecast alerts. All default false for v0.
    enable_forecast_alerts: bool = False         # gates Flow 1 Phase C (steps 1.12-1.14)
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

    # --- Input quality assessment ---
    input_quality: InputQualityConfig = InputQualityConfig()

    # --- Multi-model alert strategy ---
    alert_model_strategy: ModelCombinationStrategy = ModelCombinationStrategy.PRIMARY
    min_operational_ensemble_size: int = 20
    min_operational_quantile_levels: int = 7

    # --- Quarantined evaluation-only collector archive paths ---
    # Gated: unset means the corresponding collector flow no-ops; it never
    # falls back to any operational path. These paths ARE part of
    # DeploymentConfig (parsed from their own [adapters.*] TOML section by
    # load_config, then stripped from the adapters dict before validation —
    # see config/deployment.py).
    bafu_forecast_archive_path: Path | None = None
        # Plan 111 route-C BAFU forecast collector. Set from
        # [adapters.bafu_forecast].archive_base_path.
    bafu_observation_archive_path: Path | None = None
        # Plan 136 BAFU LINDAS observation archive collector. Set from
        # [adapters.bafu_observation].archive_base_path.
```

### InputQualityConfig

Thresholds used by `assess_input_quality()` (Flow 1 step 1.7) to assign `InputQualityLevel` per dimension.

```python
class InputQualityConfig(BaseModel):
    obs_degraded_hours: float = 12.0
        # observation staleness → DEGRADED (must be > observation_staleness_warning_hours)
    nwp_age_partial_hours: float = 9.0
        # NWP cycle age → PARTIAL
    nwp_age_degraded_hours: float = 11.0
        # NWP cycle age → DEGRADED (must be <= nwp_max_fallback_age_hours)
    warmup_snapshot_age_partial_hours: float = 24.0
        # warm-up snapshot age → PARTIAL (default; caller may override per season)
    warmup_snapshot_age_degraded_hours: float = 42.0
        # warm-up snapshot age → DEGRADED (must be <= warm_up_snapshot_max_age_hours;
        # for monsoon deployments, must be <= warm_up_snapshot_max_age_monsoon_hours)
```

Cross-validators (enforced at `DeploymentConfig` load time):
- `obs_degraded_hours > observation_staleness_warning_hours`
- `nwp_age_partial_hours < nwp_age_degraded_hours`
- `nwp_age_degraded_hours <= nwp_max_fallback_age_hours`
- `warmup_snapshot_age_partial_hours < warmup_snapshot_age_degraded_hours`
- `warmup_snapshot_age_degraded_hours <= warm_up_snapshot_max_age_hours`

Module: `config/deployment.py`

This is the deployment-wide config. Adapter-specific config (adapter types, cache ages,
archive flags) lives in the `[adapters]` section of `config.toml` and is loaded separately
by each adapter — not part of `DeploymentConfig`.

Deployment-specific parameters live in `[[parameters]]` sections of `config.toml` — loaded
during Flow 0 step 0.6 and upserted into the `parameters` table. See `architecture-context.md`
§`parameters` table for the schema and extensibility model.

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

- `FakeForecastModel` — `predict()` returns a deterministic `ForecastEnsemble` (configurable member count, constant values). `train()` returns a trivial artifact. Declares `data_requirements.static_features = frozenset()` (no static attributes needed). Ignores `static_attributes` in inputs. Useful for testing the orchestration and service layers without real ML.

### Verification

Each fake has a test: `assert isinstance(FakeObservationStore(), ObservationStore)` — ensures Protocol conformance. These tests run in CI and catch any Protocol signature drift.

---

## Multi-model combination types and services (v0b, Plan 026)

### MultiModelForecastResult

Returned by `run_all_station_forecasts()`. Carries all per-model results from a single forecast cycle run, including the `combinable_results` subset that excludes categorical fallback-tier models.

Module: `services/run_station_forecast.py`

```python
@dataclass(frozen=True, kw_only=True, slots=True)
class MultiModelForecastResult:
    station_id: StationId
    results: dict[ModelId, StationForecastResult]  # all successful models
    priorities: dict[ModelId, int]                  # model_id → assignment priority
    primary_model_id: ModelId | None               # highest-priority success (for fallback)
    failed_models: dict[ModelId, AssignmentFailure] # model_id → structured failure (cause + detail)

    @property
    def combinable_results(self) -> dict[ModelId, StationForecastResult]:
        """Results from non-fallback models only (model_id not in FALLBACK_MODEL_IDS)."""
        return {
            mid: r for mid, r in self.results.items()
            if mid not in FALLBACK_MODEL_IDS
        }
```

**Plan 150 (landed):** `failed_models`'s value type changed from `str` to `AssignmentFailure`
(`cause: AssignmentFailureCause`, `detail: str`) — see the landed concrete `AssignmentFailureCause` /
`AssignmentSuccess` / `AssignmentFailure` / `AssignmentOutcome` shapes documented above (§ "AssignmentFailureCause —
landed concrete, assignment-level"). `_run_single_model` returns `AssignmentOutcome` instead of
`StationForecastResult | str`; `run_all_station_forecasts` dispatches via `match` and wraps the call in a
per-assignment backstop `try` that converts an unanticipated exception into
`AssignmentFailure(cause=AssignmentFailureCause.UNEXPECTED_EXCEPTION, ...)` rather than letting it escape and darken
the whole station.

### Forecast combination service

Module: `services/forecast_combination.py`

```python
def combine_ensembles_pooled(
    ensembles: dict[ModelId, dict[str, ForecastEnsemble]],
) -> dict[str, ForecastEnsemble]:
    """Merge all models' member ensembles into a grand pooled ensemble per parameter.
    Only MEMBERS-representation ensembles are included; QUANTILES ensembles are skipped
    with a warning. Member IDs are remapped sequentially to avoid collision."""

def compute_bma_weights(
    skill_scores: dict[ModelId, float],  # model_id → mean CRPS (lower is better)
) -> dict[ModelId, float]:
    """Derive per-model BMA weights from skill scores via inverse-CRPS normalization.
    Weight_i = (1/CRPS_i) / sum(1/CRPS_j). Returns weights summing to 1.0.
    Models with missing or non-positive CRPS are excluded (weight = 0)."""

def combine_ensembles_bma(
    ensembles: dict[ModelId, dict[str, ForecastEnsemble]],
    weights: dict[ModelId, float],
    n_members: int = 100,
) -> dict[str, ForecastEnsemble]:
    """Weight-proportional member sampling: draw n_members from the pooled set with
    probability proportional to each model's BMA weight. Only MEMBERS-representation
    ensembles are included. Falls back to pooled if weights are absent."""

def compute_bma_skill_cross_validated(
    station_id: StationId,
    parameter: str,
    hindcasts_by_model: dict[ModelId, list[HindcastForecast]],
    observations: list[Observation],
    thresholds: ...,
    flow_regime_config: ...,
    seasons: ...,
    skill_source: SkillSource,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    """Two-fold temporal cross-validation: split hindcast period in half, train BMA weights
    on each half, evaluate on the other half, average the resulting skill scores.
    Yields an out-of-sample BMA skill estimate stored with model_id = BMA_MODEL_ID."""

def build_combined_forecasts(
    station_id: StationId,
    multi_result: MultiModelForecastResult,
    strategy: ModelCombinationStrategy,
    nwp_metadata: ...,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
    weights: dict[ModelId, float] | None = None,  # BMA weights; None → pooled fallback
) -> list[OperationalForecast]:
    """Construct combined OperationalForecast records from multi_result.combinable_results.
    Returns empty list if fewer than 2 combinable models succeeded (caller falls back to primary).
    Sets combination_strategy, source_model_ids, model_artifact_id=None, and sentinel model_id.
    When strategy=BMA and weights are provided, delegates to combine_ensembles_bma();
    falls back to combine_ensembles_pooled() if weights are absent."""
```

### Combined skill computation service

Module: `services/skill/combined_skill.py`

```python
def compute_combined_skill(
    station_id: StationId,
    parameter: str,
    strategy: ModelCombinationStrategy,
    hindcasts_by_model: dict[ModelId, list[HindcastForecast]],
    observations: list[Observation],
    thresholds: ...,
    flow_regime_config: ...,
    seasons: ...,
    skill_source: SkillSource,
    clock: Callable[[], UtcDatetime],
    uuid_factory: Callable[[], UUID],
) -> tuple[list[SkillScore], list[SkillDiagram]]:
    """Construct the combined ensemble at each time step where ALL models have hindcasts
    (intersection, not union), then run the standard verification suite with
    model_id = POOLED_MODEL_ID and artifact_id = None.
    Prefect task wrapper: compute_combined_skills_task."""
```

---

## Module map

Summary of where each type and Protocol lives in the source tree:

```
src/sapphire_flow/
├── types/
│   ├── ids.py              # StationId, ModelId, ObservationId, ForecastAdjustmentId, HistoricalForcingId, etc.
│   ├── datetime.py         # UtcDatetime, ensure_utc()
│   ├── enums.py            # All enums (including OnboardingOutcome — in-memory only, no DB column)
│   ├── domain.py           # GeoCoord, ParameterDefinition, DangerLevelDefinition, QcFlag,
│   │                       #   QcRuleParams, QcRuleSet, StationQcOverride, ClimBaseline,
│   │                       #   SeasonDefinition, ExceedanceResult, SkillInterpretationScheme, etc.
│   ├── ensemble.py         # ForecastEnsemble
│   ├── model.py            # StationInputData, StationModelInputs, GroupModelInputs,
│   │                       #   StationTrainingData, GroupTrainingData,
│   │                       #   ModelDataRequirements, ModelParams, ModelArtifact,
│   │                       #   ModelRecord, ModelRegistryEntry, ModelArtifactRecord,
│   │                       #   stack_model_inputs()
│   ├── training.py         # TrainingUnit, HindcastStepResult
│   ├── onboarding.py       # OnboardingResult
│   ├── model_onboarding.py # CompatibilityReport, SkillGateResult,
│   │                       #   OnboardingUnitResult, ModelOnboardingResult,
│   │                       #   ONBOARDING_FAILED_OUTCOMES, ONBOARDING_SKIPPED_OUTCOMES
│   │                       #   (imports TrainingUnit, HindcastStepResult from types/training.py;
│   │                       #    imports ArtifactId from types/ids.py)
│   ├── observation.py      # Observation, RawObservation
│   ├── forecast.py         # OperationalForecast, HindcastForecast, ForecastAdjustment, ForeignForecast
│   ├── weather.py          # WeatherForecastRecord, PointForecast, BasinAverageForecast,
│   │                       #   ElevationBandForecast, GriddedForecast
│   ├── historical_forcing.py # RawHistoricalForcing, HistoricalForcingRecord
│   ├── alert.py            # Alert
│   ├── skill.py            # SkillScore, SkillDiagram, FlowRegimeConfig
│   ├── station.py          # StationConfig, ModelAssignment, GroupModelAssignment, StationWeatherSource
│   ├── basin.py            # Basin
│   ├── rating_curve.py     # RatingCurve
│   ├── pipeline.py         # PipelineHealthRecord, FlowRunStatus
│   └── auth.py             # User (v1.x design intent), AccessToken (REALIZED, Slice C), AuditEntry (REALIZED, Slice B)
├── schemas/
│   └── forecast.py         # ForecastAdjustmentItem (Pydantic boundary validation)
├── protocols/
│   ├── forecast_model.py   # ForecastModel
│   ├── stores.py           # All store Protocols + ConflictError
│   │                       #   (ObservationStore, ForecastStore, HindcastStore,
│   │                       #    WeatherForecastStore, AlertStore, SkillStore,
│   │                       #    ModelArtifactStore, StationStore, StationGroupStore,
│   │                       #    PipelineHealthStore, RatingCurveStore,
│   │                       #    FlowRegimeConfigStore, ForecastAdjustmentStore,
│   │                       #    ForeignForecastStore, HistoricalForcingStore,
│   │                       #    BasinStore, ModelStateStore, ModelStore, ParameterStore)
│   ├── adapters.py         # WeatherForecastSource, StationDataSource, WeatherReanalysisSource,
│   │                       #   ForeignForecastSource, PipelineStatusSource
│   ├── grid_extractor.py   # GridExtractor
│   └── notification.py     # NotificationAdapter
├── services/
│   ├── run_station_forecast.py  # run_station_forecast(), run_all_station_forecasts(),
│   │                            #   MultiModelForecastResult, FALLBACK_PRIORITY_THRESHOLD
│   ├── forecast_combination.py  # combine_ensembles_pooled(), build_combined_forecasts()
│   └── skill/
│       ├── service.py           # compute_skill_for_station() (artifact_id: ArtifactId | None)
│       └── combined_skill.py    # compute_combined_skill(), compute_combined_skills_task
└── config/
    └── deployment.py       # DeploymentConfig
```
