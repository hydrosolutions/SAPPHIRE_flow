# Design Doc Fixes Plan

---
status: READY
---

Fixes for 22 issues identified in the critical design review, plus
revised station model and validation phasing from discussion.

---

## Key design decisions (from discussion)

### 1. River stations and weather stations are separate entities

| Station kind | Metadata | Time series parameters | Forecast role |
|---|---|---|---|
| **River** | coordinates, altitude, code, name | water_level (m asl), discharge (m³/s) | **Forecast target** — model output |
| **Weather** | coordinates, altitude, code, name | precipitation, temperature, humidity, radiation, wind, SWE, ... | **Model input** — observations for training, NWP for operational forcing |

Models take weather inputs and produce river outputs. Weather stations are
never forecast targets. Each river station maps to one or more weather input
sources (nearby weather stations for training, NWP grid points for operational
forecasting).

### 2. Multi-parameter river stations

River stations can have multiple forecast parameters (water_level, discharge).
The forecast target is configurable per (station, parameter) — not per station.
Model assignments are per (station, parameter) pair. A station may use LSTM
for discharge but persistence for water level.

### 3. Validation phasing (Option C: build sub-daily, validate with daily)

| Phase | Data | Resolution | Forecast target | Purpose |
|---|---|---|---|---|
| v0a | CAMELS-CH + MeteoSwiss SMN + hydro_scraper | Daily (historical), sub-daily (operational) | Discharge | Pipeline validation with Swiss data |
| v0b | CAMELS-DE, CAMELS-NZ, CAMELS-US | Sub-daily | Discharge | Sub-daily algorithm testing |
| v0c | 3 BAFU sites (requested sub-daily data) | Sub-daily | Water level + discharge | Swiss sub-daily validation |
| v1 | Nepal DHM + ECMWF IFS | Sub-daily | Water level | Production deployment |

The pipeline is resolution-agnostic from day one. `lead_time_minutes` handles
both daily (1440) and sub-daily (60, 180, 360, ...) lead times. All code
paths (QC, alerts, gap detection, aggregation) work with arbitrary resolution.

### 4. Exceedance probability: configurable per threshold

Exceedance probability is stored per threshold row in `flood_thresholds`,
with global defaults from `config.toml`. This allows hydromets to tune alert
sensitivity per station and severity level.

Global defaults:
- Watch: 20% of ensemble members exceed threshold (low confidence, heads up)
- Warning: 50% (majority agrees)
- Danger: 80% (strong consensus)

Per-station overrides are set via the dashboard or API by editing the
threshold's `exceedance_probability` column. When NULL, the global default
for that level applies.

### 5. NWP archive: ensemble statistics only

Full NWP ensemble archiving is too expensive (~3.86B rows/year at 150
stations). Instead, archive **ensemble summary statistics** (median, 10th
percentile, 90th percentile) per lead time. Full ensemble members are used
for the current forecast cycle, then discarded.

| What's stored | Rows/day (150 stations) | Rows/year | ~GB/year |
|---|---|---|---|
| 3 statistics × 120 lead times × 7 params | ~1.5M | ~552M | ~27 |

This supports:
- Quantile mapping bias correction (NWP median vs observed)
- NWP skill evaluation (verify forecasts against observations)
- Uncertainty calibration (check if 10th/90th brackets observations)

The `weather_forecasts` table stores statistics using reserved negative
`member` values: -1 = median, -2 = 10th percentile, -3 = 90th percentile.
Real ensemble members use non-negative values (0 = control/deterministic,
1-20 = perturbed members). This avoids semantic overlap between the control
run (member=0) and archived statistics.

**Transition mechanism**: During the current forecast cycle, all ensemble
members (0-20) are inserted into `weather_forecasts`. After the forecast
cycle completes, a single transaction DELETEs the real member rows for that
(station_id, parameter_id, issued_at) and INSERTs the 3 statistics rows
(member=-1, -2, -3). This ensures atomicity — at no point do both raw
members and statistics coexist for the same issue time.

CHECK constraint: `member >= -3` prevents accidental garbage data.

### 6. Data retention: archive to Parquet

Raw hydrological forecasts older than 2 years are archived to Parquet files.
NWP statistics older than 3 years are archived. Selected/published forecasts
and skill-relevant forecasts are kept indefinitely in PostgreSQL.

### 7. CAMELS-CH weather data: needs verification

CAMELS-CH includes catchment-averaged weather data but it may be from a
gridded product (not station observations). Need to verify against the paper.
If gridded: use as-is for v0a model training (good enough for pipeline
validation). MeteoSwiss SMN station data remains available as an alternative
or supplement.

---

## Batch 1: Schema gaps

**Goal**: Every table the system writes to has a defined schema in DD-02.

### Fix 1.1 — Add `weather_forecasts` table to DD-02

Add to DD-02 after the `forecast_values` section:

```
### weather_forecasts

| Column           | Type        | Notes                              |
|------------------|-------------|------------------------------------|
| station_id       | UUID        | FK -> stations (river station whose coordinates were used for NWP extraction) |
| parameter_id     | UUID        | FK -> parameters                   |
| issued_at        | TIMESTAMPTZ | When the NWP model run was issued  |
| lead_time_minutes| INTEGER     | Lead time from issued_at           |
| member           | SMALLINT    | ≥0 = real ensemble member (0=control); <0 = archived stats: -1=median, -2=p10, -3=p90 |
| value            | FLOAT       |                                    |

Primary key: (station_id, parameter_id, issued_at, lead_time_minutes, member)

**station_id semantics**: This references the *river station* whose (lon, lat)
coordinates were used to extract the nearest NWP grid point. It does NOT
reference a weather station. NWP grid point extraction is always relative to
the river station's location.

Partitioning: Monthly range on `issued_at`, managed by pg_partman (premake=3).
Same strategy as `forecast_values`.

Volume estimate (ensemble statistics only — median + p10 + p90):
- 50 stations:  50 × 7 × 3 × 120 × 4 = 504K rows/day, ~184M rows/year, ~9 GB/year
- 150 stations: 150 × 7 × 3 × 120 × 4 = 1.51M rows/day, ~552M rows/year, ~27 GB/year

Indexes:
- (station_id, parameter_id, issued_at DESC) — for "latest NWP forecast" queries.
  DESC on issued_at avoids backwards index scans.
```

