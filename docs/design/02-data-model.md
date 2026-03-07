---
status: DRAFT
---

> **DRAFT** — This design doc has not completed the review maturity gate. Do not treat as authoritative until `status: READY`.

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
| metadata    | JSONB       | Flexible extra fields. Validated by `BasinMetadata` Pydantic schema (see types-and-protocols.md). |
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
| metadata    | JSONB       | Flexible extra fields. Validated by `StationMetadata` Pydantic schema (see types-and-protocols.md). |
| created_at  | TIMESTAMPTZ | DEFAULT now()                  |
| updated_at  | TIMESTAMPTZ | DEFAULT now(), trigger on update  |

Virtual stations (`kind = virtual`) represent ungauged forecast sites where runoff is calculated from upstream measurements or area-based methods rather than direct gauge observation. They participate in forecasting and alerting like regular stations but have no direct observations.

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

**Audit**: Changes to model assignments are logged to the `audit_log` table
with `action = 'model_config_changed'` and the previous/new values in `detail`.

**Operational workflow**:
- **Ordering requirement**: The `models` table must be populated before
  `station_model_config` rows can be inserted (FK constraint). Model discovery
  runs automatically at application startup (see 04-models.md), which populates
  the `models` table. The `import-model-config` CLI command verifies that all
  referenced `model_id` values exist in the `models` table before inserting,
  and fails with a clear error if any are missing.
- **Initial setup**: Developers bulk-import model assignments from TOML via
  `sapphire-flow import-model-config --file models.toml`. This is a one-time
  bootstrap — the TOML file is not read at runtime.
- **Ongoing changes**: Admin or forecaster roles can change a (station, parameter)
  model assignment via the dashboard or API. Forecasters see model skill scores to
  inform their choice.
- **Model upgrade**: When a new model package version is installed, a startup
  check compares `model_version` against the installed version and warns about
  mismatches. An admin can bulk-update versions via
  `PATCH /api/v1/admin/model-config/bulk`.

**Role permissions**:
- `admin`: full CRUD on model config
- `forecaster`: can change `model_id` and `fallback_model_id` for (station, parameter)
  pairs they monitor (not artifact paths — those are infrastructure)
- `viewer`: read-only

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

### parameters

| Column     | Type        | Notes                               |
|------------|-------------|-------------------------------------|
| id         | UUID        | PK                                  |
| name       | TEXT        | UNIQUE. e.g. "precipitation", "water_level" |
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
| source       | TEXT        | nullable. Provenance identifier, e.g. "bafu", "dhm", "smn", "manual". Set by the adapter at ingest time. |
| ingested_at  | TIMESTAMPTZ | DEFAULT now(). When the row was inserted. Enables data latency monitoring (ingested_at - timestamp = delivery delay). |

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
a forecaster explicitly excludes it (flag 9). Values 5-8 are reserved for
future QC checks. The QC service sets flags automatically after each ingest;
forecasters review flagged observations on the dashboard.

**Partitioning**: `PARTITION BY RANGE (timestamp)` with yearly boundaries
(e.g. `FROM ('2020-01-01') TO ('2021-01-01')`). The PK
`(station_id, parameter_id, timestamp)` already includes the partition key.
Managed via PostgreSQL declarative partitioning.
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

Drain process: A Prefect maintenance flow `drain_dead_letter_queue` (daily
or on-demand, with `concurrency_limit=1` to prevent concurrent drain
attempts; defined in `flows/maintenance.py`, scheduled alongside pg_partman
maintenance) queries rows using
`SELECT ... WHERE drained_at IS NULL ORDER BY created_at LIMIT 1000`.
Each row is processed in its own transaction:
1. Validate `source_table` against allowlist (`observations`, `forecast_values`, `weather_forecasts`)
2. Validate `payload` JSONB against the corresponding Pydantic boundary schema
   (e.g. observation rows → validate station_id, parameter_id, timestamp, value fields)
3. Resolve the target table via a **hard-coded mapping dict**
   (`{"observations": observations_table, "forecast_values": forecast_values_table,
   "weather_forecasts": weather_forecasts_table}`). The `source_table` TEXT value
   is NEVER interpolated into SQL — the mapping dict returns the SQLAlchemy table
   object or a parameterized query template. This prevents SQL injection even if
   the allowlist check is bypassed.
