# Project Conventions

Patterns and conventions specific to SAPPHIRE Flow that complement the
coding style rules in `CLAUDE.md`.

> **v0 note**: This document describes the full v1 system. For v0, `v0-scope.md`
> overrides where it differs — notably: no PgBouncer (direct connections only),
> no table partitioning, no DLQ, no auth/RBAC, single Prefect work pool. When
> conventions.md and v0-scope.md conflict, v0-scope.md wins for v0 implementation.

---

## Naming

### Python

| Element | Convention | Example |
|---------|-----------|---------|
| Modules | `snake_case.py` | `observation_store.py` |
| Classes | `PascalCase` | `ForecastEnsemble` |
| Adapter classes | `{Name}Adapter` | `MeteoSwissAdapter`, `HydroScraperAdapter` |
| Functions/methods | `snake_case` | `prepare_model_inputs()` |
| Enums | `PascalCase.UPPER_CASE` | `StationKind.WEATHER` |
| Constants | `UPPER_CASE` | `DEFAULT_POOL_SIZE` |

### Database

| Element | Convention | Example |
|---------|-----------|---------|
| Tables | `snake_case`, plural | `stations`, `forecast_values` |
| Columns | `snake_case` | `station_id`, `issued_at` |
| Junction tables | `{parent}_{child}` plural | `bulletin_forecasts` |
| Enum columns | store as TEXT matching Python enum `.value` | `"weather"`, `"danger"` |
| Timestamps | `TIMESTAMPTZ`, always UTC | `created_at`, `updated_at` |
| Primary keys | `id` (UUID), except audit_log and pipeline_health (BIGSERIAL) and models (TEXT) | |
| Human-readable refs | `code` column, TEXT UNIQUE | `stations.code = "ABC-001"` |

### API routes

Pattern: `/api/v1/{resource}` with nested sub-resources.

```
# Stations & data
GET    /api/v1/stations                         # list stations (paginated)
GET    /api/v1/stations/{id}                    # station detail
GET    /api/v1/stations/{id}/observations       # observations for station
GET    /api/v1/stations/{id}/forecasts          # forecasts for station

# Forecasts
GET    /api/v1/forecasts/{id}                   # forecast detail with ensemble values
POST   /api/v1/forecasts/{id}/adjust            # apply adjustment (v1 — requires Flow 3)
PATCH  /api/v1/forecasts/{id}/status            # transition status (v1 — requires Flow 3)

# Alerts
GET    /api/v1/alerts                           # list alerts (filterable by status, source)
POST   /api/v1/alerts/{id}/acknowledge          # acknowledge an alert

# Operations
POST   /api/v1/flows/{flow}/trigger             # manually trigger a flow run
GET    /api/v1/health                           # health check (aggregate status)
GET    /api/v1/health/detail                    # detailed component status (v1: requires auth)

# Auth (v1)
POST   /api/v1/users
GET    /api/v1/users
PATCH  /api/v1/users/{id}
POST   /api/v1/access-tokens
GET    /api/v1/access-tokens
DELETE /api/v1/access-tokens/{id}
POST   /api/v1/access-tokens/{id}/regenerate
```

- Version always in path: `/api/v1/`
- Pagination: `limit` + `after` (cursor-based)
- Filtering: query params (`?level=danger`, `?source=forecast`)
- Temporal aggregation: `?aggregate=pentadal|dekadal`
- Format: `?format=csv|json` (default JSON)

### Parameter names

River station parameters use canonical names:
- `water_level` (m, reference datum documented per station in metadata)
- `discharge` (m³/s)

Weather station parameters use canonical names mapped from adapter-specific
names at ingest:

| Canonical name | Unit | SMN shortname (informational) | NWP shortname (ICON-CH2-EPS) |
|---------------|------|-------------------------------|------------------------------|
| `precipitation` | mm | `rre150h0` | `tp` |
| `temperature` | °C | `tre200h0` | `t_2m` |
| `humidity` | % | `ure200h0` | `relhum_2m` |
| `radiation` | W/m² | `gre000h0` | deferred |
| `wind_speed` | m/s | `fkl010h0` | `u_10m` / `v_10m` |
| `snow_depth` | cm | `htoauths` | `sd` |
| `snowmelt` | mm | — | — |
| `reference_et` | mm/h | `erefaoh0` | — |
| `swe` | mm | (if available) | — |