No standalone `(issued_at)` index — partition pruning uses partition bounds
metadata, not an index.

Update the data volume estimates section in DD-02 to include weather_forecasts.

Also update `WeatherForecast.member` docstring in types-and-protocols.md from:
`member: int  # ensemble member index (0 = control/deterministic)`
to:
`member: int  # ≥0 = real ensemble member (0=control); reserved negative: -1=median, -2=p10, -3=p90`

This documents the reserved negative values so implementers know that
`WeatherForecast` objects with `member=-1`, `-2`, or `-3` are valid
(they represent archived ensemble statistics, not raw ensemble members).
Real ensemble members always use non-negative values.

**Also update `forecast_values.member` in DD-02**: The existing DD-02 column
note says `Ensemble member index (0 = median)`, which conflicts with the
convention above. Change it to:
`Ensemble member index (0 = deterministic/control). forecast_values stores
raw ensemble output only — no statistics rows. Median and percentiles are
computed on read.`
This aligns both tables: non-negative member values always mean real ensemble
members (0 = control/deterministic), negative values are reserved for archived
statistics (used only in `weather_forecasts`).

### Fix 1.2 — Document `users` table expectations in DD-02

Add a note after `audit_log`:

```
### users (managed by fastapi-users)

Created and managed by the `fastapi-users` library. The application relies on:
- `id` (UUID): PK, referenced by observation_edits.edited_by,
  forecast_adjustments.adjusted_by, rating_curves.created_by,
  bulletins.generated_by, audit_log.user_id, station_model_config.updated_by
- `email` (TEXT): unique, used for login
- `hashed_password` (TEXT)
- `is_active` (BOOLEAN)
- `is_superuser` (BOOLEAN)
- `role` (TEXT): custom column — "viewer", "forecaster", "admin"
- `totp_secret` (TEXT): nullable, for MFA

The exact schema is determined by fastapi-users at migration time. Additional
columns (role, totp_secret) are added via a custom user model that extends
the fastapi-users base.
```

### Fix 1.3 — Add `dead_letter_queue` table to DD-02

Add after the partitioning discussion:

```
### dead_letter_queue

| Column       | Type        | Notes                              |
|--------------|-------------|------------------------------------|
| id           | BIGSERIAL   | PK                                 |
| source_table | TEXT        | "observations", "forecast_values", or "weather_forecasts" |
| payload      | JSONB       | The row that failed to insert      |
| error        | TEXT        | PostgreSQL error message           |
| created_at   | TIMESTAMPTZ | DEFAULT now()                      |
| drained_at   | TIMESTAMPTZ | nullable — set when successfully replayed |

Unpartitioned, append-only. Expected to be empty during normal operation.

Index: partial index on `(created_at) WHERE drained_at IS NULL` for
efficient drain queries and startup checks.

Drain process: A Prefect maintenance flow (daily or on-demand, with
`concurrency_limit=1` to prevent concurrent drain attempts) queries rows
using `SELECT ... FOR UPDATE SKIP LOCKED WHERE drained_at IS NULL`,
attempts to INSERT each into its `source_table`, and sets `drained_at`
on success. Rows that still fail (e.g. partition still missing) are
left for the next run. The health endpoint reports non-empty dead letter
queue as a warning.

Startup check: If the dead letter queue has undrained rows, log a warning
with count and oldest `created_at`.
```

### Fix 1.4 — Fix database user permissions in DD-01, DD-07, and conventions.md

The database user permissions section lives in DD-01 (line 230), DD-07
(line 216), and conventions.md (line 194). Update all three to:

```
sapphire_api:
  SELECT on all tables (including new: weather_forecasts, dead_letter_queue,
    station_weather_sources)
  INSERT/UPDATE on: observation_edits, forecast_adjustments, bulletins,
    audit_log, alert_events, forecasts (status + version), access_tokens
  Note: "acknowledge only" on alert_events is enforced at the application
  level (API route checks), not via PostgreSQL GRANT. The DB grants full
  INSERT/UPDATE on alert_events to sapphire_api.

sapphire_worker:
  SELECT/INSERT/UPDATE on: observations, forecasts, forecast_values,
    alert_events, model_skill, weather_forecasts, dead_letter_queue
  DELETE on: weather_forecasts (ensemble-to-statistics transition only)

sapphire_prefect:
  Full access to `prefect` database only, no access to `sapphire_flow` tables
```

---

## Batch 2: Protocol & type fixes

**Goal**: All Protocol signatures are consistent and implementable.

### Fix 2.1 — Resolve str vs UUID in store Protocol methods

**Rule**: Store methods called from flow context (where `StationConfig` is
available) accept `UUID` or `StationConfig`. Methods called from adapter
boundaries or CLI accept `str` (station codes).

Changes to types-and-protocols.md:

| Method | Current param | Change to |
|--------|--------------|-----------|
| `ObservationStore.get_observations` | `station_id: str, parameter_id: str` | `station_id: UUID, parameter_id: UUID` |
| `ObservationStore.get_latest_observation` | `station_id: str, parameter_id: str` | `station_id: UUID, parameter_id: UUID` |
| `ObservationStore.get_previous_observations` | `list[tuple[str, str]]` | `list[tuple[UUID, UUID]]` |
| `ObservationStore.detect_gaps` | `station_ids: list[str]` | `station_ids: list[UUID]` |
| `ObservationStore.upsert_observations` | return `-> int` | return `-> tuple[int, dict[tuple[str, str], tuple[UUID, UUID]]]` |
| `WeatherStore.get_weather_forecasts` | `station_id: str` | `station_id: UUID` |
| `AlertStore.get_thresholds` | `station_id: str` | `station_id: UUID, parameter_id: UUID` |
| `AlertStore.get_thresholds_batch` | `set[tuple[str, str]]` | `set[tuple[UUID, UUID]]` |
| `AlertStore.resolve_observation_alerts` | `station_id: str, parameter_id: str` | `station_id: UUID, parameter_id: UUID` |
| `TrainingStore.prepare_training_data` | `station_id: str` | `station_id: UUID` |

