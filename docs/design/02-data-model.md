# Data Model

## Core concepts

- **Basin**: A hydrological catchment grouping stations
- **Station**: A physical location with sensors (weather or river gauge), belonging to a basin
- **Parameter**: What is measured (precipitation, temperature, water level, ...)
- **Observation**: A measured value at a station, at a point in time
- **Observation edit**: A hydrologist's correction to an observation, with audit metadata
- **Forecast**: An ensemble of predicted values for a station + parameter, issued at a specific time
- **Forecast type**: Temporal resolution — daily, sub-daily, pentadal (5-day), dekadal (10-day), monthly, seasonal
- **Rating curve**: A versioned stage-discharge relationship for a river gauge
- **Adjustment**: A forecaster's manual edit to a forecast, with audit metadata
- **Bulletin**: A generated report (Excel) from selected + adjusted forecasts
- **Flood threshold**: Warning/danger levels per station, used for priority alerting
- **Alert event**: A persisted record of a flood alert with lifecycle (raised → acknowledged → resolved)
- **Audit log entry**: A security event record (login, admin action, flow trigger)

## PostgreSQL schema (conceptual)

### basins

| Column      | Type        | Notes                          |
|-------------|-------------|--------------------------------|
| id          | UUID        | PK                             |
| code        | TEXT        | Hydromet's basin identifier    |
| name        | TEXT        |                                |
| metadata    | JSONB       | Flexible extra fields          |
| created_at  | TIMESTAMPTZ | DEFAULT now()                  |
| updated_at  | TIMESTAMPTZ | DEFAULT now(), trigger on update  |

### stations

| Column      | Type        | Notes                          |
|-------------|-------------|--------------------------------|
| id          | UUID        | PK                             |
| code        | TEXT        | Hydromet's station identifier, UNIQUE |
| name        | TEXT        |                                |
| basin_id    | UUID        | FK -> basins, nullable         |
| lon         | FLOAT       |                                |
| lat         | FLOAT       |                                |
| elevation_m | FLOAT       | nullable                       |
| kind        | ENUM        | weather / river / virtual                |
| metadata    | JSONB       | Flexible extra fields          |
| created_at  | TIMESTAMPTZ | DEFAULT now()                  |
| updated_at  | TIMESTAMPTZ | DEFAULT now(), trigger on update  |

Virtual stations (`kind = virtual`) represent ungauged forecast sites where runoff is calculated from upstream measurements or area-based methods rather than direct gauge observation. They participate in forecasting and alerting like regular stations but have no direct observations.

### station_model_config

| Column           | Type        | Notes                              |
|------------------|-------------|------------------------------------|
| id               | UUID        | PK                                 |
| station_id       | UUID        | FK -> stations, UNIQUE             |
| model_id         | TEXT        | FK -> models, primary model        |
| model_version    | TEXT        | Expected package version           |
| model_artifact   | TEXT        | Path to model weights/parameters   |
| fallback_model_id| TEXT        | FK -> models, nullable             |
| fallback_artifact| TEXT        | Path to fallback model artifact, nullable |
| updated_by       | UUID        | FK -> users, who last changed this |
| updated_at       | TIMESTAMPTZ | DEFAULT now(), trigger on update   |

One row per station. The `station_id` UNIQUE constraint ensures each station
has exactly one active model configuration. Changes are made via the
API/dashboard — no container restart needed.

**Audit**: Changes to model assignments are logged to the `audit_log` table
with `action = 'model_config_changed'` and the previous/new values in `detail`.

**Operational workflow**:
- **Initial setup**: Developers bulk-import model assignments from TOML via
  `sapphire-flow import-model-config --file models.toml`. This is a one-time
  bootstrap — the TOML file is not read at runtime.
- **Ongoing changes**: Admin or forecaster roles can change a station's model
  assignment via the dashboard or API. Forecasters see model skill scores to
  inform their choice.
- **Model upgrade**: When a new model package version is installed, a startup
  check compares `model_version` against the installed version and warns about
  mismatches. An admin can bulk-update versions via
  `PATCH /api/v1/admin/model-config/bulk`.

**Role permissions**:
- `admin`: full CRUD on model config
- `forecaster`: can change `model_id` and `fallback_model_id` for stations
  they monitor (not artifact paths — those are infrastructure)