Each adapter maps its source-specific parameter names to these canonical
names. The `parameters` table stores the canonical names.

**Extensibility:** The list above is the v0 seed data. Deployments can register additional
parameters via `[[parameters]]` in the deployment config TOML (loaded in Flow 0 step 0.6).
Future domains include water quality (`water_temperature`, `dissolved_oxygen`, `turbidity`),
groundwater (`groundwater_level`), and soil (`soil_moisture`). See `architecture-context.md`
§`parameters` table for the extensibility model and `ParameterDomain` enum.

### Environment variables

- Secrets: `DB_ADMIN_PASSWORD`, `SECRET_KEY`
- API keys: `SAPPHIRE_DG_API_KEY`, `IEASYHYDRO_API_KEY`
- Connection strings: `DATABASE_URL` (via PgBouncer) *(v1)*, `DATABASE_URL_DIRECT` (admin/migrations). v0: `DATABASE_URL` connects directly to PostgreSQL (no PgBouncer).
- Log overrides: `SAPPHIRE_LOG_<MODULE>=<LEVEL>`. Single `_` maps to `.` (package separator); double `__` maps to literal `_` (e.g. `SAPPHIRE_LOG_ADAPTERS_FORECAST__INTERFACE=DEBUG`).
- Dev-only reference data: `CAMELS_CH_HOST_DIR` — host path bind-mounted read-only into the `prefect-worker` container's `/data/raw` via `docker-compose.dev.yml`. Target must contain an uppercase `CAMELS_CH/` subdirectory. See `.env.example` for the authoritative doc block.

### Network identifiers

Lowercase, underscore-separated labels identifying the data source network:
`"bafu"`, `"uk_ea"`, `"usgs"`, `"bfg"`, `"dhm"`.

Set once at import; used as part of the `(network, code)` composite unique
constraint on `stations` and `basins`.

### Prefect flows and tasks

- Flow functions: `verb_noun` — `run_forecast_cycle`, `ingest_observations`, `compute_skills_flow`
- Task functions: `verb_noun` — `fetch_weather_forecasts`, `run_model_forecasts`, `compute_skills_task`
- Deployment names: kebab-case — `run-forecast-cycle`, `ingest-observations`, `compute-skills`

When the dual-interface pattern is used (see `orchestration.md` §Flow composition, pattern 4), append `_task` and `_flow` suffixes to the `verb_noun` base name (e.g., `compute_skills_task`, `compute_skills_flow`). The Prefect deployment `name=` in the `@flow` decorator retains the unsuffixed kebab-case form (`compute-skills`).

### Log events

- Event names: `{entity}.{action}` — `nwp.fetch_completed`, `observation.qc_failed`, `forecast.stored`
- See [`docs/standards/logging.md`](standards/logging.md) § Event naming for pattern rules and examples.

---

## Adapter registration

Adapters are selected via `config.toml`:

```toml
# v0 (Switzerland)
[adapters.weather_forecast]
type = "meteoswiss_nwp"        # maps to MeteoSwissNwpAdapter
archive = true                 # permanently store all NWP data
max_cache_age_hours = 12

[adapters.weather_stations]
type = "meteoswiss_smn"        # maps to MeteoSwissSmnAdapter
max_cache_age_hours = 24

[adapters.river_stations]
type = "hydro_scraper"         # maps to HydroScraperAdapter
max_cache_age_hours = 24
```

Each adapter class lives in `adapters/{type}.py` and satisfies the
corresponding Protocol (`WeatherForecastSource`, `StationDataSource`,
or `WeatherReanalysisSource`). `WeatherReanalysisSource` is implemented in v0
by several `PerSourceStoreReader`/`StoreBackedReanalysisSource`/
`HybridForcingSource` adapters (Plan 072/115b4) — training/hindcast/live
past-dynamic forcing reads basin-averaged gridded data via the `hybrid`
default (self-derived MeteoSwiss products; CAMELS-CH is retained only as a
validation reference, see `historical_forcing.source` below). Config loading
resolves `${VAR}` references from `os.environ` at startup; unresolved references
raise immediately.