4. Attempt to INSERT into the resolved table
5. On success, set `drained_at = now()` and commit
6. On failure (e.g. partition still missing), log the error and continue to the next row

Per-row transactions ensure partial progress — rows that succeed are marked
drained even if later rows fail. The health endpoint reports non-empty dead
letter queue as a warning.

Startup check: If the dead letter queue has undrained rows, log a warning
with count and oldest `created_at`.

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
| unit         | TEXT        | nullable, e.g. "m a.s.l." or "m". Maps to `FloodThreshold.unit` NamedTuple field. |
| valid_from_month | SMALLINT    | nullable, range 1-12             |
| valid_to_month   | SMALLINT    | nullable, range 1-12             |
| exceedance_probability | FLOAT | nullable — when NULL, use global default for this level. Semantics: an alert fires when `(fraction of ensemble members exceeding threshold) >= exceedance_probability`. Lower values for severe levels provide earlier warning (more conservative). Typical defaults: danger=0.2, warning=0.5, watch=0.7. See `AlertConfig` in types-and-protocols.md. |
| created_at  | TIMESTAMPTZ | DEFAULT now()                      |
| updated_at  | TIMESTAMPTZ | DEFAULT now(), trigger on update  |

Composite key: (station_id, parameter_id, level, valid_from_month, valid_to_month)

**Month column consistency**: Both month columns must be NULL or both non-NULL.
A CHECK constraint enforces this:
```sql
CHECK ((valid_from_month IS NULL) = (valid_to_month IS NULL))
```
A partially-NULL pair (e.g. `valid_from_month=NULL, valid_to_month=3`) has
undefined semantics and is rejected at the database level.

**Year-round uniqueness**: Because `NULL != NULL` in PostgreSQL unique constraints,
the composite key alone cannot prevent duplicate year-round rows for the same
(station, parameter, level). A partial unique index enforces this:
```sql
CREATE UNIQUE INDEX uq_flood_threshold_year_round
  ON flood_thresholds (station_id, parameter_id, level)
  WHERE valid_from_month IS NULL AND valid_to_month IS NULL;
```

**Overlap prevention**: For seasonal thresholds, overlapping month ranges for the
same (station, parameter, level) are prohibited. An application-level CHECK
validates before INSERT/UPDATE:
- For each new row with non-NULL months, query existing seasonal rows for the same
  (station, parameter, level) and reject if any month falls in both ranges.
- The query logic uses the same wrapping-aware comparison as the threshold lookup.
This is enforced at the store layer, not via a DB constraint (PostgreSQL exclusion
constraints do not natively support circular month ranges).

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
| level        | ENUM        | watch / warning / danger (uses `alert_level_enum`, a 3-value subset — see note below) |
| lead_time_minutes | INTEGER | nullable — at which lead time threshold exceeded (NULL for observation-triggered alerts) |
| forecast_value | FLOAT     | The value that exceeded threshold   |
| threshold_value | FLOAT    | The threshold that was exceeded     |
| raised_at    | TIMESTAMPTZ | When the alert was created         |
| acknowledged_by | UUID     | FK -> users, nullable              |
| acknowledged_at | TIMESTAMPTZ | nullable                        |
| resolved_at  | TIMESTAMPTZ | nullable (null = still active)     |
| notes        | TEXT        | nullable, operational commentary   |
| notified_at | TIMESTAMPTZ | nullable — when notification was sent |

**ENUM note**: `flood_thresholds.level` uses `flood_level_enum` (4 values:
`normal / watch / warning / danger`). `alert_events.level` uses a separate
`alert_level_enum` (3 values: `watch / warning / danger`). Two separate
PostgreSQL ENUMs are used because alerts are never created for the `normal`
level — using a 3-value enum enforces this at the database level. The `normal`
threshold is stored for reference display only (e.g. showing the baseline
level on charts) and does not trigger alerts.