- `viewer`: read-only

### parameters

| Column     | Type        | Notes                               |
|------------|-------------|-------------------------------------|
| id         | UUID        | PK                                  |
| name       | TEXT        | e.g. "precipitation", "water_level" |
| unit       | TEXT        | e.g. "mm", "m", "degC"             |
| aggregation_method | TEXT | sum / mean / max / min — how to aggregate over time periods. Default: mean |
| created_at | TIMESTAMPTZ | DEFAULT now()                       |
| updated_at  | TIMESTAMPTZ | DEFAULT now(), trigger on update  |

The `aggregation_method` column specifies how values should be combined when computing pentadal, dekadal, or monthly aggregations. Precipitation uses `sum`, temperature uses `mean`, water level and discharge use `mean`. This prevents incorrect aggregation (e.g. summing temperatures).

### observations

| Column       | Type        | Notes                              |
|--------------|-------------|------------------------------------|
| station_id   | UUID        | FK -> stations                     |
| parameter_id | UUID        | FK -> parameters                   |
| timestamp    | TIMESTAMPTZ |                                    |
| value        | FLOAT       |                                    |
| quality_flag | SMALLINT    | nullable, hydromet-defined         |
| is_edited    | BOOLEAN     | default false                      |

Primary key: (station_id, parameter_id, timestamp)

#### Quality flag values

| Flag | Meaning | Set by |
|------|---------|--------|
| 0    | Unchecked (default) | Ingest |
| 1    | Passed all automated checks | QC service |
| 2    | Suspect — failed range check | QC service |
| 3    | Suspect — failed rate-of-change check | QC service |
| 4    | Suspect — failed spatial consistency check | QC service |
| 9    | Excluded by forecaster | Manual (observation_edits) |

Flags 2-4 are advisory — the observation is still used for forecasting unless
a forecaster explicitly excludes it (flag 9). The QC service sets flags
automatically after each ingest; forecasters review flagged observations
on the dashboard.

**Partitioning**: Partition by year using PostgreSQL declarative partitioning.
Managed by `pg_partman` with `premake = 2` (creates partitions 2 years ahead).
The initial Alembic migration creates partitions for all historical years plus
2 years into the future. pg_partman's maintenance function runs daily via
`pg_cron` or a Prefect maintenance flow to create upcoming partitions. If a
partition does not exist when data arrives, PostgreSQL rejects the INSERT —
this is a data loss scenario. The health endpoint includes a check that
verifies partitions exist for the current year + 1.

**Active partition protection**: The `upsert_observations` and `save_forecast`
store methods catch PostgreSQL's partition-missing error
(`SQLSTATE 23514` / `no partition of relation`) and raise a distinct
`PartitionMissingError`. This error triggers:
1. An immediate critical notification via all configured sinks
2. The observation/forecast data is written to a `dead_letter_queue` table
   (unpartitioned, append-only) to prevent data loss
3. The flow is marked as failed with a clear error message

The dead letter queue is drained automatically once the missing partition is
created (by `pg_partman` maintenance or manual intervention). A startup check
also verifies that the dead letter queue is empty — leftover rows indicate a
previous partition failure that was resolved but not fully recovered.

### observation_edits

| Column        | Type        | Notes                              |
|---------------|-------------|------------------------------------|
| id            | UUID        | PK                                 |
| station_id    | UUID        | FK -> stations                     |
| parameter_id  | UUID        | FK -> parameters                   |
| timestamp     | TIMESTAMPTZ | Which observation was edited       |
| previous_value| FLOAT       | Value before the edit              |
| new_value     | FLOAT       | Value after the edit               |
| edited_by     | UUID        | FK -> users                        |
| edited_at     | TIMESTAMPTZ |                                    |
| reason        | TEXT        | Free-text justification            |
| edit_type     | ENUM        | corrected / excluded               |
| idempotency_key | TEXT    | UNIQUE, nullable — prevents duplicate submissions |

When `edit_type = excluded`: the observation is flagged as "do not use for
forecasting". The value in `observations` is preserved but `quality_flag`
is set to a sentinel indicating exclusion. Forecast input preparation
filters these out.

When `edit_type = corrected`: the observation's `value` is replaced in place,
`is_edited` is set to true, and the original value is preserved in
`previous_value`. The raw original lives in the hydromet's source database.