The `foreign_forecast` adapter role is defined via the `ForeignForecastSource` Protocol
(`protocols/adapters.py`). Implementation deferred — no adapter class exists in v0.

---

## Model discovery

Models register via Python entry points in their package's `pyproject.toml`:

```toml
[project.entry-points."sapphire_flow.models"]
lstm_daily = "hf_forecasting.lstm:LSTMDailyModel"
hbv = "hf_forecasting.hbv:HBVModel"
```

At startup, `ModelRegistry` discovers all installed models:

```python
from importlib.metadata import entry_points
models = entry_points(group="sapphire_flow.models")
```

The entry point name (e.g. `"lstm_daily"`) is the model's TEXT primary key
in the `models` table and must be stable across versions.

Each model class declares an `artifact_scope` class attribute (`ArtifactScope.STATION` or `ArtifactScope.GROUP`). `ModelRegistry` reads this at startup and stores it in the `models.artifact_scope` column. Station-scoped models (conceptual) are trained per station; group-scoped models (ML) are trained on a station group and produce one shared artifact.

---

## Time range convention: half-open intervals

All store Protocol methods that accept `start` and `end` parameters use **half-open intervals `[start, end)`**:

- SQL: `WHERE timestamp >= start AND timestamp < end`
- Python fakes: `start <= x.timestamp < end`

This convention:
- Prevents double-counting at boundaries when adjacent windows abut
- Matches PostgreSQL's default `tstzrange` semantics
- Composes cleanly: `[t0, t1)` + `[t1, t2)` covers `[t0, t2)` with no overlap

The convention applies to all `fetch_*` methods with time range parameters and to `mark_stale`.

---

## Error handling at adapter boundaries

### Retry + circuit breaker

```python
@task(retries=3, retry_delay_seconds=[60, 300, 900])
def fetch_weather_forecasts(adapter, ...):
    return adapter.fetch_forecasts(...)
```

- Prefect `@task` handles retry with exponential backoff.
- Circuit breaker in the calling flow/task: after 5 consecutive failures, pause 30 min. Circuit breakers track state across invocations (consecutive failure count, pause timer); adapters are stateless data-fetching components, so the breaker logic belongs in the orchestration layer.
- Stale data beyond `max_cache_age_hours` is flagged but still used for forecasting.

### Custom exceptions

All exceptions inherit from `SapphireError`. Authoritative class definitions in
`types-and-protocols.md` § Exception hierarchy. Module: `exceptions.py`.

| Exception | Meaning | Handling |
|-----------|---------|----------|
| `SapphireError` | Base for all domain errors | — |
| `InsufficientDataError` | Not enough input data | Try fallback model |
| `SanityCheckFailure` | Model output implausible | Try fallback model |
| `ModelLoadError` | Failed to deserialize or load a model artifact | Try fallback model |
| `ModelOutputError` | Model ran but produced zero convertible ensembles | Try fallback model |
| `ConflictError` | Optimistic locking conflict | Return 409 Conflict |
| `AdapterError` | External source error/timeout | Retry, then fallback |
| `ConfigurationError` | Invalid/missing config | Fail fast at startup |
| `ModelSmokeTestError` | Model raised exception during smoke test | Flow 13: unit outcome = `FAILED_SMOKE_TEST`; continue other units |
| `ArtifactIntegrityError` | SHA-256 hash mismatch on fetched artifact bytes | Do not deserialize; task failure |
| `ExtractionError` | Preprocessing/extraction failure (GridExtractor) | Log, skip station or fail cycle depending on scope |
| `StoreError` | Store data retrieval failure (archive not found, corrupt data) | Log, raise to caller |
| `PartitionMissingError` | DB partition doesn't exist | Write to dead letter queue, alert ops. **v0: not needed (no partitioning, see v0-scope.md § A1)** |

> **`InsufficientDataError` — Flow 13 exception**: In model onboarding (and other multi-phase initialization flows), there is no fallback model. Exception mapping is phase-based, not type-based: `InsufficientDataError` before training maps to `SKIPPED_NO_DATA`; once training begins, any `SapphireError` subclass maps to the `FAILED_*` variant for the current phase (e.g., `FAILED_TRAINING`, `FAILED_HINDCAST`, `FAILED_SKILL`, `FAILED_ASSIGNMENT`). True unexpected exceptions (`TypeError`, `AttributeError`) propagate to Prefect as task-level failures per the standard rule.