Note: `parameter_id` changes from `str` to `UUID` alongside `station_id` in
all methods that accept both. The table above shows the complete change for
each method.

`upsert_observations` return type changes to also return a `code_to_uuid`
mapping (`(station_code, parameter_name) → (station_id, parameter_id)`).
This mapping is built during the store's internal code→UUID resolution and
is passed to `check_observation_alerts` (see Fix 3.2).

Methods that stay `str` (adapter boundary — input params unchanged):
- `ObservationStore.upsert_observations` — receives `list[Observation]` which uses `station_code: str`
- `WeatherStore.upsert_weather_forecasts` — same
- `AlertStore.upsert_thresholds` — receives `list[FloodThreshold]` with `station_code: str`

The store implementation resolves `station_code → UUID` internally for
upsert methods. Flow-facing query methods use UUID directly.

Also update DD-01's Protocol snippets for **all** affected stores
(ObservationStore, WeatherStore, AlertStore, TrainingStore) to use UUID
instead of str, matching the types-and-protocols.md changes above. DD-01
contains its own copies of these Protocol signatures that must stay in sync.

### Fix 2.2 — Multi-parameter station model

This is the largest structural change. It affects DD-02 (schema), DD-05
(flows), DD-01 (StationStore Protocol), DD-06 (API), types-and-protocols.md,
and conventions.md.

#### 2.2a — New types (types-and-protocols.md)

Replace `StationConfig.parameter_id: str` with per-parameter model config:

```python
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

Remove open question #2 from types-and-protocols.md ("StationConfig.parameter_id
— is this always a single parameter?") — resolved by this change.

#### 2.2b — Updated `station_model_config` table (DD-02)

Replace the existing `station_model_config` definition. Change prose from
"One row per station" to "One row per (station, parameter) pair":

```
### station_model_config

| Column           | Type        | Notes                              |
|------------------|-------------|------------------------------------|
| id               | UUID        | PK                                 |
| station_id       | UUID        | FK -> stations                     |
| parameter_id     | UUID        | FK -> parameters                   |
| model_id         | TEXT        | FK -> models                       |
| model_version    | TEXT        |                                    |
| model_artifact   | TEXT        | Path to model weights/parameters   |
| fallback_model_id| TEXT        | FK -> models, nullable             |
| fallback_artifact| TEXT        | Path to fallback artifact, nullable|
| updated_by       | UUID        | FK -> users                        |
| updated_at       | TIMESTAMPTZ | DEFAULT now()                      |

Unique constraint: (station_id, parameter_id)

One row per (station, parameter) pair. A river station forecasting both
water_level and discharge has two rows. Changes are made via the
API/dashboard — no container restart needed.
```

Update the operational workflow, role permissions, and bootstrap TOML
sections in DD-02 to reference the (station, parameter) pair instead of
just station.

#### 2.2c — Add `station_weather_sources` junction table (DD-02)

```
### station_weather_sources

| Column              | Type   | Notes                              |
|---------------------|--------|------------------------------------|
| river_station_id    | UUID   | FK -> stations (kind=river)        |
| weather_station_id  | UUID   | FK -> stations (kind=weather)      |
| distance_km         | FLOAT  | Haversine distance, computed at import time |

Primary key: (river_station_id, weather_station_id)

Populated during station import via CLI or API. The import tool computes
haversine distance from station coordinates. If a station's coordinates
are later updated, distance_km must be recomputed (flagged by a warning
in the station update API response).
```

#### 2.2d — Updated StationStore Protocol (types-and-protocols.md, DD-01)

Add `parameter_id` to model assignment methods:

```python
class StationStore(Protocol):
    # ... existing methods unchanged ...

    def get_model_assignment(
        self, station_id: UUID, parameter_id: UUID,
    ) -> ModelAssignment | None: ...

    def upsert_model_assignment(
        self,
        station_id: UUID,
        parameter_id: UUID,       # NEW
        model_id: str,
        model_version: str,
        artifact_path: str,
        fallback_model_id: str | None = None,
        fallback_artifact: str | None = None,
    ) -> None: ...

    def bulk_upsert_model_assignments(
        self,
        assignments: list[tuple[str, str, ModelAssignment, ModelAssignment | None]],
        # Each tuple: (station_code, parameter_name, primary, fallback | None)
    ) -> int: ...
```

Update DD-01's StationStore Protocol snippet to match.

Also update `get_active_station_configs()` in the StationStore Protocol to
document that it must JOIN `station_weather_sources` to populate
`StationConfig.weather_source_ids`. The implementation uses:
`LEFT JOIN station_weather_sources sws ON sws.river_station_id = s.id`,
grouping to collect weather station UUIDs into a list per river station.
Stations with no linked weather sources get an empty list.

#### 2.2e — Updated `forecast_station` task (DD-05)

Replace the current `forecast_station` with a per-parameter loop:

```python
@task(retries=1, retry_delay_seconds=30, timeout_seconds=300)
def forecast_station(
    station: StationConfig,
    forecast_store: ForecastStore,
    observation_store: ObservationStore,
    weather_store: WeatherStore,
    alert_store: AlertStore,
    model_registry: ModelRegistry,
    alert_config: AlertConfig,
) -> list[Forecast]:
    """Produce forecasts for all configured parameters at this station."""
    results = []

    for param_config in station.forecast_configs:
        forecast = forecast_single_parameter(
            station, param_config,
            forecast_store, observation_store, weather_store,
            alert_store, model_registry, alert_config,
        )
        if forecast is not None:
            results.append(forecast)

    return results