**Idempotency**: The API layer generates a client-side idempotency key per edit
request. The `observation_edits` table includes an `idempotency_key TEXT UNIQUE`
column (nullable — null for edits created before this feature). Duplicate
submissions (e.g. HTMX double-click) are rejected at the database level.

### flood_thresholds

| Column       | Type        | Notes                              |
|--------------|-------------|------------------------------------|
| station_id   | UUID        | FK -> stations                     |
| parameter_id | UUID        | FK -> parameters                   |
| level        | ENUM        | normal / watch / warning / danger  |
| value        | FLOAT       | Threshold in parameter's unit      |
| unit_note    | TEXT        | nullable, e.g. "m a.s.l." or "m"  |
| valid_from_month | SMALLINT    | nullable, range 1-12             |
| valid_to_month   | SMALLINT    | nullable, range 1-12             |
| updated_at  | TIMESTAMPTZ | DEFAULT now(), trigger on update  |

Composite key: (station_id, parameter_id, level, valid_from_month, valid_to_month)

When both month columns are NULL, the threshold applies year-round. When set,
the threshold applies only during the specified month range (inclusive, wrapping
supported — e.g. `valid_from_month=11, valid_to_month=3` covers Nov–Mar).

**Query logic**: To find active thresholds for a given month M, the query must
handle wrapping explicitly:
```sql
WHERE (valid_from_month IS NULL AND valid_to_month IS NULL)  -- year-round
   OR (valid_from_month <= valid_to_month
       AND M BETWEEN valid_from_month AND valid_to_month)    -- non-wrapping
   OR (valid_from_month > valid_to_month
       AND (M >= valid_from_month OR M <= valid_to_month))   -- wrapping (e.g. Nov-Mar)
```
Seasonal thresholds take precedence over year-round thresholds for the same
(station, parameter, level) when the current month falls within the seasonal range.

Thresholds are typically fetched from the hydromet's API during initial
setup and stored locally. They can also be manually configured.

### alert_events

| Column       | Type        | Notes                              |
|--------------|-------------|------------------------------------|
| id           | UUID        | PK                                 |
| station_id   | UUID        | FK -> stations                     |
| parameter_id | UUID        | FK -> parameters                   |
| forecast_id  | UUID        | FK -> forecasts, nullable (null for observation-triggered alerts) |
| source           | ENUM        | forecast / observation                     |
| level        | ENUM        | watch / warning / danger           |
| lead_time_minutes | INTEGER | At which lead time threshold exceeded |
| forecast_value | FLOAT     | The value that exceeded threshold   |
| threshold_value | FLOAT    | The threshold that was exceeded     |
| raised_at    | TIMESTAMPTZ | When the alert was created         |
| acknowledged_by | UUID     | FK -> users, nullable              |
| acknowledged_at | TIMESTAMPTZ | nullable                        |
| resolved_at  | TIMESTAMPTZ | nullable (null = still active)     |
| notes        | TEXT        | nullable, operational commentary   |
| notified_at | TIMESTAMPTZ | nullable — when notification was sent |

Lifecycle: raised → acknowledged → resolved.
Alerts are persisted (not ephemeral), enabling post-event review.
An alert is auto-resolved when the triggering forecast is superseded
by a new forecast that no longer exceeds the threshold.

Alerts can be triggered by either forecasts or real-time observations. When an observation exceeds a flood threshold, an alert is raised with `source = observation` and `forecast_id = NULL`. Observation-based alerts are resolved when a subsequent observation falls below the threshold.

**Deduplication indexes** (defined in the migration):
- Forecast alerts: `CREATE UNIQUE INDEX uq_active_forecast_alert ON alert_events (station_id, parameter_id, forecast_id, level) WHERE resolved_at IS NULL`
- Observation alerts: `CREATE UNIQUE INDEX uq_active_observation_alert ON alert_events (station_id, parameter_id, source, level) WHERE resolved_at IS NULL`

These partial unique indexes enforce alert idempotency at the database level.
`INSERT ... ON CONFLICT DO NOTHING` on these indexes prevents duplicate alerts
across flow reruns. Both indexes must be present in the migration — without them,
alert deduplication silently fails.

### audit_log

