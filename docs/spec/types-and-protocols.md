# Types and Protocols Specification

Authoritative reference for all domain types, enums, and Protocols in
SAPPHIRE Flow. Implementation must match these signatures exactly.
Sources are cited as `[DD-NN]` referencing `docs/design/NN-*.md`.

## Module layout

```
src/sapphire_flow/
├── types/
│   ├── __init__.py          # re-exports everything
│   ├── enums.py             # all Enums and Literals
│   ├── observation.py       # Observation, QualityFlag
│   ├── weather.py           # WeatherForecast
│   ├── station.py           # StationInfo, StationConfig, ParameterForecastConfig, StationKind
│   ├── forecast.py          # ForecastEnsemble, ForecastType, ForecastStatus, ModelInputs
│   ├── alert.py             # FloodThreshold, AlertEvent, FloodLevel, AlertSource
│   ├── rating.py            # RatingCurve
│   ├── bulletin.py          # Bulletin
│   ├── observation_edit.py  # ObservationEdit
│   ├── adjustment.py        # ForecastAdjustment
│   ├── skill.py             # (metrics are dict[str, float], no dedicated type)
│   ├── training.py          # TrainingDataset, TrainResult, ModelConfig
│   ├── qc.py                # QCConfig, Bounds
│   └── config.py            # AlertConfig
├── schemas/                     # Pydantic boundary validation (JSONB fields)
│   ├── __init__.py              # re-exports all schema models
│   ├── access_token.py          # AccessTokenScope
│   ├── station.py               # StationMetadata, BasinMetadata
│   ├── rating_curve.py          # RatingCurveData, RatingCurveUncertainty
│   ├── model_skill.py           # SkillMetrics
│   ├── audit_log.py             # AuditDetail (discriminated union)
│   └── forecast_adjustment.py   # EnsembleSnapshot
├── protocols/
│   ├── __init__.py          # re-exports everything
│   ├── stores.py            # all Store Protocols
│   ├── adapters.py          # WeatherForecastSource, WeatherReanalysisSource, StationDataSource, ThresholdSource
│   ├── models.py            # ForecastModel, TrainableModel
│   └── notification.py      # NotificationSink
```

---

## Enums

### StationKind [DD-02]

```python
from enum import Enum

class StationKind(Enum):
    WEATHER = "weather"
    RIVER = "river"
    VIRTUAL = "virtual"
```

### QualityFlag [DD-02]

```python
from enum import IntEnum

class QualityFlag(IntEnum):
    UNCHECKED = 0
    PASSED = 1
    SUSPECT_RANGE = 2
    SUSPECT_RATE_OF_CHANGE = 3
    SUSPECT_SPATIAL = 4
    EXCLUDED = 9
    # Values 5-8 are reserved for future QC checks.
```

### EditType [DD-02]

```python
from enum import Enum

class EditType(Enum):
    CORRECTED = "corrected"
    EXCLUDED = "excluded"
```

### FloodLevel [DD-02]

```python
from enum import Enum

class FloodLevel(Enum):
    NORMAL = "normal"
    WATCH = "watch"
    WARNING = "warning"
    DANGER = "danger"
```

### AlertSource [DD-02]

```python
from enum import Enum

class AlertSource(Enum):
    FORECAST = "forecast"
    OBSERVATION = "observation"
```

### ForecastType [DD-04]

```python
class ForecastType(Enum):
    SUBDAILY = "subdaily"
    DAILY = "daily"
    PENTADAL = "pentadal"
    DEKADAL = "dekadal"
    MONTHLY = "monthly"
    SEASONAL = "seasonal"
```

### ForecastStatus [DD-02]

```python
class ForecastStatus(Enum):
    RAW = "raw"
    REVIEWED = "reviewed"
    SELECTED = "selected"
    PUBLISHED = "published"
```

### BulletinScope [DD-02]

```python
from enum import Enum

class BulletinScope(Enum):
    COUNTRY = "country"
    BASIN = "basin"
```

---

## Domain types (NamedTuples)

### Observation [DD-03]

```python
from datetime import datetime
from typing import NamedTuple

class Observation(NamedTuple):
    station_code: str
    parameter: str              # e.g. "precipitation", "water_level"
    timestamp: datetime
    value: float
    quality_flag: int | None = None  # QualityFlag int value
```

No invariants enforced in `__new__` — observations come from external
sources and may have any shape. Validation happens in the QC service.

### WeatherForecast [DD-03]

```python
class WeatherForecast(NamedTuple):
    station_code: str
    parameter: str
    issued_at: datetime
    lead_time_minutes: int      # e.g. 1440 = 1 day
    member: int                 # ≥0 = real ensemble member (0=control); reserved negative: -1=median, -2=p10, -3=p90
    value: float
```

### StationInfo [DD-03]

Returned by adapters. Represents external station metadata before DB mapping.

