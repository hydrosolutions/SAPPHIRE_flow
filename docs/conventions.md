# Project Conventions

Patterns and conventions specific to SAPPHIRE Flow that complement the
coding style rules in `CLAUDE.md`.

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
GET    /api/v1/stations
GET    /api/v1/stations/{id}
GET    /api/v1/stations/{id}/observations
GET    /api/v1/stations/{id}/forecasts
POST   /api/v1/forecasts/{id}/adjust
PATCH  /api/v1/forecasts/{id}/status
POST   /api/v1/alerts/{id}/acknowledge
POST   /api/v1/flows/ingest/trigger
GET    /api/v1/health
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

| Canonical name | Unit | SMN shortname |
|---------------|------|---------------|
| `precipitation` | mm | `rre150h0` |
| `temperature` | °C | `tre200h0` |
| `humidity` | % | `ure200h0` |
| `radiation` | W/m² | `gre000h0` |
| `wind_speed` | m/s | `fkl010h0` |
| `snow_depth` | cm | `htoauths` |
| `reference_et` | mm/h | `erefaoh0` |
| `swe` | mm | (if available) |

Each adapter maps its source-specific parameter names to these canonical
names. The `parameters` table stores the canonical names.

### Environment variables

- Secrets: `DB_ADMIN_PASSWORD`, `SECRET_KEY`
- API keys: `SAPPHIRE_DG_API_KEY`, `IEASYHYDRO_API_KEY`
- Connection strings: `DATABASE_URL` (via PgBouncer), `DATABASE_URL_DIRECT` (admin/migrations)

### Prefect flows and tasks

- Flow functions: `verb_noun` — `ingest_weather`, `run_forecasts`, `check_flood_alerts`
- Task functions: `verb_noun` — `fetch_weather_forecasts`, `run_model_forecasts`
- Deployment names: kebab-case — `ingest-weather`, `run-forecasts`

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
or `WeatherReanalysisSource`). `WeatherReanalysisSource` is retained for v1 (Nepal)
but not implemented in v0 — training uses station observations. Config loading
resolves `${VAR}` references from `os.environ` at startup; unresolved references
raise immediately.

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

## Error handling at adapter boundaries

### Retry + circuit breaker

```python
@task(retries=3, retry_delay_seconds=[60, 300, 900])
def fetch_weather_forecasts(adapter, ...):
    return adapter.fetch_forecasts(...)
```

- Prefect `@task` handles retry with exponential backoff.
- Circuit breaker at adapter level: after 5 consecutive failures, pause 30 min.
- Stale data beyond `max_cache_age_hours` is flagged but still used for forecasting.

### Custom exceptions

| Exception | Meaning | Handling |
|-----------|---------|----------|
| `InsufficientDataError` | Not enough input data | Try fallback model |
| `SanityCheckFailure` | Model output implausible | Try fallback model |
| `ModelLoadError` | Failed to load model artifact | Try fallback model |
| `PartitionMissingError` | DB partition doesn't exist | Write to dead letter queue, alert ops |

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
- **Method**: per parameter (precipitation: sum, temperature: mean, water level: mean).

---

## Database connection patterns

- **Runtime traffic**: through PgBouncer (`DATABASE_URL`, port 6432, transaction pooling).
- **Migrations**: direct connection (`DATABASE_URL_DIRECT`), bypasses PgBouncer.
- **Prefect server**: direct connection (manages own pool).

### Service users (least privilege)

| User | Permissions |
|------|-------------|
| `sapphire_api` | SELECT all (incl. weather_forecasts, dead_letter_queue, station_weather_sources); INSERT/UPDATE on forecast_adjustments, bulletins, alerts, forecasts (status+version), access_tokens, users, refresh_tokens; UPDATE `last_used_at` on access_tokens (API middleware); INSERT only on audit_log (append-only) |
| `sapphire_worker` | SELECT on stations, station_weather_sources, station_groups, station_group_members, rating_curves; SELECT/INSERT/UPDATE on observations, forecasts, forecast_values, alerts, skill_scores, weather_forecasts, model_artifacts, dead_letter_queue; SELECT/INSERT on hindcast_forecasts, hindcast_values, pipeline_health |
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
Manual adjustments are append-only (each creates an immutable
`forecast_adjustments` row).