> **`ConfigurationError` — Flow 13 exception**: `ConfigurationError` is also raised at flow invocation time when required scope parameters are missing (e.g., `group_ids=None` for a group-scoped model with no existing assignments). The "fail fast" principle applies: reject the invocation immediately rather than proceeding with an empty scope.

### Flow-level strategy

Expected failures (data/model issues) catch and try fallback model.
Unexpected failures (TypeError, AttributeError) propagate to Prefect
for logging and notification.

---

## ID conventions

| Context | ID type | Example |
|---------|---------|---------|
| Most PKs | UUID | basins, stations, forecasts, alerts |
| Models | TEXT (entry point name) | `"lstm_daily"` |
| Audit log | BIGSERIAL | high-volume appends |
| Station/basin human ref | `code` TEXT UNIQUE | `"ABC-001"`, `"BASIN-01"` |

- API accepts both UUID and code for station lookups.
- Internal services use UUID (from `StationConfig`).
- Adapter boundaries use string codes.
- **Polars compatibility**: Polars has no native UUID dtype. UUID columns read from
  PostgreSQL arrive as `Utf8` (string) columns when using ConnectorX (`read_database_uri`).
  Domain code uses `StationId = NewType("StationId", UUID)` etc.; conversion between
  `UUID` and string happens at the store boundary. See `docs/spec/types-and-protocols.md`
  for the full ID NewType list.

---

## Timestamps

- **Storage**: always UTC (`TIMESTAMPTZ` in PostgreSQL).
- **Display**: converted at API/dashboard boundary. Default display timezone
  from `default_display_timezone` in deployment config. Per-station IANA timezones
  (from station metadata) are used for daily aggregation day boundaries —
  these coincide in single-timezone deployments (Nepal, Switzerland).
- **Python**: timezone-aware `datetime` objects.
- **API format**: ISO 8601 with timezone — `"2026-07-01T12:00:00Z"`.
- **No `datetime.now()`** in business logic — inject a clock (see CLAUDE.md).

### Temporal aggregation periods

- **Pentadal**: days 1-5, 6-10, 11-15, 16-20, 21-25, 26-end (last pentad is 3-6 days).
- **Dekadal**: days 1-10, 11-20, 21-end.
- **Method**: per parameter — defined in the `parameters` table (`aggregation_method` column). See `architecture-context.md` for the table definition.

---

## Database connection patterns

> **v1-only** (v0-scope.md §A3): PgBouncer is not used in v0. v0: direct PostgreSQL connection for all traffic.

- **Runtime traffic**: through PgBouncer (`DATABASE_URL`, port 6432, transaction pooling).
- **Migrations**: direct connection (`DATABASE_URL_DIRECT`), bypasses PgBouncer.
- **Prefect server**: direct connection (manages own pool).

### Service users (least privilege)

| User | Permissions |
|------|-------------|
| `sapphire_api` | SELECT all (incl. parameters, weather_forecasts, dead_letter_queue *(v1)*, station_weather_sources); INSERT/UPDATE on forecast_adjustments *(v1)*, bulletins *(v1)*, alerts, forecasts (status+version), access_tokens *(v1)*, users *(v1)*, refresh_tokens *(v1)*; UPDATE `last_used_at` on access_tokens (API middleware); INSERT only on audit_log *(v1)* (append-only) |
| `sapphire_worker` | SELECT on stations, parameters, station_weather_sources, station_groups, station_group_members, rating_curves; SELECT/INSERT/UPDATE on observations, forecasts, forecast_values, alerts, skill_scores, weather_forecasts, model_artifacts, dead_letter_queue *(v1)*; SELECT/INSERT on hindcast_forecasts, hindcast_values, pipeline_health, observation_versions *(v1)* |
| `sapphire_prefect` | Full access to `prefect` database only |

---

## Concurrency control

Forecast status transitions use optimistic locking:

```sql
UPDATE forecasts
SET status = $1, version = version + 1
WHERE id = $2 AND version = $3;
```