def forecast_single_parameter(
    station: StationConfig,
    param_config: ParameterForecastConfig,
    forecast_store: ForecastStore,
    observation_store: ObservationStore,
    weather_store: WeatherStore,
    alert_store: AlertStore,
    model_registry: ModelRegistry,
    alert_config: AlertConfig,
) -> Forecast | None:
    # Load model first (needed to check needs_full_ensemble for input prep).
    try:
        model = model_registry.load(param_config.model_config)
    except ModelLoadError:
        logging.exception("Failed to load primary model for %s/%s, trying fallback",
                          station.code, param_config.parameter_name)
        if param_config.fallback_config is None:
            return None
        try:
            model = model_registry.load(param_config.fallback_config)
        except ModelLoadError:
            logging.exception("Fallback model also failed to load for %s/%s, skipping",
                              station.code, param_config.parameter_name)
            return None

    # Prepare inputs OUTSIDE model try block — input prep is not model-specific,
    # so failure here skips the station entirely (no fallback can help).
    try:
        use_full = getattr(model, "needs_full_ensemble", False)
        inputs = prepare_model_inputs(
            station, param_config, observation_store, weather_store,
            use_full_ensemble=use_full,
        )
    except Exception:
        logging.exception("Input preparation failed for %s/%s, skipping",
                          station.code, param_config.parameter_name)
        return None

    try:
        primary_obs = inputs.observations.get(param_config.parameter_name, [])
        observation_span_hours = (
            (primary_obs[-1][0] - primary_obs[0][0]).total_seconds() / 3600
            if len(primary_obs) >= 2 else 0
        )
        if observation_span_hours < model.min_lookback_hours:
            logging.warning(
                "Insufficient data for %s/%s (%.0f hrs, %d required), trying fallback",
                station.code, param_config.parameter_name,
                observation_span_hours, model.min_lookback_hours,
            )
            raise InsufficientDataError(station.code)

        ensemble = model.predict(inputs)
        validate_ensemble(ensemble, station.metadata)
    except (InsufficientDataError, SanityCheckFailure, RuntimeError):
        logging.exception("Primary model failed for %s/%s, trying fallback",
                          station.code, param_config.parameter_name)
        if param_config.fallback_config is None:
            logging.error("No fallback configured for %s/%s, skipping",
                          station.code, param_config.parameter_name)
            return None
        try:
            fallback_model = model_registry.load(param_config.fallback_config)
            # Re-prepare inputs if fallback has different ensemble requirements
            fallback_needs_full = getattr(fallback_model, "needs_full_ensemble", False)
            if fallback_needs_full != use_full:
                inputs = prepare_model_inputs(
                    station, param_config, observation_store, weather_store,
                    use_full_ensemble=fallback_needs_full,
                )
            ensemble = fallback_model.predict(inputs)
            validate_ensemble(ensemble, station.metadata)
        except (InsufficientDataError, SanityCheckFailure, ModelLoadError, RuntimeError):
            logging.exception("Fallback also failed for %s/%s, skipping",
                              station.code, param_config.parameter_name)
            return None

    # v2.0: apply_rating_curve here when discharge conversion is implemented

    forecast = forecast_store.save_forecast(station, param_config, ensemble)
    check_flood_alert(
        forecast, ensemble, station, param_config,
        alert_store, alert_config,
    )
    return forecast
```

**ForecastStore.save_forecast signature change**: Add `param_config` parameter
so the store knows which parameter this forecast is for:

```python
def save_forecast(
    self, station: StationConfig, param_config: ParameterForecastConfig,
    ensemble: ForecastEnsemble,
) -> Forecast: ...
```

The store uses `param_config.parameter_id` (UUID) for the `forecasts.parameter_id`
column. `ForecastEnsemble.parameter_id` stays as `str` (the canonical name set
by the model) — it is a model-facing type and models don't know about UUIDs.
The store resolves the mismatch using `param_config`.

**Propagation**: Update the `ForecastStore` Protocol in **both**
types-and-protocols.md and DD-01 to match this new signature:

```python
class ForecastStore(Protocol):
    # ... other methods unchanged ...

    def save_forecast(
        self, station: StationConfig, param_config: ParameterForecastConfig,
        ensemble: ForecastEnsemble,
    ) -> Forecast: ...
```

Note: `timeout_seconds` increased from 120 to 300 to accommodate multiple
parameters per station.

#### 2.2f — Updated `check_flood_alert` (DD-05)

**Dependency**: This fix depends on Fix 3.1 (exceedance probability changes)
being applied first. Fix 3.1 adds `FloodThreshold.exceedance_probability` and
replaces `AlertConfig` with the `default_exceedance` method. Apply Fix 3.1
before this fix.

Add `param_config` parameter, use it instead of `station.parameter_id`:

```python
@task
def check_flood_alert(
    forecast: Forecast,
    ensemble: ForecastEnsemble,
    station: StationConfig,
    param_config: ParameterForecastConfig,
    store: AlertStore,
    alert_config: AlertConfig,
):
    thresholds = store.get_thresholds(station.id, param_config.parameter_id)
    if not thresholds:
        return

    # ... lead time loop unchanged ...

    for threshold in thresholds:
        # Per-threshold exceedance probability (or global default)
        min_probability = threshold.exceedance_probability or \
            alert_config.default_exceedance(FloodLevel(threshold.level))

        if exceedance_fraction >= min_probability:
            store.raise_alert(station, forecast, lead_time, threshold,
                              exceedance_fraction=exceedance_fraction)

    store.resolve_stale_alerts(station, forecast)
    # ... notification unchanged ...