---

## Partitioning

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
- **Notification retry**: sweep every 5 min for alerts with `notified_at IS NULL`.

---

## Forecast status workflow

```
raw --> reviewed --> published
```

- **raw**: Model output, no human interaction yet.
- **reviewed**: Forecaster has selected preferred model per station (and optionally adjusted values).
- **published**: Visible in public API and bulletins.

Transitions enforced server-side with optimistic locking.

---

## Status and enum master list

All status/enum columns store TEXT matching the Python enum `.value` (lowercase).

| Column / Type | Values | Terminal states |
|---------------|--------|-----------------|
| `observations.qc_status` / `QcStatus` | `raw`, `qc_passed`, `qc_failed`, `qc_suspect` | `qc_passed`, `qc_failed` |
| `forecasts.status` / `ForecastStatus` | `raw`, `reviewed`, `published` | `published` |
| `forecasts.representation` / `EnsembleRepresentation` | `members`, `quantiles` | — |
| `forecasts.warm_up_source` / `WarmUpSource` | `fresh`, `snapshot`, `cold_start` | — |
| `alerts.status` / `AlertStatus` | `raised`, `acknowledged`, `resolved` | `resolved` |
| `alerts.source` / `AlertSource` | `forecast`, `observation`, `pipeline` | — |
| `models.artifact_scope` / `ArtifactScope` | `station`, `group` | — |
| `model_artifacts.status` / `ModelArtifactStatus` | `training`, `pending_approval`, `active`, `superseded`, `rejected` | `superseded`, `rejected` |
| `hindcast_forecasts.forcing_type` / `ForcingType` | `nwp_archive`, `reanalysis` | — |
| `skill_scores.skill_source` / `SkillSource` | `hindcast_nwp_archive`, `hindcast_reanalysis`, `operational`, `transfer_validation` | — |
| `skill_scores.flow_regime` / `FlowRegime` | `low`, `high`, `flood` | — |
| `station_weather_sources.extraction_type` / `SpatialRepresentation` | `point`, `basin_average`, `elevation_band`, `gridded` | — |
| `station_thresholds.source` / `ThresholdSource` | `authority`, `inferred` | — |
| `stations.regulation_type` / `RegulationType` | `unregulated`, `reservoir`, `irrigation_diversion`, `run_of_river_hydro` | — |
| `stations.station_status` / `StationStatus` | `onboarding`, `operational`, `suspended`, `decommissioned` | `decommissioned` |
| `stations.station_kind` / `StationKind` | `weather`, `river` | — |
| `pipeline_health.status` / `PipelineHealthStatus` | `ok`, `warning`, `critical` | — |
| `pipeline_health.check_type` / `PipelineCheckType` | `nwp_delivery`, `observation_freshness`, `forecast_freshness`, `flow_run_health`, `disk_usage`, `backup_freshness`, `backup_restore_test` | — |
| `dead_letter_queue.resolution` / `DlqResolution` | `replayed`, `discarded` | `replayed`, `discarded` |
| `users.role` / `UserRole` | `org_admin`, `it_admin`, `model_admin`, `forecaster` | — |
| `audit_log.event_type` / `AuditEventType` | `login`, `logout`, `login_failed`, `user_created`, `user_deactivated`, `api_key_created`, `api_key_revoked`, `api_key_request`, `forecast_status_change`, `forecast_adjusted`, `model_promoted`, `model_rejected`, `station_status_change` | — |
| `audit_log.actor_type` / `AuditActorType` | `user`, `api_key`, `system` | — |
| `forecast_adjustments` adjustment_type / `AdjustmentType` | `shift`, `scale`, `cap`, `floor` | — |
| deployment config calendar / `Calendar` | `gregorian`, `bikram_sambat` | — |
| notification channel / `NotificationChannel` | `email`, `sms`, `webhook` | — |