Returns 409 Conflict to the API caller if the version doesn't match.
Manual adjustments *(v1)* are append-only (each creates an immutable
`forecast_adjustments` row).

---

## Partitioning

> **v1-only** (v0-scope.md §A1): Table partitioning is not used in v0. All tables are unpartitioned.

| Table | Strategy | Key |
|-------|----------|-----|
| `observations` | Yearly range | `timestamp` |
| `forecast_values` | Monthly range | `issued_at` (denormalized from `forecasts`) |
| `hindcast_values` | Monthly range | `hindcast_step` (denormalized from `hindcast_forecasts`) |
| `weather_forecasts` | Monthly range | `cycle_time` |

Managed by `pg_partman` with premake=2 (observations) / premake=3
(forecast_values). Data landing in a missing partition goes to the
dead letter queue and is recovered when the partition is created.

---

## Alert lifecycle

```
raised --> acknowledged --> resolved
```

- **Forecast alerts**: exceedance probability thresholds (configurable per level).
- **Observation alerts**: measured value exceeds threshold.
- **Auto-resolution**: newer data no longer exceeds threshold.
- **Deduplication**: partial unique indexes prevent duplicates on flow reruns.
- **Notification retry** *(v1)*: sweep every 5 min for alerts with `notified_at IS NULL`.

---

## Forecast status workflow

```
raw --> reviewed --> published
```

- **raw**: Model output, no human interaction yet.
- **reviewed**: Forecaster has selected preferred model per station (and optionally adjusted values).
- **published**: Visible in public API and bulletins.

Transitions enforced server-side with optimistic locking.

> **v0 note**: v0 uses only `raw` status -- Flow 3 (forecast review/publish) is deferred to v1.

---

## Status and enum master list

All status/enum columns store TEXT matching the Python enum `.value` (lowercase).