```

#### 2.2g — Updated `compute_model_skill` (DD-05)

```python
@flow(log_prints=True)
def compute_model_skill(
    forecast_store: ForecastStore,
    observation_store: ObservationStore,
    skill_store: SkillStore,
    station_configs: list[StationConfig],
    lookback_days: int = 90,
    clock: Callable[[], datetime] = datetime.now,
):
    for station in station_configs:
        for param_config in station.forecast_configs:
            forecasts = forecast_store.get_past_forecasts(station, lookback_days=lookback_days)
            observations = observation_store.get_observations(
                station.id, param_config.parameter_id,
                start=clock() - timedelta(days=lookback_days),
                end=clock(),
            )

            model_ids = [param_config.model_config.model_id]
            if param_config.fallback_config:
                model_ids.append(param_config.fallback_config.model_id)
            for model_id in model_ids:
                model_forecasts = [
                    f for f in forecasts
                    if f.model_id == model_id and f.parameter_id == param_config.parameter_id
                ]
                metrics = compute_metrics(model_forecasts, observations)
                skill_store.save_skill_scores(
                    station, param_config.parameter_id, model_id, metrics,
                )
```

**SkillStore Protocol update**: Update `save_skill_scores` in both
types-and-protocols.md and DD-01 to accept `parameter_id`:

```python
class SkillStore(Protocol):
    def save_skill_scores(
        self, station: StationConfig, parameter_id: UUID,
        model_id: str, metrics: dict[str, float],
    ) -> None: ...
```

This distinguishes skill scores for water_level vs discharge at the same
station. The `model_skill` table in DD-02 already has a `parameter_id`
column.

#### 2.2h — Updated `run_forecasts` flow (DD-05)

Updated signature — `rating_curve_store` removed (see Fix 3.4):

```python
@flow(log_prints=True)
def run_forecasts(
    station_store: StationStore,
    forecast_store: ForecastStore,
    observation_store: ObservationStore,
    weather_store: WeatherStore,
    alert_store: AlertStore,
    model_registry: ModelRegistry,
    alert_config: AlertConfig,
):
    station_configs = station_store.get_active_station_configs()

    futures = [
        forecast_station.submit(
            station, forecast_store, observation_store, weather_store,
            alert_store, model_registry, alert_config,
        )
        for station in station_configs
    ]

    results = [f.result(raise_on_failure=False) for f in futures]
    # Each result is now list[Forecast], not Forecast | None
    total_forecasts = sum(len(r) for r in results if r is not None)
    total_stations = sum(1 for r in results if r is not None and len(r) > 0)
    logging.info("Forecast complete: %d forecasts for %d/%d stations",
                 total_forecasts, total_stations, len(station_configs))
```

#### 2.2i — Updated DD-06 API endpoints

Update model-config API to include parameter:

```
GET    /api/v1/stations/{id}/model-config                All parameter configs
GET    /api/v1/stations/{id}/model-config/{parameter}    Config for specific parameter
PATCH  /api/v1/stations/{id}/model-config/{parameter}    Change model for a parameter
GET    /api/v1/admin/model-config                        All station model configs
PATCH  /api/v1/admin/model-config/bulk                   Bulk update
```

#### 2.2j — Updated `check_observation_alerts` (DD-05)

Weather station ingest does NOT run flood alert checking — only river
station ingest does. Add a note to `ingest_stations`:

```python
@flow(log_prints=True)
def ingest_stations(
    adapter: StationDataSource,
    store: ObservationStore,
    alert_store: AlertStore,
    qc_service: QualityCheckService,
    station_ids: list[str],
    check_alerts: bool = True,  # False for weather station ingest
):
    # ... fetch + QC unchanged ...

    rows_upserted, code_to_uuid = store.upsert_observations(observations)
    logging.info("Upserted %d observations", rows_upserted)

    if check_alerts:
        check_observation_alerts(observations, store, alert_store, code_to_uuid)
```

### Fix 2.3 — Add `generated_by` to BulletinStore.save_bulletin

```python
def save_bulletin(
    self, scope: BulletinScope, basin_id: str | None,
    template_id: str, path: str, forecast_ids: list[str],
    generated_by: UUID,
) -> None: ...
```

Also update the `generate_bulletin` flow call site in DD-05 to pass the
user ID from the triggering request context. Update DD-01's BulletinStore
Protocol snippet to include the `generated_by: UUID` parameter.

### Fix 2.4 — Document parameter naming convention

Add to DD-03 and conventions.md:

River station parameters use canonical names:
- `water_level` (m, reference datum documented per station in metadata)
- `discharge` (m³/s)

Weather station parameters use canonical names mapped from adapter-specific
names at ingest:
- `precipitation` (mm) — SMN: `rre150h0`, CAMELS-CH: TBD
- `temperature` (°C) — SMN: `tre200h0`
- `humidity` (%) — SMN: `ure200h0`
- `radiation` (W/m²) — SMN: `gre000h0`
- `wind_speed` (m/s) — SMN: `fkl010h0`
- `snow_depth` (cm) — SMN: `htoauths`
- `reference_et` (mm/h) — SMN: `erefaoh0`
- `swe` (mm) — snow water equivalent, if available

Each adapter maps its source-specific parameter names to these canonical
names. The `parameters` table stores the canonical names.

**Naming convention for types**: `ParameterForecastConfig` has both
`parameter_name: str` (the canonical name, e.g. `"discharge"`) and
`parameter_id: UUID` (the FK to the `parameters` table). Flow code uses
`parameter_name` for human-readable contexts and `parameter_id` for
database queries. The `StationConfig` builder (in the store layer)
resolves both from the `parameters` table join.

---

## Batch 3: Logic fixes

**Goal**: Alert, forecast, and fallback logic behave correctly.

### Fix 3.1 — Make exceedance probability configurable per threshold

In DD-02, add `exceedance_probability` column to `flood_thresholds`:

```
| exceedance_probability | FLOAT | nullable — when NULL, use global default for this level |
```

Update composite key to include it: `(station_id, parameter_id, level,
valid_from_month, valid_to_month)` — unchanged (exceedance_probability is
not part of the key).

In types-and-protocols.md, update `FloodThreshold`:

```python
class FloodThreshold(NamedTuple):
    station_code: str
    parameter: str
    level: str
    value: float
    unit: str
    valid_from_month: int | None = None
    valid_to_month: int | None = None
    exceedance_probability: float | None = None  # NEW — null = use global default