```python
from typing import Any

class StationInfo(NamedTuple):
    code: str
    name: str
    lon: float
    lat: float
    elevation_m: float | None = None
    kind: str = "river"         # "weather", "river", or "virtual"
    basin_code: str | None = None
    metadata: dict[str, Any] | None = None
```

### StationConfig [DD-05]

Internal representation used by flows. Constructed from the DB station +
station_model_config join.

```python
from typing import Any
from uuid import UUID

class ModelAssignment(NamedTuple):
    model_id: str
    model_version: str
    artifact_path: str

class ParameterForecastConfig(NamedTuple):
    parameter_name: str             # canonical name, e.g. "water_level", "discharge"
    parameter_id: UUID              # FK -> parameters table
    model_config: ModelAssignment
    fallback_config: ModelAssignment | None

class StationConfig(NamedTuple):
    id: UUID
    code: str
    name: str
    basin_id: UUID | None
    lon: float
    lat: float
    elevation_m: float | None
    kind: StationKind
    forecast_configs: list[ParameterForecastConfig]  # one per forecast target
    weather_source_ids: list[UUID]   # linked weather station UUIDs
    has_rating_curve: bool
    metadata: dict[str, Any] | None = None
```

### BasinInfo [DD-02]

Lightweight basin reference type for listing.

```python
class BasinInfo(NamedTuple):
    id: UUID
    code: str
    name: str
    metadata: dict[str, Any] | None = None
```

### FloodThreshold [DD-03, DD-02]

```python
class FloodThreshold(NamedTuple):
    station_code: str
    parameter: str
    level: str                  # "normal", "watch", "warning", "danger"
    value: float
    unit: str                   # e.g. "m_gauge_zero", "m_asl", "m3s"
    valid_from_month: int | None = None  # 1-12, null = year-round
    valid_to_month: int | None = None    # 1-12, null = year-round
    exceedance_probability: float | None = None  # null = use global default
```

### AlertEvent [DD-02]

Represents a persisted alert record from the database.

```python
class AlertEvent(NamedTuple):
    id: UUID
    station_id: UUID
    parameter_id: UUID
    forecast_id: UUID | None    # null for observation-triggered
    source: AlertSource
    level: FloodLevel
    lead_time_minutes: int | None
    forecast_value: float
    threshold_value: float
    raised_at: datetime
    acknowledged_by: UUID | None = None
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    notes: str | None = None
    notified_at: datetime | None = None
```

**Invariant**: `AlertEvent.level` is never `FloodLevel.NORMAL` — alerts are
only created for watch/warning/danger exceedances.

### ForecastEnsemble [DD-04]

Returned by models. Each member is a list of (lead_time_minutes, value) pairs.

```python
class ForecastEnsemble(NamedTuple):
    station_id: str
    parameter_id: str
    forecast_type: ForecastType
    issued_at: datetime
    members: list[list[tuple[int, float]]]
    # members[i] = [(lead_time_minutes, value), ...]
    # All members must have identical lead-time sequences.
```

### Forecast [DD-02]

Database representation of a stored forecast (header row, no values).

```python
class Forecast(NamedTuple):
    id: UUID
    station_id: UUID
    parameter_id: UUID
    issued_at: datetime
    model_id: str
    model_version: str
    forecast_type: ForecastType
    version: int                # optimistic concurrency
    status: ForecastStatus
```

### ModelInputs [DD-04]

Input to `ForecastModel.predict()`. Constructed by `services/forecast_prep.py`.

```python
class ModelInputs(NamedTuple):
    station_id: str             # station code (human-readable, not UUID)
    parameter_id: str           # canonical parameter name, e.g. "discharge"
    observations: dict[str, list[tuple[datetime, float]]]
    # Keyed by parameter name. Excluded observations already removed.
    weather_forecasts: dict[str, list[tuple[datetime, float]]]
    # Keyed by parameter name.
    forecast_type: ForecastType
    metadata: dict[str, Any]    # station context (elevation, basin area, ...)
```

**Weather ensemble reduction**: `weather_forecasts` contains point values per
timestamp, not full ensemble members. The forecast preparation service
(`services/forecast_prep.py`) reduces weather ensemble members to their median
(or mean, configurable) per lead time before constructing `ModelInputs`.

Weather ensemble reduction is configured per deployment in config.toml:

    [forecast_prep]
    weather_ensemble_method = "median"  # "median" or "mean"

Models that need full weather ensemble members declare this via an
**optional** class attribute (NOT part of the ForecastModel Protocol, since
Python Protocols cannot have default implementations):

    class MyEnsembleModel:
        needs_full_ensemble: bool = True  # opt-in

        def predict(self, inputs: ModelInputs) -> ForecastEnsemble: ...