**Python type mapping**: The store layer maps the 3-value DB `alert_level_enum`
to the Python `FloodLevel` enum (which includes `NORMAL`). Since the DB
constraint guarantees `NORMAL` never appears, the mapping is safe:
`FloodLevel(row["level"])` always succeeds. The `AlertEvent` NamedTuple uses
`FloodLevel` (not a separate 3-value Python enum) to avoid enum proliferation
— the DB constraint is the enforcement point.

Lifecycle: raised → acknowledged → resolved.
Alerts are persisted (not ephemeral), enabling post-event review.
An alert is auto-resolved when the triggering forecast is superseded
by a new forecast that no longer exceeds the threshold.

Alerts can be triggered by either forecasts or real-time observations. When an observation exceeds a flood threshold, an alert is raised with `source = observation` and `forecast_id = NULL`. Observation-based alerts are resolved when a subsequent observation falls below the threshold.

**Deduplication indexes** (defined in the migration):
- Forecast alerts: `CREATE UNIQUE INDEX uq_active_forecast_alert ON alert_events (station_id, parameter_id, forecast_id, level) WHERE resolved_at IS NULL AND source = 'forecast'`
- Observation alerts: `CREATE UNIQUE INDEX uq_active_observation_alert ON alert_events (station_id, parameter_id, level) WHERE resolved_at IS NULL AND source = 'observation'`

A forecast alert and an observation alert for the same (station, parameter,
level) can coexist as active simultaneously — they represent independent
signals ("the forecast predicts flooding" vs "we're measuring flooding now").
The `source` filter in each WHERE clause ensures the two index scopes are
disjoint. The observation index no longer includes `source` in the indexed
columns because the WHERE clause already restricts to `source = 'observation'`.

These partial unique indexes enforce alert idempotency at the database level.
Inserts must specify the explicit arbiter:
- Forecast alerts:
  ```sql
  INSERT ... ON CONFLICT (station_id, parameter_id, forecast_id, level)
    WHERE resolved_at IS NULL AND source = 'forecast' DO NOTHING
  ```
- Observation alerts:
  ```sql
  INSERT ... ON CONFLICT (station_id, parameter_id, level)
    WHERE resolved_at IS NULL AND source = 'observation' DO NOTHING
  ```

**Why not `ON CONFLICT ON CONSTRAINT`?** PostgreSQL does not support
`ON CONFLICT ON CONSTRAINT` with partial unique indexes. The `WHERE` predicate
must be specified inline in the `ON CONFLICT` clause, matching the index
definition exactly. Both indexes must be present in the migration — without
them, alert deduplication silently fails.

### audit_log

| Column     | Type        | Notes                              |
|------------|-------------|------------------------------------|
| id         | BIGSERIAL   | PK (not UUID — high volume)        |
| timestamp  | TIMESTAMPTZ | DEFAULT now()                      |
| user_id    | UUID        | FK -> users, nullable (system events) |
| action     | TEXT        | e.g. "login", "login_failed", "token_created", "token_revoked", "flow_triggered", "model_config_changed", "account_locked", "password_reset", "admin_action". Full list matches `AuditDetail` discriminated union variants in types-and-protocols.md. |
| detail     | JSONB       | Action-specific context. Validated by `AuditDetail` discriminated union Pydantic schema (see types-and-protocols.md). |
| ip_address | INET        | Client IP                          |

Append-only. No UPDATE or DELETE access granted to application database users.
Covers: authentication events, admin actions, token management, manual flow triggers.
Observation edits and forecast adjustments have their own dedicated audit tables.

**Brute-force protection**: Dual-tracked lockout enforced at the API layer:
- **Per-username**: 5 failed login attempts within 15 minutes → 30-minute lockout
  (regardless of source IP). Prevents credential stuffing against a known account.
- **Per-IP**: 20 failed login attempts within 15 minutes → 30-minute lockout
  (regardless of username). Prevents distributed attacks across multiple accounts.

**Counter persistence**: Lockout counters MUST be shared across all API workers.
For the default single-worker deployment, an in-process dict with TTL-based
expiry is sufficient. For multi-worker deployments (multiple uvicorn workers),
counters are stored in Redis (required when `WEB_CONCURRENCY > 1`). On
application restart, counters are lost — this is acceptable because (a) Caddy's
rate limit provides a secondary defense layer, and (b) restarts are infrequent
relative to the 15-minute lockout window. The configuration is:
- `LOCKOUT_BACKEND=memory` (default, single-worker)
- `LOCKOUT_BACKEND=redis` (multi-worker, requires `REDIS_URL`)