```

**Replace** the existing `AlertConfig` class in types-and-protocols.md
(current fields: `exceedance_danger`, `exceedance_warning`, `exceedance_watch`;
current method: `min_exceedance_probability`) with the following:

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

Also update DD-05's existing `check_flood_alert` code to use the new
method name (`default_exceedance` replaces `min_exceedance_probability`).

Update TOML example:
```toml
[alerts]
# Global defaults — overridden by per-threshold exceedance_probability in DB
default_exceedance_watch = 0.2
default_exceedance_warning = 0.5
default_exceedance_danger = 0.8
```

**Semantic reversal note**: This changes the exceedance convention from the
current spec. Previously, danger had the lowest probability threshold (0.2,
easiest to trigger — conservative for life safety) and watch had the highest
(0.7, hardest to trigger). The new convention reverses this: watch=0.2
(triggers easily, early warning) and danger=0.8 (requires strong ensemble
consensus to avoid false alarms at the highest severity). This matches
standard meteorological ensemble practice where higher severity alerts
demand stronger forecast confidence.

Update DD-05 explanatory text: "Higher severity requires stronger ensemble
agreement by default. Watch triggers when even a minority of members signal
elevated levels (20%). Danger requires near-consensus (80%). These defaults
can be overridden per station and level via the flood_thresholds table,
allowing hydromets to tune sensitivity for specific locations (e.g. lower
danger threshold for stations protecting critical infrastructure)."

### Fix 3.2 — Observation alerts raise for all exceeded thresholds

In DD-05, change `check_observation_alerts` to raise alerts for all exceeded
thresholds, not just the worst. The function receives a `code_to_uuid` mapping
built during ingest (from `upsert_observations`'s internal resolution):

```python
@task
def check_observation_alerts(
    observations: list[Observation],
    store: ObservationStore,
    alert_store: AlertStore,
    code_to_uuid: dict[tuple[str, str], tuple[UUID, UUID]],
    # Maps (station_code, parameter_name) -> (station_id, parameter_id)
    # Built during ingest from the store's internal code→UUID resolution.
):
    for (station_code, param_name), (station_id, parameter_id) in code_to_uuid.items():
        thresholds = alert_store.get_thresholds(station_id, parameter_id)
        if not thresholds:
            continue
        latest = store.get_latest_observation(station_id, parameter_id)
        if latest is None:
            continue

        for threshold in thresholds:
            if latest.value >= threshold.value:
                alert_store.raise_observation_alert(latest, threshold)

        # Resolve alerts for thresholds no longer exceeded —
        # uses the existing resolve_observation_alerts(station_id, parameter_id)
        # which resolves all observation alerts for this station+parameter
        # where the latest value no longer exceeds the threshold.
        alert_store.resolve_observation_alerts(station_id, parameter_id)
```

This matches the forecast alert behavior which checks each threshold
independently. Resolution uses the existing `resolve_observation_alerts`
method (plural) which checks all observation-source alerts for the given
station+parameter against the latest observation and resolves those whose
threshold is no longer exceeded. The resolution logic lives in the store
implementation, keeping the flow code simple.

The `code_to_uuid` mapping is returned by the
`ObservationStore.upsert_observations` method as a side product of its
internal code→UUID resolution (see Fix 2.1 for the Protocol signature change).

### Fix 3.3 — Reuse prepared inputs in fallback path

Already addressed in Fix 2.2e — the `forecast_single_parameter` function
prepares inputs once and reuses them for the fallback model.

### Fix 3.4 — Remove rating curve code from v1.0 flow

Already addressed in Fix 2.2e — `forecast_single_parameter` has a
`# v2.0: apply_rating_curve here` comment placeholder. Remove
`rating_curve_store` from the `forecast_station` and `run_forecasts`
signatures in DD-05. Move the rating curve application description to the
v2.0 section of DD-04.

---

## Batch 4: Underspecified areas

**Goal**: A junior developer can implement each component without asking
questions.

### Fix 4.1 — Weather-to-station mapping

Add to DD-03:

```
### Weather input mapping

Each river station is linked to one or more weather stations via the
`station_weather_sources` table (see DD-02). This mapping is:
- Created during station import (CLI or API)
- Distance computed via haversine formula from station coordinates
- Configurable max distance threshold (default 50 km) — stations beyond
  this are excluded with a warning
- Editable via the admin API/dashboard

For NWP forecasts, the adapter extracts the nearest grid point to the
*river station's* coordinates (not a weather station). The extraction
uses the river station's (lon, lat) directly against the NWP grid. NWP
data is stored in `weather_forecasts` keyed by the river station's UUID.

For model training, the `prepare_model_inputs` service queries historical
observations from the linked weather stations (via `station.weather_source_ids`)
for the configured weather parameters.
```

Update `prepare_model_inputs` signature in DD-05, types-and-protocols, and
DD-01's `services/forecast_prep.py` snippet:

```python
def prepare_model_inputs(
    station: StationConfig,
    param_config: ParameterForecastConfig,
    observation_store: ObservationStore,
    weather_store: WeatherStore,
    use_full_ensemble: bool = False,
) -> ModelInputs: ...
```

The `use_full_ensemble` parameter is determined by the caller (see Fix 4.5)
from `getattr(model, "needs_full_ensemble", False)` after loading the model.
This keeps `prepare_model_inputs` model-agnostic.