| Column / Type | Values | Terminal states | Scope |
|---------------|--------|-----------------|-------|
| `observations.qc_status` / `QcStatus` | `raw`, `qc_passed`, `qc_failed`, `qc_suspect`, `missing` | `qc_passed`, `qc_failed`, `missing` | v0+v1 |
| `forecasts.status` / `ForecastStatus` | `raw`, `reviewed`, `published` | `published` | v0+v1 |
| `forecasts.representation` / `EnsembleRepresentation` | `members`, `quantiles` | — | v0+v1 |
| `forecasts.warm_up_source` / `WarmUpSource` | `fresh`, `snapshot`, `cold_start` | — | v0+v1 |
| `rating_curves.interpolation` / `InterpolationMethod` | `linear`, `log_linear` | — | v1 |
| `alerts.status` / `AlertStatus` | `raised`, `acknowledged`, `resolved` | `resolved` | v0+v1 |
| `alerts.source` / `AlertSource` | `forecast`, `observation`, `pipeline` | — | v0+v1 |
| `models.artifact_scope` / `ArtifactScope` | `station`, `group` | — | v0+v1 |
| API/dashboard `model_tier` / `ModelTier` | `skill`, `fallback` | — | v0+v1 |
| `model_artifacts.status` / `ModelArtifactStatus` | `training`, `pending_approval` *(v1 -- v0 auto-promotes, §A7)*, `active`, `superseded`, `rejected` *(v1)* | `superseded`, `rejected` | v0+v1 |
| `hindcast_forecasts.forcing_type` / `ForcingType` | `nwp_archive`, `reanalysis` | — | v0+v1 |
| `skill_scores.skill_source` / `SkillSource` | `hindcast_nwp_archive`, `hindcast_reanalysis`, `operational`, `transfer_validation` | — | v0+v1 |
| `skill_scores.flow_regime` / `FlowRegime` | `low`, `high`, `flood` | — | v0+v1 |
| `station_weather_sources.extraction_type` / `SpatialRepresentation` | `point`, `basin_average`, `elevation_band`, `gridded` | — | v0+v1 |
| `station_weather_sources.role` / `WeatherSourceRole` | `forecast`, `reanalysis` | — | v0+v1 |
| `station_thresholds.source` / `ThresholdSource` | `authority`, `inferred` | — | v0+v1 |
| `DangerLevelDefinition.direction` / `ThresholdDirection` | `above`, `below` | — | v0+v1 |
| `stations.regulation_type` / `RegulationType` | `unregulated`, `reservoir`, `irrigation_diversion`, `run_of_river_hydro` | — | v0+v1 |
| `stations.station_status` / `StationStatus` | `onboarding`, `operational`, `suspended`, `decommissioned` | `decommissioned` | v0+v1 |
| `parameters.parameter_domain` / `ParameterDomain` | `river`, `weather`, `water_quality`, `groundwater`, `soil` | — | v0+v1 |
| `parameters.aggregation_method` / `AggregationMethod` | `sum`, `mean` | — | v0+v1 |
| `stations.station_kind` / `StationKind` | `weather`, `river`, `lake` | — | v0+v1 |
| `pipeline_health.status` / `PipelineHealthStatus` | `ok`, `warning`, `critical` | — | v0+v1 |
| `pipeline_health.check_type` / `PipelineCheckType` (TEXT, not check-constrained — `types/enums.py::PipelineCheckType` is authoritative) | `nwp_delivery`, `observation_freshness`, `forecast_freshness`, `flow_run_health`, `disk_usage`, `backup_freshness`, `backup_restore_test`, `forecast_station_dark`, `alert_suppressed_fallback`, `priority_migration_audit`, `climatology_threshold_review`, `bafu_forecast_freshness`, `weather_history_ingest` *(Plan 115b4 §6A)* | — | v0+v1 |
| `dead_letter_queue.resolution` / `DlqResolution` | `replayed`, `discarded` (NULL = unresolved) | `replayed`, `discarded` | **v1** |
| `users.role` / `UserRole` | `org_admin`, `it_admin`, `model_admin`, `forecaster` | — | **v1** |
| `observations.source` / `ObservationSource` | `measured`, `rating_curve_derived`, `manual_import`, `component_derived` *(v1 only)* | — | v0+v1 |
| `audit_log.event_type` / `AuditEventType` | `login`, `logout`, `login_failed`, `password_changed`, `user_created`, `user_deactivated`, `api_key_created`, `api_key_revoked`, `api_key_request`, `forecast_status_change`, `forecast_adjusted`, `model_promoted`, `model_rejected`, `station_status_change`, `observation_reprocessed` | — | **v1** |
| `audit_log.actor_type` / `AuditActorType` | `user`, `api_key`, `system` | — | **v1** |
| `forecast_adjustments` adjustment_type / `AdjustmentType` | `shift`, `scale`, `cap`, `floor` | — | **v1** |
| deployment config calendar / `Calendar` | `gregorian`, `bikram_sambat` | — | v0+v1 |
| notification channel / `NotificationChannel` | `email`, `sms`, `webhook` | — | **v1** |
| `stations.ownership` / `StationOwnership` | `own`, `foreign` | `foreign` (cannot transition to own) | v0+v1 |
| `stations.gauging_status` / `GaugingStatus` | `gauged`, `ungauged`, `calculated` | — | v0+v1 |
| `ForeignForecastStatus` | `published` | `published` | v0+v1 |
| `FlowRunState` | `pending`, `running`, `completed`, `failed`, `crashed`, `cancelling`, `cancelled` | — | v0+v1 |
| `ForcingProvenance` | `nwp_direct`, `observed`, `interpolated`, `gap_filled_climatology`, `gap_filled_persistence`, `reanalysis`, `derived`, `unknown` | — | v0+v1 |
| `historical_forcing.source` / `ForcingSource` (TEXT, not check-constrained) | `meteoswiss_rhiresd` *(definitive precip, monthly publication — Plan 115b1)*, `meteoswiss_rprelimd` *(preliminary precip, live tail)*, `meteoswiss_tabsd`, `meteoswiss_tmind`, `meteoswiss_tmaxd`, `meteoswiss_sreld` *(relative sunshine duration — Plan 115b1)*, `camels-ch`, `nwp_archive` *(reserved, unused in v0b)* | — | v0+v1 |
| `model_assignments.status` / `ModelAssignmentStatus` | `active`, `inactive` | `inactive` | v0+v1 |
| `OnboardingOutcome` (in-memory only) | `promoted`, `gate_rejected`, `skipped_compat`, `skipped_no_data`, `skipped_insufficient_eval`, `failed_smoke_test`, `failed_training`, `failed_hindcast`, `failed_skill`, `failed_assignment` | all terminal | v0+v1 |
| Forecast QC rule IDs (string, not enum) | `negative_value`, `range_check`, `flat_ensemble`, `ensemble_spread`, `climatology_outlier`, `temporal_consistency`, `quantile_crossing` | — | v0+v1 |