Counters are checked before password verification. Lockout events are logged here with
`action = 'account_locked'`, the client IP, and `detail` indicating which
counter triggered the lockout (`{"trigger": "username"}` or `{"trigger": "ip"}`).
See 06-api.md "Password and brute-force policy". Additionally, `/auth/login`
has a stricter Caddy rate limit than general API endpoints (see 07-deployment.md).

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

### users (managed by fastapi-users)

Created and managed by the `fastapi-users` library. The application relies on:
- `id` (UUID): PK, referenced by observation_edits.edited_by,
  forecast_adjustments.adjusted_by, rating_curves.created_by,
  bulletins.generated_by, audit_log.user_id, station_model_config.updated_by
- `email` (TEXT): unique, used for login
- `hashed_password` (TEXT)
- `is_active` (BOOLEAN)
- `is_superuser` (BOOLEAN)
- `role` (ENUM): custom column using `user_role_enum` — "viewer", "forecaster", "admin".
  PostgreSQL ENUM prevents invalid values at the DB level.
- `totp_secret` (TEXT): nullable, for MFA

The exact schema is determined by fastapi-users at migration time. Additional
columns (role, totp_secret) are added via a custom user model that extends
the fastapi-users base.

**`is_superuser` vs `role` relationship**: `is_superuser` is a fastapi-users
built-in field. SAPPHIRE Flow does **not** use `is_superuser` for authorization
— all permission checks use the `role` column only. `is_superuser` is kept in
sync by the application: set to `true` when `role = 'admin'`, `false` otherwise.
This invariant is enforced in the custom user model's save logic. If the two
diverge (e.g., direct DB edit), `role` takes precedence.

**TOTP secret encryption**: The `totp_secret` column stores a Fernet-encrypted
value, not plaintext. The encryption key is loaded from a Docker secret
(`TOTP_ENCRYPTION_KEY`). This prevents TOTP secret exposure if the database
is compromised (dump, backup leak, SQL injection). Encryption and decryption
happen in the custom user model's property accessors.

**TOTP key rotation**: If `TOTP_ENCRYPTION_KEY` must be rotated (compromise,
employee departure), the application supports a `TOTP_ENCRYPTION_KEY_PREVIOUS`
Docker secret. On decryption, the current key is tried first; if it fails,
the previous key is tried. A CLI command
`sapphire-flow rotate-totp-key` re-encrypts all TOTP secrets with the current
key and logs the operation to `audit_log`. After re-encryption completes,
the previous key secret can be removed.

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
| created_at    | TIMESTAMPTZ | DEFAULT now()                      |
| updated_at    | TIMESTAMPTZ | DEFAULT now(), trigger on update   |

Unique constraint: (station_id, parameter_id, issued_at, model_id, forecast_type).
This enables idempotent upserts — re-running a forecast flow produces the same
row, not a duplicate. The `save_forecast` store method uses
`INSERT ... ON CONFLICT (station_id, parameter_id, issued_at, model_id, forecast_type)
DO UPDATE SET status = EXCLUDED.status, version = EXCLUDED.version, updated_at = now()`.

`model_version` is intentionally denormalized from `station_model_config` — it
captures the exact model version used at forecast time, enabling retrospective
analysis when model versions change.

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
| member           | SMALLINT    | Ensemble member index (0 = deterministic/control). forecast_values stores raw ensemble output only — no statistics rows. Median and percentiles are computed on read. |
| value            | FLOAT       |                                    |

Primary key: (issued_at, forecast_id, lead_time_minutes, member)

Note: `issued_at` must be part of the PK because PostgreSQL declarative
partitioning requires the partition key in all unique constraints.

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