The check happens in `forecast_single_parameter` (see DD-05), NOT inside
`prepare_model_inputs`. The caller inspects the loaded model and passes a
`use_full_ensemble: bool` parameter:

    # In forecast_single_parameter, AFTER model is loaded:
    model = model_registry.load(param_config.model_config)
    use_full = getattr(model, "needs_full_ensemble", False)
    inputs = prepare_model_inputs(
        station, param_config, observation_store, weather_store,
        use_full_ensemble=use_full,
    )

When `use_full_ensemble` is True, weather_forecasts dict keys become
`"{parameter}_m{member_index}"` (e.g. "precipitation_m0",
"precipitation_m1", ..., "precipitation_m20").

**Important**: `needs_full_ensemble` is NOT added to the `ForecastModel`
Protocol definition. It is an optional attribute checked via `getattr` with
a default of `False`. Models that don't define it get reduced ensemble input.
This avoids breaking the Protocol contract for existing model implementations.

### RatingCurve [DD-02]

```python
class RatingCurve(NamedTuple):
    id: UUID
    station_id: UUID
    valid_from: datetime
    valid_to: datetime | None   # null = currently active
    created_by: UUID | None     # FK -> users
    data: dict[str, Any]        # stage-discharge pairs or equation
    uncertainty: dict[str, Any] | None = None
```

### TrainingDataset [DD-04]

```python
class TrainingDataset(NamedTuple):
    station_id: str
    observations: dict[str, list[tuple[datetime, float]]]
    weather_history: dict[str, list[tuple[datetime, float]]]
    metadata: dict[str, Any]
```

### TrainResult [DD-04]

```python
class TrainResult(NamedTuple):
    model_id: str
    station_id: str
    metrics: dict[str, float]   # training metrics (loss, val_loss, ...)
    artifact_path: str
    trained_at: datetime
```

### Bulletin [DD-02]

```python
class Bulletin(NamedTuple):
    id: UUID
    scope: BulletinScope
    basin_id: UUID | None
    template_id: str
    file_path: str
    forecast_ids: list[UUID]
    generated_at: datetime
    generated_by: UUID | None = None
```

### ObservationEdit [DD-02]

Database representation of an observation edit record.

```python
class ObservationEdit(NamedTuple):
    id: UUID
    station_id: UUID
    parameter_id: UUID
    timestamp: datetime
    previous_value: float
    new_value: float
    edited_by: UUID
    edited_at: datetime
    reason: str
    edit_type: EditType
    idempotency_key: str | None = None
```

### ForecastAdjustment [DD-02]

Database representation of a forecast adjustment record.

```python
class ForecastAdjustment(NamedTuple):
    id: UUID
    forecast_id: UUID
    adjusted_by: UUID
    adjusted_at: datetime
    lead_time_minutes: int
    original: dict[str, Any]    # validated by EnsembleSnapshot schema
    adjusted: dict[str, Any]    # validated by EnsembleSnapshot schema
    reason: str | None = None
```

### ModelConfig [DD-04]

Configuration passed to `TrainableModel.train()`.

```python
class ModelConfig(NamedTuple):
    hyperparameters: dict[str, Any]
    epochs: int | None = None
    device: str = "cpu"
```

---

## Configuration types

### QCConfig [DD-05]

```python
class Bounds(NamedTuple):
    min: float
    max: float

class QCConfig(NamedTuple):
    bounds: dict[str, Bounds]                    # parameter -> bounds
    max_rates: dict[str, float]                  # parameter -> max rate per hour

    def get_bounds(self, parameter: str) -> Bounds | None:
        return self.bounds.get(parameter)

    def get_max_rate(self, parameter: str) -> float | None:
        return self.max_rates.get(parameter)
```

Note: `QCConfig` uses methods, which NamedTuple supports. Alternatively
this could be a plain class — implementer's choice, but keep it immutable.

### AlertConfig [DD-05]

```python
class AlertConfig(NamedTuple):
    default_watch: float = 0.2       # 20% — low signal, heads up
    default_warning: float = 0.5     # 50% — majority of members
    default_danger: float = 0.8      # 80% — strong ensemble consensus

    def default_exceedance(self, level: FloodLevel) -> float:
        match level:
            case FloodLevel.DANGER:
                return self.default_danger
            case FloodLevel.WARNING:
                return self.default_warning
            case FloodLevel.WATCH:
                return self.default_watch
            case _:
                return 1.0  # normal — never alert
```

---

## Protocols

### Adapter Protocols [DD-03]

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class WeatherForecastSource(Protocol):
    """Fetches NWP ensemble forecasts (e.g. ICON-CH2-EPS, ECMWF)."""
    def fetch_forecasts(
        self,
        station_ids: list[str],
        issued_after: datetime,
    ) -> list[WeatherForecast]: ...