---

## Model assignment priority

Lower integer = higher priority within the model's tier — the model is tried first
in the forecast cycle's PRIMARY first-success chain and drives alert decisions
when all models succeed. The intended hierarchy is **skill models > fallbacks**,
but Plan 100 makes that hierarchy categorical: fallback membership comes from
`ModelTier`/fallback model ID, not from the mutable DB `priority` value.

**Priorities are config-driven (Plan 089).** Onboarding no longer hardcodes a
priority. It resolves each model's priority from the `[model_priorities]` map in
`config.toml` (`DeploymentConfig.model_priorities`), keyed by `model_id`. A model
absent from the map gets `DEFAULT_PRIORITY = 50` — a neutral run-order value for
non-fallback models. Re-onboarding is idempotent and re-applies the current config
priorities to existing assignments (upsert).

| Priority | Model type | Semantics |
|----------|-----------|-----------|
| 10–30 | Skill models (NWP regression, NWP rainfall-runoff, linear regression) | Weather-driven / conceptual; alert-selection primary |
| 50 | `DEFAULT_PRIORITY` (unlisted models) | Neutral tier — outranks fallbacks, below tuned skill models |
| 90 | `PersistenceFallbackModel` | Guaranteed last-resort; excluded from combination |
| 100 | `ClimatologyFallbackModel` | Absolute last-resort; excluded from combination |

- Config: `[model_priorities]` in `config.toml`, parsed at the Pydantic boundary
  into `DeploymentConfig.model_priorities: dict[str, int]`. Operator-tunable.
- DB default: `server_default="0"` on `model_assignments.priority` and `group_model_assignments.priority`.
- Alert strategy dispatches via `min(priority)` — lowest integer wins.
- `PersistenceFallbackModel` and `ClimatologyFallbackModel` are the fallback tier regardless of the stored assignment priority. API/dashboard surfaces expose this as `model_tier = "fallback"`; all other current models render as `model_tier = "skill"`.
- The API/dashboard derives a `no_floor` station badge from active `climatology_fallback` artifact presence. This is not a `StationStatus`, station column, or migration.
- Priorities >= 90 remain reserved for fallback assignment order. Multi-model combination excludes the categorical fallback tier (`ModelTier.FALLBACK`), not rows merely because their DB priority is >= 90.
- v1 may add a separate `alert_priority` column to decouple fallback order from alert selection (see `architecture-context.md` §I3).

---

## Schema constraint checklist

Apply this checklist whenever adding or modifying a database table:

- **Every table with a natural key must have a unique constraint.** If rows in the
  table represent a unique real-world event or entity identifiable by a combination
  of domain columns, declare a unique index on those columns. A primary-key UUID
  alone does not prevent logical duplicates.
- **Every FK column used in WHERE or JOIN must have an index.** A foreign key column
  without an index causes a sequential scan on the referencing table. For header+values
  pairs (e.g. `forecast_values.forecast_id`, `hindcast_values.hindcast_forecast_id`),
  add a non-unique index on the FK column after the table definition.
- **When adding a constraint to a header table, check the sibling values table.**
  Header+values pairs are always modified together. If the header table gets a new
  unique index, check whether the values table's FK column already has a covering
  index; if not, add one in the same migration.
- **Use `IF NOT EXISTS` / `IF EXISTS` on all index operations in migrations.** Every
  `op.create_index` in `upgrade()` should pass `if_not_exists=True` and every
  `op.drop_index` in `downgrade()` should pass `if_exists=True` so migrations are
  idempotent and safe to run on a DB that has already been partially migrated.

---

## Invariants

- **Foreign stations cannot have model assignments.** `model_assignments` must only reference stations with `ownership='own'`. Foreign stations are display-only and never run through local models. Enforced at application layer; DB trigger deferred to v1.