| Column     | Type        | Notes                              |
|------------|-------------|------------------------------------|
| id         | BIGSERIAL   | PK (not UUID — high volume)        |
| timestamp  | TIMESTAMPTZ | DEFAULT now()                      |
| user_id    | UUID        | FK -> users, nullable (system events) |
| action     | TEXT        | e.g. "login", "login_failed", "token_created", "flow_triggered" |
| detail     | JSONB       | Action-specific context            |
| ip_address | INET        | Client IP                          |

Append-only. No UPDATE or DELETE access granted to application database users.
Covers: authentication events, admin actions, token management, manual flow triggers.
Observation edits and forecast adjustments have their own dedicated audit tables.

**Proxy-aware IP logging**: Since the API sits behind Caddy, the application must
read `X-Forwarded-For` (or `X-Real-IP`) to log the true client IP, not the Caddy
container's internal IP. FastAPI is configured with trusted proxy headers
(`--proxy-headers`), and only the Caddy container's Docker network IP is trusted
as a proxy source — this prevents external clients from spoofing the header.

**Retention policy**: Audit log entries are retained for a minimum of 5 years, in compliance with typical government record-keeping requirements. The retention period is configurable per deployment (some jurisdictions require 7 or 10 years). Expired entries are archived to compressed files before deletion. No automated purge runs without explicit admin configuration.

**Integrity**: The current design relies on PostgreSQL GRANT restrictions (no
UPDATE/DELETE for application users) to prevent tampering. Threat model: the
primary risk is an insider with `sapphire_admin` credentials modifying audit
records. For v1.0, GRANT-based protection is sufficient given the small admin
team and physical server access controls. For v2.0, consider adding a
`prev_hash TEXT` column implementing a hash chain
(`SHA-256(prev_hash || timestamp || action || detail)`) to detect tampering.

### models

| Column        | Type        | Notes                              |
|---------------|-------------|------------------------------------|
| id            | TEXT        | PK — matches entry point name (e.g. "lstm_daily") |
| display_name  | TEXT        | Human-readable name for dashboard  |
| package       | TEXT        | Python package providing this model (e.g. "hf-forecasting") |
| version       | TEXT        | Currently installed version         |
| registered_at | TIMESTAMPTZ | When this model was first discovered |
| updated_at    | TIMESTAMPTZ | Last time version changed           |

Populated automatically at startup by the model discovery process
(see 04-models.md). Entry point names are stable identifiers — they don't
change when the model package is upgraded. The `version` column is updated
on each startup if the installed package version has changed.

### forecasts

| Column        | Type        | Notes                             |
|---------------|-------------|-----------------------------------|
| id            | UUID        | PK                                |
| station_id    | UUID        | FK -> stations                    |
| parameter_id  | UUID        | FK -> parameters                  |
| issued_at     | TIMESTAMPTZ | When the forecast was produced    |
| model_id      | TEXT        | FK -> models, which model produced this |
| model_version | TEXT        |                                   |
| forecast_type | ENUM        | subdaily / daily / pentadal / dekadal / monthly / seasonal |
| version       | INTEGER     | DEFAULT 1, incremented on status change |
| status        | ENUM        | raw / reviewed / selected / published |

**v1.0**: Models predict water level. Discharge conversion via rating curves is deferred to v2.0. The `parameter_id` distinguishes forecast parameters (water_level vs discharge). See 04-models.md.

`forecast_type` distinguishes temporal resolution. Models that natively
produce pentadal/monthly/seasonal output store results directly with the
appropriate type. Pentadal and dekadal forecasts can also be derived from
daily forecasts via aggregation at query time (see API).

`status` tracks the bulletin workflow:
- `raw` — freshly generated by the model
- `reviewed` — hydrologist has reviewed (may or may not have adjusted)
- `selected` — hydrologist selected this forecast for bulletin production
- `published` — bulletin containing this forecast has been generated

### forecast_values

| Column           | Type        | Notes                              |
|------------------|-------------|------------------------------------|
| forecast_id      | UUID        | FK -> forecasts                    |
| issued_at        | TIMESTAMPTZ     | Denormalized from forecasts for partition pruning |
| lead_time_minutes | INTEGER    | Lead time in minutes (e.g. 1440 = 1 day, 360 = 6 hours). Integer for indexing performance. Python domain types handle conversion. |
| member           | SMALLINT    | Ensemble member index (0 = median) |
| value            | FLOAT       |                                    |