@runtime_checkable
class WeatherReanalysisSource(Protocol):
    """Fetches historical weather reanalysis data for model training,
    hindcasting, and skill metric calculation (e.g. ERA5, COSMO-REA6).

    NOT implemented in v0 — training uses station observations (SMN for
    Switzerland, DHM for Nepal) via StationDataSource. Retained for v1
    where Nepal may use ERA5-Land as a gridded reanalysis source.
    """
    def fetch_historical(
        self,
        station_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> list[Observation]: ...


@runtime_checkable
class StationDataSource(Protocol):
    def fetch_observations(
        self,
        station_ids: list[str],
        start: datetime,
        end: datetime,
    ) -> list[Observation]: ...

    def list_stations(self) -> list[StationInfo]: ...


@runtime_checkable
class ThresholdSource(Protocol):
    def fetch_flood_thresholds(
        self,
        station_ids: list[str],
    ) -> list[FloodThreshold]: ...
```

### Notification Protocol [DD-03]

```python
@runtime_checkable
class NotificationSink(Protocol):
    def send(self, subject: str, body: str, severity: FloodLevel) -> None: ...
```

### Model Protocols [DD-04]

```python
from pathlib import Path

@runtime_checkable
class ForecastModel(Protocol):
    @property
    def min_lookback_hours(self) -> int:
        """Minimum hours of historical observations needed."""
        ...

    def predict(self, inputs: ModelInputs) -> ForecastEnsemble: ...
    def save(self, path: Path) -> None: ...

    @classmethod
    def load(cls, path: Path) -> "ForecastModel": ...


@runtime_checkable
class TrainableModel(Protocol):
    def train(
        self,
        training_data: TrainingDataset,
        config: ModelConfig,
    ) -> TrainResult: ...
```

### ModelRegistry [DD-04]

Not a Protocol — a concrete class that discovers and manages model instances
via Python entry points. Defined here for completeness since flows depend on it.

```python
class ModelRegistry:
    def load(self, assignment: ModelAssignment) -> ForecastModel: ...
    def create(self, model_type: str) -> TrainableModel: ...
    def save(self, model: ForecastModel, station_id: str) -> None: ...
    def list_models(self) -> dict[str, type[ForecastModel]]: ...
```

### Store Protocols [DD-01]

```python
@runtime_checkable
class StationStore(Protocol):
    # Station CRUD (API + CLI)
    def list_stations(
        self,
        kind: StationKind | None = None,
        basin_id: UUID | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> tuple[list[StationConfig], str | None]: ...
    # Returns (stations, next_cursor). Cursor is opaque string or None.

    def get_station_by_id(self, station_id: UUID) -> StationConfig | None: ...
    def get_station_by_code(self, code: str) -> StationConfig | None: ...

    def create_station(self, info: StationInfo) -> StationConfig: ...
    # Raises ValueError if code already exists.

    def update_station(
        self,
        station_id: UUID,
        name: str | None = None,
        basin_id: UUID | None = None,
        elevation_m: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StationConfig: ...
    # Partial update. Only non-None fields are changed.

    # Bulk upsert (adapter import CLI)
    def upsert_stations(self, stations: list[StationInfo]) -> int: ...
    # Matches on station code. Returns rows inserted or updated.

    # Flow-facing: station configs with model assignments
    def get_active_station_configs(self) -> list[StationConfig]: ...
    # Returns stations with an active model assignment (stations JOIN station_model_config).

    def get_station_configs_by_basin(self, basin_id: UUID) -> list[StationConfig]: ...

    # Basin listing
    def list_basins(self) -> list[BasinInfo]: ...

    # Model config management (admin API + import CLI)
    def get_model_assignment(
        self, station_id: UUID, parameter_id: UUID,
    ) -> ModelAssignment | None: ...

    def upsert_model_assignment(
        self,
        station_id: UUID,
        parameter_id: UUID,
        model_id: str,
        model_version: str,
        artifact_path: str,
        fallback_model_id: str | None = None,
        fallback_artifact: str | None = None,
    ) -> None: ...

    def bulk_upsert_model_assignments(
        self,
        assignments: list[tuple[str, str, ModelAssignment, ModelAssignment | None]],
    ) -> int: ...
    # Each tuple: (station_code, parameter_name, primary, fallback | None). Returns rows upserted.


@runtime_checkable
class ObservationStore(Protocol):
    def upsert_observations(self, observations: list[Observation]) -> tuple[int, dict[tuple[str, str], tuple[UUID, UUID]]]: ...
    def insert_observations_no_overwrite(self, observations: list[Observation]) -> int: ...
    def get_observations(
        self, station_id: UUID, parameter_id: UUID,
        start: datetime, end: datetime,
    ) -> list[Observation]: ...
    def get_latest_observation(
        self, station_id: UUID, parameter_id: UUID,
    ) -> Observation | None: ...
    def get_previous_observations(
        self, station_param_pairs: list[tuple[UUID, UUID]],
    ) -> dict[tuple[UUID, UUID], Observation]: ...
    def update_quality_flags(
        self, flagged: list[tuple[Observation, int]],
    ) -> None: ...
    def detect_gaps(
        self, station_ids: list[UUID],
        lookback_days: int = 7,       # used when start/end omitted
        start: datetime | None = None, # overrides lookback_days when set
        end: datetime | None = None,
    ) -> list[tuple[UUID, datetime, datetime]]: ...


@runtime_checkable
class WeatherStore(Protocol):
    def upsert_weather_forecasts(self, forecasts: list[WeatherForecast]) -> int: ...
    def get_weather_forecasts(
        self, station_id: UUID, parameter_id: UUID,
        start: datetime, end: datetime,
    ) -> list[WeatherForecast]: ...


@runtime_checkable
class ForecastStore(Protocol):
    def save_forecast(
        self, station: StationConfig, param_config: ParameterForecastConfig,
        ensemble: ForecastEnsemble,
    ) -> Forecast: ...
    def get_forecasts_by_ids(self, ids: list[UUID]) -> list[Forecast]: ...
    def get_selected_forecasts_for_basin(self, basin_id: UUID) -> list[Forecast]: ...
    def get_all_selected_forecasts(self) -> list[Forecast]: ...
    def get_past_forecasts(
        self, station: StationConfig, lookback_days: int,
    ) -> list[Forecast]: ...
    def update_forecast_status(self, forecast_ids: list[UUID], status: ForecastStatus) -> None: ...


@runtime_checkable
class RatingCurveStore(Protocol):
    def get_active_rating_curve(self, station: StationConfig) -> RatingCurve | None: ...
    def save_rating_curve(self, rating_curve: RatingCurve) -> None: ...
    def list_rating_curves(self, station_id: UUID) -> list[RatingCurve]: ...


@runtime_checkable
class AlertStore(Protocol):
    def upsert_thresholds(self, thresholds: list[FloodThreshold]) -> int: ...
    def get_thresholds(self, station_id: UUID, parameter_id: UUID) -> list[FloodThreshold]: ...
    def get_thresholds_batch(
        self, station_param_pairs: set[tuple[UUID, UUID]],
    ) -> dict[tuple[UUID, UUID], list[FloodThreshold]]: ...
    def raise_alert(
        self, station: StationConfig, forecast: Forecast, lead_time: int,
        threshold: FloodThreshold,
        exceedance_fraction: float | None = None,
    ) -> None: ...
    def raise_observation_alert(self, observation: Observation, threshold: FloodThreshold) -> None: ...
    def resolve_stale_alerts(self, station: StationConfig, forecast: Forecast) -> None: ...
    def resolve_observation_alerts(self, station_id: UUID, parameter_id: UUID) -> None: ...
    def get_unacknowledged_danger_alerts(
        self, station_id: UUID | None = None, source: AlertSource | None = None,
    ) -> list[AlertEvent]: ...


@runtime_checkable
class SkillStore(Protocol):
    def save_skill_scores(
        self, station: StationConfig, parameter_id: UUID,
        model_id: str, model_version: str,
        forecast_type: ForecastType,
        lead_time_minutes: int,
        period_start: datetime, period_end: datetime,
        metrics: dict[str, float],
    ) -> None: ...


@runtime_checkable
class BulletinStore(Protocol):
    def save_bulletin(
        self, scope: BulletinScope, basin_id: UUID | None,
        template_id: str, path: str, forecast_ids: list[UUID],
        generated_by: UUID,
    ) -> None: ...
    def get_bulletin(self, bulletin_id: UUID) -> Bulletin | None: ...
    def list_bulletins(
        self, scope: BulletinScope | None = None, basin_id: UUID | None = None,
    ) -> list[Bulletin]: ...


@runtime_checkable
class TrainingStore(Protocol):
    """Data access only. Training data assembly (joining observations with
    weather data, QC filtering) lives in services/training_prep.py."""
    def get_training_observations(
        self, station_id: UUID, parameter_id: UUID,
        start: datetime | None = None, end: datetime | None = None,
    ) -> list[Observation]: ...
    # Returns QC-passed observations (quality_flag != 9) for the target parameter.

    def get_training_weather(
        self, weather_station_ids: list[UUID], params: list[str] | None = None,
        start: datetime | None = None, end: datetime | None = None,
    ) -> list[Observation]: ...
    # Returns weather observations from linked stations for specified parameters.

    def log_training_result(self, station_id: UUID, result: TrainResult) -> None: ...


@runtime_checkable
class ObservationEditStore(Protocol):
    """Records manual edits to observation values with full audit trail."""
    def save_edit(
        self, station_id: UUID, timestamp: datetime, edit: ObservationEdit,
    ) -> None: ...
    def get_edits(
        self, station_id: UUID, start: datetime, end: datetime,
    ) -> list[ObservationEdit]: ...


@runtime_checkable
class ForecastAdjustmentStore(Protocol):
    """Records manual adjustments to forecasts during the review workflow."""
    def save_adjustment(
        self, forecast_id: UUID, adjustment: ForecastAdjustment,
    ) -> None: ...
    def get_adjustments(self, forecast_id: UUID) -> list[ForecastAdjustment]: ...


@runtime_checkable
class AuditLogStore(Protocol):
    """Append-only log of all user actions for auditability."""
    def log_action(
        self, user_id: UUID, action: str, detail: dict[str, Any],
    ) -> None: ...
    def query_log(
        self, user_id: UUID | None = None, action: str | None = None,
        start: datetime | None = None, end: datetime | None = None,
    ) -> list[dict[str, Any]]: ...
```

---

## Exceptions

Custom exceptions used across the codebase:

```python
class SanityCheckFailure(Exception):
    """Model output failed plausibility checks."""

class InsufficientDataError(Exception):
    """Not enough historical data for the model's lookback requirement."""

class ModelLoadError(Exception):
    """Failed to load a model artifact."""

class PartitionMissingError(Exception):
    """PostgreSQL partition does not exist for the target date range."""
```

---

## Cross-reference: type origin by design doc

| Type | Source doc | Section |
|------|-----------|---------|
| StationKind | 02-data-model | stations table |
| QualityFlag | 02-data-model | quality flag values |
| EditType | 02-data-model | observation_edits |
| FloodLevel | 02-data-model | flood_thresholds |
| AlertSource | 02-data-model | alert_events |
| ForecastType | 04-models | ForecastModel Protocol |
| ForecastStatus | 02-data-model | forecasts table |
| BulletinScope | 02-data-model | bulletins table |
| Bulletin | 02-data-model | bulletins table |
| Observation | 03-adapters | adapter domain types |
| WeatherForecast | 03-adapters | adapter domain types |
| StationInfo | 03-adapters | adapter domain types |
| FloodThreshold | 03-adapters | adapter domain types |
| StationConfig | 05-flows | forecast flow |
| ParameterForecastConfig | 05-flows | multi-parameter station model |
| BasinInfo | 02-data-model | basins table |
| ModelAssignment | 02-data-model | station_model_config |
| AlertEvent | 02-data-model | alert_events table |
| ForecastEnsemble | 04-models | ForecastModel Protocol |
| Forecast | 02-data-model | forecasts table |
| ModelInputs | 04-models | ForecastModel Protocol |
| RatingCurve | 02-data-model | rating_curves table |
| TrainingDataset | 04-models | training interface |
| TrainResult | 04-models | training interface |
| ModelConfig | 04-models | training interface |
| ObservationEdit | 02-data-model | observation_edits table |
| ForecastAdjustment | 02-data-model | forecast_adjustments table |
| QCConfig / Bounds | 05-flows | QC configuration |
| AlertConfig | 05-flows | flood alert flow |
| WeatherForecastSource | 03-adapters | DataSource Protocol |
| WeatherReanalysisSource | 03-adapters | DataSource Protocol |
| StationDataSource | 03-adapters | DataSource Protocol |
| ThresholdSource | 03-adapters | DataSource Protocol |
| NotificationSink | 03-adapters | NotificationSink Protocol |
| ForecastModel | 04-models | ForecastModel Protocol |
| TrainableModel | 04-models | training interface |
| ModelRegistry | 04-models | model discovery |
| StationStore | 01-architecture | repository Protocols |
| ObservationStore | 01-architecture | repository Protocols |
| WeatherStore | 01-architecture | repository Protocols |
| ForecastStore | 01-architecture | repository Protocols |
| AlertStore | 01-architecture | repository Protocols |
| SkillStore | 01-architecture | repository Protocols |
| BulletinStore | 01-architecture | repository Protocols |
| RatingCurveStore | 01-architecture | repository Protocols |
| TrainingStore | 01-architecture | repository Protocols |
| ObservationEditStore | 01-architecture | repository Protocols |
| ForecastAdjustmentStore | 01-architecture | repository Protocols |
| AuditLogStore | 01-architecture | repository Protocols |

---

## JSONB Boundary Schemas (Pydantic)

Pydantic models that validate JSONB fields at the store/API boundary. These
live in `src/sapphire_flow/schemas/` — separate from `types/` (domain
NamedTuples) and `protocols/` (store interfaces). They are used exclusively
when reading from or writing to the database and when serializing API
responses. They are **never** imported by domain logic or flow code.

### Module layout

```
src/sapphire_flow/
├── schemas/
│   ├── __init__.py              # re-exports all schema models
│   ├── access_token.py          # AccessTokenScope
│   ├── station.py               # StationMetadata, BasinMetadata
│   ├── rating_curve.py          # RatingCurveData, RatingCurveUncertainty
│   ├── model_skill.py           # SkillMetrics
│   ├── audit_log.py             # AuditDetail (discriminated union)
│   └── forecast_adjustment.py   # EnsembleSnapshot
```

### Mapping: DB column → Pydantic model

| Table | Column | Pydantic model | Module |
|-------|--------|----------------|--------|
| `access_tokens` | `scope` | `AccessTokenScope` | `access_token.py` |
| `stations` | `metadata` | `StationMetadata` | `station.py` |
| `basins` | `metadata` | `BasinMetadata` | `station.py` |
| `rating_curves` | `data` | `RatingCurveData` | `rating_curve.py` |
| `rating_curves` | `uncertainty` | `RatingCurveUncertainty` | `rating_curve.py` |
| `model_skill` | `metrics` | `SkillMetrics` | `model_skill.py` |
| `audit_log` | `detail` | `AuditDetail` (union) | `audit_log.py` |
| `forecast_adjustments` | `original` | `EnsembleSnapshot` | `forecast_adjustment.py` |
| `forecast_adjustments` | `adjusted` | `EnsembleSnapshot` | `forecast_adjustment.py` |

---

### `schemas/access_token.py`

```python
from pydantic import BaseModel, field_validator


class AccessTokenScope(BaseModel):
    """Validates access_tokens.scope JSONB."""

    stations: list[str] | None = None
    # Station codes this token can access. None = all stations.
    # Empty list = no stations (effectively useless, but valid).

    forecast_status: list[str] | None = None
    # Which forecast statuses are visible (e.g. ["published"]).
    # None = all statuses visible.

    read_only: bool = True

    @field_validator("forecast_status", mode="before")
    @classmethod
    def validate_forecast_status(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        allowed = {"raw", "reviewed", "selected", "published"}
        for s in v:
            if s not in allowed:
                raise ValueError(f"invalid forecast_status '{s}', must be one of {allowed}")
        return v
```

### `schemas/station.py`

```python
from pydantic import BaseModel


class StationMetadata(BaseModel):
    """Validates stations.metadata JSONB."""

    source: str | None = None
    source_id: str | None = None
    river_name: str | None = None
    catchment_area_km2: float | None = None
    gauge_datum_m: float | None = None
    commissioning_year: int | None = None

    model_config = {"extra": "allow"}


class BasinMetadata(BaseModel):
    """Validates basins.metadata JSONB."""

    country: str | None = None
    region: str | None = None
    area_km2: float | None = None
    description: str | None = None

    model_config = {"extra": "allow"}
```

### `schemas/rating_curve.py`

```python
from pydantic import BaseModel, field_validator, model_validator
from typing import Literal, Self


class StageDischargePair(BaseModel):
    stage_m: float
    discharge_m3s: float

    @field_validator("discharge_m3s")
    @classmethod
    def discharge_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("discharge must be >= 0")
        return v


class EquationCoefficients(BaseModel):
    """Power-law rating equation: Q = C * (h - a)^n"""

    form: Literal["power_law"] = "power_law"
    offset_a: float
    coefficient_c: float
    exponent_n: float

    @field_validator("coefficient_c")
    @classmethod
    def coefficient_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("coefficient_c must be > 0")
        return v


class RatingCurveData(BaseModel):
    """Validates rating_curves.data JSONB.
    Exactly one of `pairs` or `equation` must be provided."""

    pairs: list[StageDischargePair] | None = None
    equation: EquationCoefficients | None = None

    @model_validator(mode="after")
    def exactly_one_form(self) -> Self:
        has_pairs = self.pairs is not None
        has_equation = self.equation is not None
        if has_pairs == has_equation:
            raise ValueError("exactly one of 'pairs' or 'equation' must be provided")
        if has_pairs and len(self.pairs) < 2:  # type: ignore[arg-type]
            raise ValueError("pairs must contain at least 2 stage-discharge points")
        return self


class UncertaintyBound(BaseModel):
    stage_m: float
    lower_m3s: float
    upper_m3s: float
    confidence: float

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError("confidence must be between 0 and 1 (exclusive)")
        return v


class RatingCurveUncertainty(BaseModel):
    """Validates rating_curves.uncertainty JSONB."""

    bounds: list[UncertaintyBound]
    method: str | None = None  # e.g. "BaRatin", "manual", "bootstrap"

    @field_validator("bounds")
    @classmethod
    def at_least_one_bound(cls, v: list[UncertaintyBound]) -> list[UncertaintyBound]:
        if len(v) == 0:
            raise ValueError("bounds must contain at least one entry")
        return v
```

### `schemas/model_skill.py`

```python
from pydantic import BaseModel, model_validator
from typing import Self


class SkillMetrics(BaseModel):
    """Validates model_skill.metrics JSONB."""

    nse: float | None = None
    kge: float | None = None
    crps: float | None = None
    bias: float | None = None
    mae: float | None = None
    rmse: float | None = None
    pbias: float | None = None
    r_squared: float | None = None

    model_config = {"extra": "allow"}

    @model_validator(mode="after")
    def at_least_one_metric(self) -> Self:
        all_values = {
            k: v for k, v in self.__dict__.items()
            if not k.startswith("_") and v is not None
        }
        if self.model_extra:
            all_values.update(self.model_extra)
        if not all_values:
            raise ValueError("at least one metric must be provided")
        return self

    @model_validator(mode="before")
    @classmethod
    def extra_values_must_be_numeric(cls, data: dict) -> dict:  # type: ignore[type-arg]
        if not isinstance(data, dict):
            return data
        known = {"nse", "kge", "crps", "bias", "mae", "rmse", "pbias", "r_squared"}
        for k, v in data.items():
            if k not in known and v is not None and not isinstance(v, (int, float)):
                raise ValueError(f"metric '{k}' must be numeric, got {type(v).__name__}")
        return data
```

### `schemas/audit_log.py`

```python
from pydantic import BaseModel, Field
from typing import Annotated, Any, Literal


class LoginDetail(BaseModel):
    action_type: Literal["login", "login_failed"] = "login"
    username: str
    method: str | None = None
    failure_reason: str | None = None


class TokenCreatedDetail(BaseModel):
    action_type: Literal["token_created"] = "token_created"
    token_name: str
    scope_stations: list[str] | None = None
    expires_at: str  # ISO 8601


class TokenRevokedDetail(BaseModel):
    action_type: Literal["token_revoked"] = "token_revoked"
    token_name: str
    revoked_token_id: str


class FlowTriggeredDetail(BaseModel):
    action_type: Literal["flow_triggered"] = "flow_triggered"
    flow_name: str
    trigger: str  # "schedule", "manual", "api"
    station_ids: list[str] | None = None


class ModelConfigChangedDetail(BaseModel):
    action_type: Literal["model_config_changed"] = "model_config_changed"
    station_id: str
    previous_model_id: str | None = None
    new_model_id: str
    previous_version: str | None = None
    new_version: str | None = None


class AdminActionDetail(BaseModel):
    action_type: Literal["admin_action"] = "admin_action"
    operation: str
    target: str | None = None
    context: dict[str, Any] | None = None


AuditDetail = Annotated[
    LoginDetail
    | TokenCreatedDetail
    | TokenRevokedDetail
    | FlowTriggeredDetail
    | ModelConfigChangedDetail
    | AdminActionDetail,
    Field(discriminator="action_type"),
]
```

### `schemas/forecast_adjustment.py`

```python
from pydantic import BaseModel, field_validator


class EnsembleSnapshot(BaseModel):
    """Validates forecast_adjustments.original and .adjusted JSONB."""

    members: list[float]
    median: float | None = None
    quantiles: dict[str, float] | None = None

    @field_validator("members")
    @classmethod
    def at_least_one_member(cls, v: list[float]) -> list[float]:
        if len(v) == 0:
            raise ValueError("members must contain at least one value")
        return v

    @field_validator("quantiles", mode="before")
    @classmethod
    def validate_quantile_keys(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return None
        for key in v:
            try:
                q = float(key)
            except ValueError:
                raise ValueError(f"quantile key '{key}' must be a numeric string")
            if not (0 <= q <= 1):
                raise ValueError(f"quantile level {q} must be between 0 and 1")
        return v
```

### Usage at the store boundary

```python
from sapphire_flow.schemas import SkillMetrics

# Writing: validate before INSERT
metrics_raw = {"nse": 0.82, "crps": 1.3, "bias": -0.05}
validated = SkillMetrics.model_validate(metrics_raw)
# INSERT INTO model_skill ... VALUES (..., validated.model_dump())

# Reading: validate after SELECT
row_jsonb = fetch_row(...)["metrics"]
metrics = SkillMetrics.model_validate(row_jsonb)
```

---

## Open design questions affecting types

1. ~~**FloodThreshold.level uses `str`, not `FloodLevel` enum**~~ — **Resolved**:
   `FloodThreshold.level` remains `str` because it comes from external adapters.
   At the store boundary, `FloodLevel(threshold.level)` parses the string to
   the enum (since `FloodLevel` uses matching string values). Flow code that
   needs the enum converts inline.

2. ~~**StationConfig.parameter_id**~~ — **Resolved**: replaced with
   `forecast_configs: list[ParameterForecastConfig]` to support multi-parameter
   forecasting. Each entry carries its own `parameter_id` and model assignment.

3. ~~**ForecastStore.get_active_rating_curve**~~ — **Resolved**: moved to
   a dedicated `RatingCurveStore` Protocol. `ForecastStore` no longer has
   this method.

4. ~~**UUID vs str for IDs in Protocols**~~ — **Resolved**: Store Protocols
   that operate on a single station in a flow context accept `StationConfig`
   (which carries the UUID `.id`). Store methods used for batch operations
   or adapter-facing code accept `str` (station code). This dual convention
   is intentional: `StationConfig` is available in flows; `str` codes are
   available at adapter boundaries.