**ModelInputs field types**: `ModelInputs.station_id` and `parameter_id` stay
as `str` (station code and canonical parameter name respectively). These are
model-facing types — models use human-readable identifiers, not UUIDs. The
`prepare_model_inputs` function receives UUIDs via `StationConfig` and
`ParameterForecastConfig` but constructs `ModelInputs` with string values
(`station.code` and `param_config.parameter_name`). Add a clarifying comment
to the `ModelInputs` definition in types-and-protocols.md:

```python
class ModelInputs(NamedTuple):
    station_id: str             # station code (human-readable, not UUID)
    parameter_id: str           # canonical parameter name, e.g. "discharge"
    ...
```

### Fix 4.2 — Clarify caching strategy

In DD-03, replace the vague "a `cache` table or the existing tables" with:

```
### Caching strategy

No separate cache table. Each adapter's data is cached in its destination
table:
- NWP statistics → `weather_forecasts` table (ensemble stats archived permanently)
- SMN weather observations → `observations` table
- BAFU river observations → `observations` table

Staleness is determined by querying `MAX(issued_at)` or `MAX(timestamp)`
for the relevant station and parameter. The adapter's `max_cache_age_hours`
config is compared against this timestamp.

For NWP GRIB2 files: raw files are cached on a Docker volume
(`/data/grib_cache/`) for 48 hours to enable retry on parse failure.
This is a filesystem cache, not database-backed. The volume is mounted
in the worker container. Files older than 48 hours are cleaned up by a
daily maintenance task.
```

### Fix 4.3 — Specify ingest → forecast trigger

In DD-05, replace the vague Prefect automation reference:

```
### Ingest → forecast trigger

The forecast flow is triggered by a Prefect automation:

    Automation: "trigger-forecasts-after-ingest"
    Trigger: ALL of:
      - Flow "ingest-weather" completed successfully
      - Flow "ingest-stations" completed successfully
      - Both completions within a 30-minute window
    Action: Run deployment "run-forecasts"

If the automation misfires (Prefect server crash between ingest and trigger),
the catch-up flow (every 15 minutes, reduced from 30) detects the gap and
triggers a forecast run.

Manual triggers via API (`POST /api/v1/flows/forecast/trigger`) bypass the
automation and run immediately.
```

Reduce catch-up interval from 30 to 15 minutes.

### Fix 4.4 — Specify TrainingStore.prepare_training_data

In types-and-protocols.md and DD-01's TrainingStore Protocol snippet:

```python
class TrainingStore(Protocol):
    def prepare_training_data(
        self,
        station_id: UUID,
        parameter_id: UUID,
        start: datetime | None = None,  # default: earliest available
        end: datetime | None = None,    # default: latest available
        weather_params: list[str] | None = None,  # default: all linked
    ) -> TrainingDataset: ...

    def log_training_result(self, station_id: UUID, result: TrainResult) -> None: ...
```

Document: "Returns QC-passed observations (quality_flag != 9) for the
target parameter at the river station, plus observations from linked
weather stations for the specified weather parameters. Gaps are
represented as missing entries, not interpolated."

### Fix 4.5 — Specify weather ensemble reduction

In types-and-protocols.md, update the ModelInputs docs:

```
Weather ensemble reduction is configured per deployment in config.toml:

    [forecast_prep]
    weather_ensemble_method = "median"  # "median" or "mean"

Models that need full weather ensemble members declare this via an
**optional** class attribute (NOT part of the ForecastModel Protocol, since
Python Protocols cannot have default implementations):

    class MyEnsembleModel:
        needs_full_ensemble: bool = True  # opt-in

        def predict(self, inputs: ModelInputs) -> ForecastEnsemble: ...

The check happens in `forecast_single_parameter` (Fix 2.2e), NOT inside
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
```

**Important**: `needs_full_ensemble` is NOT added to the `ForecastModel`
Protocol definition. It is an optional attribute checked via `getattr` with
a default of `False`. Models that don't define it get reduced ensemble input.
This avoids breaking the Protocol contract for existing model implementations.

---

## Batch 5: Design improvements

### Fix 5.1 — Update volume estimates for 50-150 stations

In DD-02, update the data volume estimates section:

```
## Data volume estimates (5 years, 50-150 river stations)

River stations: 50-150 (with linked weather stations, ~160 SMN total)

- Observations — river (15-min, sub-daily operational):
  150 × 2 params × 35,040/yr × 5 yrs = 52.5M rows
- Observations — river (daily, CAMELS-CH backfill, 25 yr):
  150 × 2 × 9,125 = 2.7M rows
- Observations — weather (hourly, SMN, 45 yr):
  160 × 7 params × 8,760/yr × 45 yrs = 441M rows
- Weather forecast statistics (NWP, 3 stats × 4 runs/day):
  150 × 7 × 3 × 120 × 4 × 365 × 5 = 2.76B rows (5 yr)
  (~552M rows/year, ~27 GB/year at 50 bytes/row)
- Hydrological forecasts (2 runs/day, 50 members, 15 lead times):
  150 × 730 × 50 × 15 × 5 = 411M rows (5 yr)
- Observation edits: negligible
- Model skill: negligible
- Bulletins: ~3,650 rows (5 yr)

Weather forecast statistics is the largest table. Monthly partitioning
is critical — each monthly partition holds ~46M rows.
```

### Fix 5.2 — Add data retention policy

Add to DD-07:

```
### Data retention

| Data | Retention in PG | Archive format |
|------|-----------------|---------------|
| Observations | Indefinite | In PostgreSQL |
| Weather forecast stats | 3 years | Parquet on volume, then S3/offsite |
| Hydrological forecasts (raw) | 2 years | Parquet on volume |
| Hydrological forecasts (selected/published) | Indefinite | In PostgreSQL |
| Model skill scores | Indefinite | In PostgreSQL |
| Audit log | Per deployment (min 5 years) | In PostgreSQL |
| Bulletins | Indefinite | In PostgreSQL + generated files |

Archive process: A monthly maintenance flow exports old partitions to
Parquet (via `COPY ... TO PROGRAM`), verifies row counts AND aggregate
checksums (MIN/MAX timestamps, SUM of values) match, then drops the
partition. Archived Parquet files are included in offsite backup.

FK cascade ordering for partition drops:
1. `forecast_values` partition (references `forecasts.id`)
2. `alert_events` rows referencing affected forecasts (SET NULL on
   `forecast_id`, preserving the alert record)
3. `forecasts` rows (only raw, non-selected forecasts)
4. `weather_forecasts` partition (no FK dependencies pointing to it)

The archive flow enforces this ordering. Selected/published forecasts
and their forecast_values are never archived.
```

### Fix 5.3 — Add Prefect upgrade strategy

Add to DD-07:

```
### Prefect version management

Pin to a specific minor version (e.g. 3.2.x). Upgrade policy:
- Test new Prefect versions on staging before production
- Only upgrade between forecast seasons (not during active flood monitoring)
- Document which Prefect APIs are used: deployments, automations,
  .submit() for concurrent tasks, flow/task decorators, CronSchedule

Known risk: Prefect 3.x API is evolving. The deployment API changed between
3.0 and 3.1. Pin exact version in docker-compose.yml and test upgrades
explicitly.
```

### Fix 5.4 — Document single worker as v1.0 limitation

Add to DD-01:

```
### Worker scaling

v1.0 uses a single Prefect worker container. This is sufficient for
50-150 stations (forecast cycle completes in minutes with concurrent
task submission). Scaling limitations:
- Worker crash fails all in-progress tasks (Prefect retry handles this)
- CPU-bound models compete for the same cores

v2.0 consideration: Add a second worker container for redundancy, or
scale horizontally with Prefect's work pool feature.
```

### Fix 5.5 — Update validation phasing in DD-00

Replace the existing v0 block in DD-00 scope priorities with three
sub-phases. The existing v0 text (lines 55-71) is replaced entirely:

```
### v0a (2026 H1) — Swiss daily pipeline validation
- CAMELS-CH daily discharge + catchment weather for model training
- MeteoSwiss SMN hourly weather stations as supplemental forcing data
- ICON-CH2-EPS ensemble NWP for operational weather forcing
- hydro_scraper for operational river gauge data (sub-daily)
- Daily forecast models (regression, persistence, possibly HBV)
- Full pipeline: ingest → forecast → alert → API
- NWP statistics archiving from day one
- No Nepal-specific features
- No security hardening

### v0b (2026 H1-H2) — Sub-daily algorithm testing
- CAMELS-DE, CAMELS-NZ, CAMELS-US sub-daily datasets
- Sub-daily forecast models (LSTM, transformer)
- Validates sub-daily code paths with real data
- No operational deployment — research/development phase

### v0c (2026 H2) — Swiss sub-daily validation
- 3 BAFU sites with requested sub-daily water level + discharge
- End-to-end sub-daily pipeline with Swiss operational data
- Validates the full sub-daily operational workflow
- Staging environment on AWS with Swiss data running continuously
```

Add CAMELS adapters to DD-03 planned adapters table:

| Adapter | Implements | Data source | Region | Phase |
|---------|-----------|-------------|--------|-------|
| camels_ch | StationDataSource | CAMELS-CH dataset (CSV) | Switzerland | v0a |
| camels_generic | StationDataSource | CAMELS-DE/NZ/US (CSV) | Multi | v0b |

---

## Minor fixes (folded into batch that touches each doc)

| Fix | Doc | Batch |
|-----|-----|-------|
| Note QualityFlag 5-8 reserved for future checks | DD-02, types-and-protocols | 1 |
| List valid StationInfo.kind values | DD-03 | 2 |
| Reduce catch-up interval to 15 min | DD-05 | 4 |
| Add user management endpoints (list, deactivate) to v2.0 scope | DD-06 | 5 |
| Fix deployment step ordering (start before import) | DD-07 | 5 |
| Change Prefect test dependency to service_healthy | DD-08 | 5 |

---

## Execution order

Batch 1 (schema gaps) → Fix 3.1 (exceedance probability) → Batch 2 (protocols)
→ remainder of Batch 3 (logic) → Batch 4 (specs) → Batch 5 (improvements)

**Cross-batch dependency**: Fix 2.2f (in Batch 2) depends on Fix 3.1 (in
Batch 3) because it uses `FloodThreshold.exceedance_probability` and
`AlertConfig.default_exceedance`, both introduced by Fix 3.1. Apply Fix 3.1
before starting Batch 2.

Batches 1-3 are blocking — they fix issues that would cause implementation
failures or wrong behavior. Batches 4-5 improve clarity and completeness.

After all batches: run `/review` on each changed design doc.

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
| 1 | 2026-03-07 | plan-reviewer, design-reviewer, data-eng, review-docs | 10 | 16 | fixes-needed |
| 2 | 2026-03-07 | plan-reviewer, design-reviewer, data-eng, review-docs | 8 | 14 | fixes-needed |
| 3 | 2026-03-07 | plan-reviewer, design-reviewer, review-docs | 4 | 8 | fixes-needed |
| 4 | 2026-03-07 | plan-reviewer, design-reviewer | 0 | 1 | fixes-needed |
| 5 | 2026-03-07 | design-reviewer, plan-reviewer, data-eng, review-docs | 8 | 16 | fixes-needed |
| 6 | 2026-03-07 | design-reviewer, plan-reviewer, data-eng, review-docs | 6 | 10 | fixes-needed |
| 7 | 2026-03-07 | design-reviewer, plan-reviewer, data-eng, review-docs | 1 | 8 | fixes-needed |
| 8 | 2026-03-07 | design-reviewer, plan-reviewer, data-eng+docs | 1 | 11 | fixes-needed |
| 9 | 2026-03-07 | design+plan+data-eng+docs (combined) | 0 | 5 | user-confirmed |