Primary key: (forecast_id, lead_time_minutes, member)

This stores the full ensemble. Summary statistics (median, quantiles)
can be computed on read or materialized as views. Lead times can be
arbitrary intervals — works for sub-daily through seasonal horizons.

**Partitioning**: Partitioned by range on `issued_at` using monthly boundaries.
The `issued_at` column is denormalized from `forecasts` (8 bytes/row overhead,
negligible at scale). Partition creation is handled by the initial migration
and a scheduled maintenance task (pg_partman or cron). UUIDv7-based range
partitioning was evaluated and rejected due to the operational complexity of
managing monthly UUID range boundaries.
Managed by `pg_partman` with `premake = 3` (creates partitions 3 months ahead).
The health endpoint verifies that next month's partition exists.

### rating_curves

| Column     | Type        | Notes                              |
|------------|-------------|------------------------------------|
| id         | UUID        | PK                                 |
| station_id | UUID        | FK -> stations                     |
| valid_from | TIMESTAMPTZ | When this curve became active      |
| valid_to   | TIMESTAMPTZ | nullable (null = current)          |
| created_by | UUID        | FK -> users                        |
| data       | JSONB       | Stage-discharge pairs or equation  |
| uncertainty | JSONB      | nullable — uncertainty bounds (e.g. confidence intervals per stage) |

Rating curve uncertainty is a major source of error in discharge forecasts, especially at high flows. The `uncertainty` field stores confidence intervals or error percentages at each stage level. In practice, many hydromets do not quantify their rating curve uncertainty — the system provides tools to estimate and store it, but the field is nullable for deployments where uncertainty data is unavailable.

Versioned: new upload creates a new row; old row gets valid_to set.

### forecast_adjustments

| Column            | Type        | Notes                             |
|-------------------|-------------|-----------------------------------|
| id                | UUID        | PK                                |
| forecast_id       | UUID        | FK -> forecasts                   |
| adjusted_by       | UUID        | FK -> users                       |
| adjusted_at       | TIMESTAMPTZ |                                   |
| lead_time_minutes | INTEGER     | Which lead time was adjusted      |
| original          | JSONB       | Original ensemble or summary      |
| adjusted          | JSONB       | Adjusted values                   |
| reason            | TEXT        | nullable, free-text justification |

Full audit trail: every adjustment is an immutable log entry.

### model_skill

| Column            | Type        | Notes                              |
|-------------------|-------------|------------------------------------|
| id                | UUID        | PK                                 |
| station_id        | UUID        | FK -> stations                     |
| parameter_id      | UUID        | FK -> parameters                   |
| model_id          | TEXT        | FK -> models                       |
| model_version     | TEXT        |                                    |
| forecast_type     | ENUM        | Same enum as forecasts             |
| lead_time_minutes | INTEGER     | Skill at this specific lead time   |
| computed_at       | TIMESTAMPTZ | When this score was calculated     |
| period_start      | TIMESTAMPTZ | Evaluation period start            |
| period_end        | TIMESTAMPTZ | Evaluation period end              |
| metrics           | JSONB       | {"nse": 0.82, "crps": 1.3, ...}   |

Populated by a periodic verification flow that compares past forecasts
against observations. JSONB metrics allow flexible score storage without
schema changes when new metrics are added.

Skill scores span long evaluation periods (up to years). The 1-year maximum
query range on time-series endpoints (see 06-api.md) does not apply to skill
score queries — the `/skill` endpoints return all scores for the requested
station and model regardless of time range.

### access_tokens

| Column       | Type        | Notes                              |
|--------------|-------------|------------------------------------|
| id           | UUID        | PK                                 |
| token_hash   | TEXT        | Hashed bearer token                |
| name         | TEXT        | Human-readable label               |
| created_by   | UUID        | FK -> users (admin who created it) |
| created_at   | TIMESTAMPTZ |                                    |
| expires_at   | TIMESTAMPTZ | NOT NULL — maximum 12 months from creation. Enforced at creation time. |
| scope        | JSONB       | See below                          |

`scope` defines what the token can access:

```json
{
  "stations": ["ABC-001", "XYZ-042"],
  "forecast_status": ["published"],
  "read_only": true
}
```