**Query pattern**: Because `forecast_values` is partitioned by `issued_at`,
all queries must include `issued_at` for efficient partition pruning. Store
methods that look up forecast values by `forecast_id` use a two-step approach:
(1) query the `forecasts` header table to obtain `issued_at`, then (2) query
`forecast_values` with both `forecast_id` and `issued_at`. This avoids
cross-partition scans.

### weather_forecasts

| Column           | Type        | Notes                              |
|------------------|-------------|------------------------------------|
| station_id       | UUID        | FK -> stations (river station whose coordinates were used for NWP extraction) |
| parameter_id     | UUID        | FK -> parameters                   |
| issued_at        | TIMESTAMPTZ | When the NWP model run was issued  |
| lead_time_minutes| INTEGER     | Lead time from issued_at           |
| member           | SMALLINT    | Only summary statistics are stored: -1=median, -2=p10, -3=p90. Full ensemble members (≥0) are transient — see Storage strategy below. |
| value            | FLOAT       |                                    |

Primary key: (station_id, parameter_id, issued_at, lead_time_minutes, member)

**station_id semantics**: This references the *river station* whose (lon, lat)
coordinates were used to extract the nearest NWP grid point. It does NOT
reference a weather station. NWP grid point extraction is always relative to
the river station's location.

Partitioning: Monthly range on `issued_at`, managed by pg_partman (premake=3).
Same strategy as `forecast_values`.

**Upsert strategy**: `INSERT ... ON CONFLICT (station_id, parameter_id,
issued_at, lead_time_minutes, member) DO UPDATE SET value = EXCLUDED.value`.
NWP data may be re-fetched (e.g., retry after partial failure); the latest
value wins. No manual edits to NWP data — unconditional overwrite is safe.

**Storage strategy — two tiers**:
- **Database** (`weather_forecasts` table): Only ensemble summary statistics
  (member values -1=median, -2=p10, -3=p90). This keeps the table lean for
  operational queries.
- **File archive** (Parquet): Full ensemble point values (all members) are
  written to compressed Parquet files on disk at ingest time, organized as
  `data/nwp_archive/{issued_date}/{station_code}.parquet`. These are the
  extracted grid-point values (not raw GRIB2). The archive is permanent —
  retained indefinitely for bias correction, hindcasting, model development,
  and retry after partial failures.

The NWP ingest flow writes both tiers atomically: first the Parquet archive
(append), then the DB statistics. If the DB write fails, the archive still
exists for dead-letter recovery. Models that need full weather ensemble
input (via `needs_full_ensemble` flag, see types-and-protocols.md and 05-flows.md) read from the
Parquet archive at forecast time, not from this table.

Volume estimate (3 statistics per lead time):
- 50 stations:  50 × 7 × 3 × 120 × 4 = 504K rows/day, ~184M rows/year, ~9 GB/year
- 150 stations: 150 × 7 × 3 × 120 × 4 = 1.51M rows/day, ~552M rows/year, ~27 GB/year

Indexes:
- (station_id, parameter_id, issued_at DESC) — for "latest NWP forecast" queries.
  DESC on issued_at avoids backwards index scans.

### rating_curves

| Column     | Type        | Notes                              |
|------------|-------------|------------------------------------|
| id         | UUID        | PK                                 |
| station_id | UUID        | FK -> stations                     |
| valid_from | TIMESTAMPTZ | When this curve became active      |
| valid_to   | TIMESTAMPTZ | nullable (null = current)          |
| created_by | UUID        | FK -> users                        |
| data       | JSONB       | Stage-discharge pairs or equation. Validated by `RatingCurveData` Pydantic schema (see types-and-protocols.md). |
| uncertainty | JSONB      | nullable — uncertainty bounds (e.g. confidence intervals per stage). Validated by `RatingCurveUncertainty` Pydantic schema (see types-and-protocols.md). |

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
| original          | JSONB       | Original ensemble or summary. Validated by `EnsembleSnapshot` Pydantic schema (see types-and-protocols.md). |
| adjusted          | JSONB       | Adjusted values. Validated by `EnsembleSnapshot` Pydantic schema (see types-and-protocols.md). |
| reason            | TEXT        | nullable, free-text justification |

Full audit trail: every adjustment is an immutable log entry.

**Append-only enforcement**: Like `audit_log`, no UPDATE or DELETE access is
granted to application database users for `observation_edits` or
`forecast_adjustments`. These tables are audit records — immutability is
enforced at the database GRANT level.

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
| metrics           | JSONB       | {"nse": 0.82, "crps": 1.3, ...}. Validated by `SkillMetrics` Pydantic schema (see types-and-protocols.md). |

Unique constraint: (station_id, parameter_id, model_id, forecast_type,
lead_time_minutes, period_start, period_end). Prevents duplicate skill scores
from repeated verification runs. Re-computation for the same evaluation period
updates the existing row via upsert.

Populated by a periodic verification flow that compares past forecasts
against observations. JSONB metrics allow flexible score storage without
schema changes when new metrics are added.

Skill scores span long evaluation periods (up to years). The 1-year maximum
query range on time-series endpoints (see 06-api.md) does not apply to skill
score queries — the `/skill` endpoints return all scores for the requested
station and model regardless of time range.

### training_results

| Column        | Type        | Notes                              |
|---------------|-------------|------------------------------------|
| id            | UUID        | PK                                 |
| station_id    | UUID        | FK -> stations                     |
| model_id      | TEXT        | FK -> models                       |
| model_version | TEXT        |                                    |
| parameter_id  | UUID        | FK -> parameters                   |
| artifact_path | TEXT        | Path to the trained artifact       |
| metrics       | JSONB       | Training metrics (loss, val_loss, etc.). Validated by `SkillMetrics` Pydantic schema (reused from model_skill). |
| trained_at    | TIMESTAMPTZ | When training completed            |
| created_at    | TIMESTAMPTZ | DEFAULT now()                      |

Stores the result of each training run, written by
`TrainingStore.log_training_result()`. One row per training invocation —
retraining the same station/model produces a new row, not an update.
This provides a historical record of all training runs for auditing
and model regression detection.

### access_tokens

| Column       | Type        | Notes                              |
|--------------|-------------|------------------------------------|
| id           | UUID        | PK                                 |
| token_hash   | TEXT        | SHA-256 hex digest of the raw bearer token. SHA-256 is appropriate because tokens are generated with high entropy (≥256 bits); a slow hash (bcrypt/argon2) is unnecessary and would add latency to every authenticated request. |
| name         | TEXT        | Human-readable label               |
| created_by   | UUID        | FK -> users (admin who created it) |
| created_at   | TIMESTAMPTZ |                                    |
| expires_at   | TIMESTAMPTZ | NOT NULL — maximum 12 months from creation. Enforced at creation time. |
| revoked_at   | TIMESTAMPTZ | nullable — set when token is revoked before expiry |
| revoked_by   | UUID        | FK -> users, nullable — admin who revoked the token |
| scope        | JSONB       | See below. Validated by `AccessTokenScope` Pydantic schema (see types-and-protocols.md). |

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
- `read_only`: always true for external tokens. The API rejects token creation
  with `read_only: false` — scoped tokens are read-only by design. Enforcement:
  a FastAPI dependency checks the token's `read_only` flag and rejects all
  mutating requests (POST, PATCH, PUT, DELETE) with HTTP 403 if `read_only`
  is true. Since the creation endpoint enforces `read_only: true`, this is
  defense-in-depth.

**Scope enforcement**: Requests bearing a scoped token are filtered server-side
in the API layer — the consumer only sees data matching their scope. Enforcement
is implemented via FastAPI dependency injection that adds WHERE clause filters
based on the token's scope. See 06-api.md "Scoped access tokens".

Null semantics (must be tested):
- `stations: null` — grants access to ALL stations
- `stations: []` (empty array) — grants access to NO stations (effectively useless)
- `forecast_status: null` — all forecast statuses are visible

**v2.0 consideration**: Station codes could change (renaming, consolidation).
Scoping by station UUID instead of code would be more stable. Migration path:
add a `station_ids` (UUID array) field alongside `stations` (codes), deprecate
code-based scoping in v2.0.

**Token validation**: A token is valid when `revoked_at IS NULL AND expires_at > now()`.
Token revocation events are logged to `audit_log` with `action = 'token_revoked'`.
The `DELETE /api/v1/admin/tokens/{id}` endpoint sets `revoked_at` and `revoked_by`
rather than deleting the row, preserving the audit trail.

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