- `stations`: list of station codes (null = all stations)
- `forecast_status`: which forecast statuses are visible (e.g. only published)
- `read_only`: always true for external tokens

Null semantics (must be tested):
- `stations: null` — grants access to ALL stations
- `stations: []` (empty array) — grants access to NO stations (effectively useless)
- `forecast_status: null` — all forecast statuses are visible

**v2.0 consideration**: Station codes could change (renaming, consolidation).
Scoping by station UUID instead of code would be more stable. Migration path:
add a `station_ids` (UUID array) field alongside `stations` (codes), deprecate
code-based scoping in v2.0.

This allows fine-grained sharing: give another government institution a
token that only shows reviewed forecasts for their region's stations.

### bulletins

| Column       | Type        | Notes                              |
|--------------|-------------|------------------------------------|
| id           | UUID        | PK                                 |
| generated_by | UUID        | FK -> users                        |
| generated_at | TIMESTAMPTZ |                                    |
| scope        | ENUM        | country / basin                    |
| basin_id     | UUID        | FK -> basins, nullable (null if country) |
| template_id  | TEXT        | Which Excel template was used      |
| file_path    | TEXT        | Path to generated Excel file       |

Tracks every bulletin generated for audit and re-generation.

Bulletin-to-forecast association is stored in a junction table:

### bulletin_forecasts

| Column       | Type        | Notes                              |
|--------------|-------------|------------------------------------|
| bulletin_id  | UUID        | FK -> bulletins, PK (composite)    |
| forecast_id  | UUID        | FK -> forecasts, PK (composite)    |

Primary key: (bulletin_id, forecast_id)

This provides referential integrity — the database prevents referencing
nonexistent forecasts, unlike a UUID array column.

## Time series conventions

- All timestamps are stored in UTC (TIMESTAMPTZ)
- Display timezone is a per-deployment config setting
- Irregular observations are stored as-is (no resampling at ingest)
- Resampling to regular intervals happens at query time or in model preprocessing
- Gaps are represented as missing rows, not sentinel values
- Ingest operations use `INSERT ... ON CONFLICT (station_id, parameter_id, timestamp) DO UPDATE
  SET value = EXCLUDED.value,
      quality_flag = CASE WHEN observations.quality_flag = 9 THEN 9 ELSE EXCLUDED.quality_flag END`
  — this handles duplicate fetches and source corrections while preserving manual
  exclusions (flag 9) set by forecasters. Re-ingesting data never silently undoes
  a forecaster's quality decision.
- Pentadal aggregation uses calendar pentads (days 1-5, 6-10, 11-15, 16-20, 21-25, 26-end),
  not rolling 5-day windows. Dekadal uses days 1-10, 11-20, 21-end.
- The last pentad (days 26-end) varies in length: 3 days (Feb non-leap), 4 days
  (Feb leap), 5 days (30-day months), 6 days (31-day months). For `sum` aggregations
  (e.g. precipitation), this asymmetry is intentional per WMO convention — consumers
  must account for variable pentad length. The API includes `pentad_days` in aggregated
  responses. Dekadal "21-end" similarly varies (8-11 days). Aggregation logic uses
  `calendar.monthrange(year, month)`, not hardcoded day counts.

## Indexing strategy

- observations: index on (station_id, parameter_id, timestamp)
- forecasts: index on (station_id, issued_at), index on (status)
- forecast_values: index on (forecast_id, lead_time_minutes)
- flood_thresholds: index on (station_id)
- model_skill: index on (station_id, model_id, lead_time_minutes)
- alert_events: index on (station_id, resolved_at) for active alert queries
- audit_log: index on (timestamp), index on (user_id, timestamp)
- Composite indexes tuned after real query profiling

## Data volume estimates (5 years of operation, 500 stations)

- Observations (15-min interval): ~500 x 2 params x 35,040/yr x 5 yrs = 175M rows
- Observations (daily, 25-yr backfill): ~500 x 2 x 9,125 = 9M rows
- Forecasts (2 runs/day, 50 members, 15 lead times): ~500 x 730 x 50 x 15 = 274M rows/yr
- Observation edits: negligible (manual, sparse)
- Model skill: negligible (periodic batch computation)
- Bulletins: ~2/day x 365 x 5 = ~3,650 rows