**NamedTuple mapping**: The `Bulletin` NamedTuple's `forecast_ids: list[UUID]`
field is populated by the store layer via `SELECT forecast_id FROM
bulletin_forecasts WHERE bulletin_id = ?`. The `bulletins` table has no
`forecast_ids` column — the junction table is the source of truth.

## Foreign key ON DELETE behavior (v1.0 defaults)

All FKs default to `ON DELETE RESTRICT` unless specified below. This prevents
accidental cascade deletion of historical data. A comprehensive FK audit with
potential CASCADE additions is planned for v2.0 (see 00-overview.md).

All FKs not listed explicitly below use `ON DELETE RESTRICT` (the stated
default). This table is exhaustive — every FK in the schema is listed.

| FK relationship | ON DELETE | Rationale |
|----------------|-----------|-----------|
| stations.basin_id → basins | SET NULL | Station survives basin reorganization |
| observations → stations | RESTRICT | Never delete a station with historical observations |
| observations → parameters | RESTRICT | Parameters are reference data |
| forecast_values → forecasts | RESTRICT | Forecasts are historical records — deletion is not a supported operation. To remove old data, drop entire partitions via pg_partman retention policy. |
| forecasts → stations | RESTRICT | Never delete a station with forecasts |
| forecasts → parameters | RESTRICT | Parameters are reference data |
| forecasts → models | RESTRICT | Model registry entries are permanent |
| alert_events → stations | RESTRICT | Alert history is audit-critical |
| alert_events → parameters | RESTRICT | Parameters are reference data |
| alert_events → forecasts | SET NULL | Allow forecast cleanup without losing alert history |
| bulletin_forecasts → bulletins | CASCADE | Junction rows follow the bulletin |
| bulletin_forecasts → forecasts | RESTRICT | Don't delete forecasts referenced by bulletins |
| bulletins.generated_by → users | RESTRICT | Bulletin audit requires the generating user |
| bulletins.basin_id → basins | SET NULL | Bulletin survives basin reorganization |
| station_model_config → stations | CASCADE | Config follows the station |
| station_model_config → parameters | RESTRICT | Parameters are reference data |
| station_model_config.model_id → models | RESTRICT | Model registry entries are permanent |
| station_model_config.fallback_model_id → models | RESTRICT | Model registry entries are permanent |
| station_model_config.updated_by → users | RESTRICT | Audit trail requires the user |
| station_weather_sources.river_station_id → stations | CASCADE | Linkage follows the station |
| station_weather_sources.weather_station_id → stations | CASCADE | Linkage follows the station |
| weather_forecasts → stations | RESTRICT | Never delete a station with NWP data |
| weather_forecasts → parameters | RESTRICT | Parameters are reference data |
| flood_thresholds → stations | RESTRICT | Threshold history is operationally critical |
| flood_thresholds → parameters | RESTRICT | Parameters are reference data |
| observation_edits → stations | RESTRICT | Edit history is audit-critical |
| observation_edits → parameters | RESTRICT | Parameters are reference data |
| observation_edits.edited_by → users | RESTRICT | Edit audit requires the user |
| forecast_adjustments → forecasts | RESTRICT | Adjustment history is audit-critical |
| forecast_adjustments.adjusted_by → users | RESTRICT | Adjustment audit requires the user |
| rating_curves → stations | RESTRICT | Rating curve history is audit-critical |
| rating_curves.created_by → users | RESTRICT | Rating curve audit requires the user |
| model_skill → stations | RESTRICT | Skill data is historical record |
| model_skill → parameters | RESTRICT | Parameters are reference data |
| model_skill → models | RESTRICT | Model registry entries are permanent |
| training_results → stations | RESTRICT | Training history is audit record |
| training_results → parameters | RESTRICT | Parameters are reference data |
| training_results → models | RESTRICT | Model registry entries are permanent |
| access_tokens.created_by → users | RESTRICT | Token audit requires the creating user |
| access_tokens.revoked_by → users | SET NULL | Allow user deactivation; revocation record preserved |
| audit_log → users | SET NULL | Preserve audit entries if a user is deactivated |

## Time series conventions

- All timestamps are stored in UTC (TIMESTAMPTZ)
- Display timezone is a per-deployment config setting
- Irregular observations are stored as-is (no resampling at ingest)
- Resampling to regular intervals happens at query time or in model preprocessing
- Gaps are represented as missing rows, not sentinel values
- Ingest operations use `INSERT ... ON CONFLICT (station_id, parameter_id, timestamp) DO UPDATE
  SET value = CASE WHEN observations.is_edited THEN observations.value ELSE EXCLUDED.value END,
      quality_flag = CASE WHEN observations.quality_flag = 9 THEN 9 ELSE EXCLUDED.quality_flag END,
      is_edited = CASE WHEN observations.is_edited THEN TRUE ELSE EXCLUDED.is_edited END`
  — this handles duplicate fetches and source corrections while preserving
  manual exclusions (flag 9), manually corrected values (`is_edited = true`),
  and the `is_edited` marker itself.
  Re-ingesting data never silently undoes a forecaster's quality or value decision.
- The `insert_observations_no_overwrite` store method uses
  `INSERT ... ON CONFLICT (station_id, parameter_id, timestamp) DO NOTHING` —
  existing rows are preserved unchanged, only genuinely new timestamps are inserted.
  Used for historical backfill where existing (possibly edited) data must not be touched.
- Pentadal aggregation uses calendar pentads (days 1-5, 6-10, 11-15, 16-20, 21-25, 26-end),
  not rolling 5-day windows. Dekadal uses days 1-10, 11-20, 21-end.
- The last pentad (days 26-end) varies in length: 3 days (Feb non-leap), 4 days
  (Feb leap), 5 days (30-day months), 6 days (31-day months). For `sum` aggregations
  (e.g. precipitation), this asymmetry is intentional per WMO convention — consumers
  must account for variable pentad length. The API includes `pentad_days` in aggregated
  responses. Dekadal "21-end" similarly varies (8-11 days). Aggregation logic uses
  `calendar.monthrange(year, month)`, not hardcoded day counts.

## Indexing strategy

All indexes on partitioned tables are **partition-local** (created on the
parent table and automatically propagated to each partition). PostgreSQL
creates a matching index on each partition — there is no global index
spanning all partitions. Queries must include the partition key column
for efficient partition pruning before the local index scan.

- observations: index on (station_id, parameter_id, timestamp) — partition-local, partition pruning on `timestamp` first
- forecasts: index on (station_id, issued_at), index on (status)
- forecast_values: index on (forecast_id, lead_time_minutes) — partition-local; queries always include `issued_at` for partition pruning (see Query pattern above), then the local index resolves `forecast_id` within the pruned partition
- weather_forecasts: index on (station_id, parameter_id, issued_at DESC) — partition-local; for "latest NWP forecast" queries. Also listed in the weather_forecasts section above.
- flood_thresholds: index on (station_id)
- model_skill: index on (station_id, model_id, forecast_type, lead_time_minutes)
- alert_events: index on (station_id, resolved_at) for active alert queries
- audit_log: index on (timestamp), index on (user_id, timestamp)
- Composite indexes tuned after real query profiling

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

## Open Questions

*None remaining.*

## Review History

| Round | Date | Reviewers | Blocking | Advisory | Status |
|-------|------|-----------|----------|----------|--------|
| 1 | 2026-03-07 | design-reviewer, review-docs, review-data-eng, review-security, review-domain | 9 | 25 | fixes-needed |
| 2 | 2026-03-08 | design-reviewer, review-docs, review-data-eng, review-security, review-domain | 14 | 27 | fixes-needed |
| 3 | 2026-03-08 | design-reviewer, review-docs, review-data-eng, review-security, review-domain | 9 | 22 | fixes-needed |
| 4 | 2026-03-08 | design-reviewer, review-docs, review-data-eng, review-security, review-domain | 11 | 31 | fixes-needed |
| 5 | 2026-03-08 | design-reviewer, review-docs, review-data-eng, review-security, review-domain | 14 | 30 | fixes-needed |
