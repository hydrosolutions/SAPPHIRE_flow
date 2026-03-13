# v0 Scope and Simplifications

> **Read this before implementing anything.** This document defines what v0 implements and how it
> differs from the full architecture in `architecture-context.md`. Implementation agents read this
> first, then `architecture-context.md` for details on specific flows.

## Guiding principles

- v0 = Swiss public data, ~50 stations, single VM, 1-2 users
- As fast as possible: aggressive performance optimization on the forecast cycle
- Research-friendly: easy to add models, run experiments, export data
- Professional enough for hydromet handover (clean types, documented APIs, reproducible results)
- `architecture-context.md` stays as the v1 north star — don't implement v1 complexity in v0

---

## Flows: what v0 implements

| Priority | Flow | v0 scope |
|----------|------|----------|
| 1 | **Flow 5/5w** — Station onboarding | Simplified bootstrap script (see A4 below). TOML import, historical obs, QC, baselines, model assignments. No dashboard, no progress tracking. |
| 2 | **Flow 2** — Observation ingest + QC | Stage 1 QC only. No rating curves (BAFU provides Q directly). Alerting steps optional (`enable_alert_cycle`). |
| 3 | **Flow 6 → 7 → 8** — Train → hindcast → skill | Auto-promote (no approval gate). Full skill metric suite (CRPS, CRPSss, BSS, POD/FAR/CSI, peak timing, NSE, KGE, PBIAS, MAE, diagrams). |
| 4 | **Flow 1** — Forecast cycle | Steps 1.2 (grid archive), 1.5 (NWP post-process), 1.9 (forecast post-process) are pass-through. Alerting (1.11-1.13) off by default. |
| — | **API** | FastAPI with basic CRUD for stations, observations, forecasts, alerts. No auth. Health endpoint. |

### Deferred beyond v0

| Flow | Earliest | Why |
|------|----------|-----|
| Flow 3 — Forecast review | v1 | No dashboard |
| Flow 4 — Pipeline monitoring | v0c or v1 | Manual supervision suffices at ~50 stations |
| Flow 9 — Model retraining (comparison) | v1 | Only initial training needed |
| Flow 10 — Skill recomputation (broad) | v1 | Flow 8 (narrow) covers v0 |
| Flow 11 — NWP gap recovery | v0c or v1 | Gaps accepted and logged |
| Flow 12 — Observation reprocessing | Branch B ad-hoc only | Branch A (rating curves) requires v1 |

---

## A. Simplifications

### A1. No table partitioning

**Full design**: 4 tables partitioned (observations yearly, forecast_values monthly, hindcast_values monthly, weather_forecasts monthly) with pg_partman, pg_cron, dead letter queue, DLQ drain task.

**v0**: Plain unpartitioned tables. ~50 stations with daily forecasts produce a few GB/year — negligible. Migration to partitioned tables later is a one-time operation on small data.

**Removes**: pg_partman, pg_cron extension, `dead_letter_queue` table, `drain_dlq` Prefect task, `PartitionMissingError` handling, all DLQ-related logic in stores.

### A2. No tiered data retention

**Full design**: Hot (PostgreSQL) → cold (Parquet) → delete at max_retention_days. Cold storage layout, archival task, hot/cold dispatch in stores, Parquet schema versioning.

**v0**: Everything stays in PostgreSQL. No cold storage, no archival task, no Parquet export. Set generous retention — v0 data fits in a few GB. **Exception**: `pipeline_health` and resolved `alerts` rows are deleted on a schedule (default 30 and 90 days respectively) to prevent unbounded growth — these have no analytical value and no cold-storage path.

**Removes**: `archive_cold_data` flow, cold storage directory layout, Parquet read/write in stores, hot/cold dispatch logic, schema version metadata.

### A3. No PgBouncer

**Full design**: PgBouncer in transaction mode, separate `DATABASE_URL_DIRECT` for migrations.

**v0**: Direct PostgreSQL connections. One API process + one Prefect worker = no connection pooling needed. Use asyncpg's built-in pool or SQLAlchemy's pool.

**Removes**: PgBouncer container, dual connection string config, transaction-mode gotchas.

### A4. Simplified station onboarding

**Full design**: Flow 5 is a 12-step Prefect flow with 7 phases, progress tracking, failure handling per station, model readiness branches (A/B/C/D).

**v0**: A Python script (or simple Prefect flow) that:
1. Reads TOML with station definitions
2. Inserts stations, basins, weather source mappings
3. Imports historical observations (bulk CSV/API)
4. Runs QC (reuses same QC service)
5. Computes baselines + flow regime boundaries
6. Configures model assignments
7. Marks stations operational

Training triggered separately (Flow 6) after onboarding completes. No onboarding dashboard, no progress tracking, no model readiness branches.

### A5. Full skill metrics (keep as designed)

Implement the full skill metric suite: CRPS, CRPSss (climatology + persistence baselines), BSS per danger level, POD/FAR/CSI, peak timing error, NSE, KGE, PBIAS, MAE — per lead time, per season, per flow regime. Plus reliability diagrams, ROC curves, rank histograms.

**Rationale**: Pure computation, high research value, most implementations available in libraries (`properscoring`, `xskillscore`). The effort is wiring, not algorithms.

### A6. Single Prefect work pool

**Full design**: Three work pools (ops, training, hindcast) with per-pool concurrency and resource limits.

**v0**: Single `default` pool. At v0 scale, resource isolation is unnecessary. Training runs are infrequent and manual.

### A7. Simplified model artifact lifecycle

**Full design**: 5 statuses (training → pending_approval → active → superseded → rejected), approval gate.

**v0**: 2 statuses: `active` and `superseded`. Training produces artifact → auto-promote → done. No approval gate.

### A8. No notification system

**Full design**: 3 channels (email, SMS, webhook), routing config, recipient management, retry sweep.

**v0**: Alerts logged to alerts table. Visible via API. No notification dispatch.

### A8a. Alert thresholds: ABOVE direction only

**Full design**: `ThresholdDirection.ABOVE` (flood) and `BELOW` (low-flow/drought). Direction is a field on `DangerLevelDefinition`.

**v0**: All danger levels use `ABOVE` (flood alerting). `BELOW` is supported by the type system but not exercised.

### A9. No forecast adjustments

**Full design**: forecast_adjustments table with 4 adjustment types, audit trail, envelope operations.

**v0**: Deferred entirely. No dashboard = no forecaster adjustments. Table schema can exist but no service logic needed.

### A10. Simple backup

**Full design**: restic with encryption, 7/4/12 retention, monthly automated restore rehearsal, 12-step recovery.

**v0**: `pg_dump` to local disk (cron or Prefect task). No restic, no encrypted backup chain, no restore rehearsal. Document a manual restore procedure.

---

## B. Deferred schemas (don't create tables)

These are deferred in architecture-context.md. For v0, don't create their tables until needed — add via Alembic migrations when actually implemented:

| Item | Tables/types to skip |
|------|---------------------|
| Auth (v1) | `users`, `access_tokens`, `refresh_tokens`, `audit_log`, UserRole, AuditEventType |
| Flow 3 — Forecast review | `forecast_adjustments`, AdjustmentType |
| Rating curves (v1) | `rating_curves`, `rating_curve_id` on observations, Stage 2 QC |
| Notification routing | `notification_routing`, `notification_recipients` |
| Bulletin generation | `bulletin/` module |
| Bikram Sambat calendar | Calendar enum, nepali-datetime dependency |
| Manual observation correction | Override columns on observations |
| Inferred thresholds | Flood frequency analysis service |
| Dead letter queue | `dead_letter_queue` table (no partitioning = no DLQ needed) |

**Rationale**: Empty "for later" tables add migration maintenance burden and clutter the schema.

---

## C. Database schema (v0 subset)

~18 tables. No partitioning, no DLQ, no auth, no cold storage dispatch.

### Reference data
- `parameters` — as designed (canonical parameter names, units, aggregation methods)

### Core entities
- `stations` — as designed (without override columns)
- `basins` — as designed
- `station_thresholds` — as designed
- `flow_regime_configs` — as designed

### Observations
- `observations` — as designed but **not partitioned**, drop `rating_curve_id` and `rating_curve_correction_version` columns (v1)

### Models
- `models` — as designed
- `station_groups` — as designed
- `station_group_members` — as designed
- `model_artifacts` — as designed but status enum reduced to `active | superseded` only
- `model_assignments` — as designed
- `model_states` — as designed
- `station_weather_sources` — as designed (geometry columns removed; basin geometry and band geometries live in `basins` table, resolved via `stations.basin_id`)

### Forecasts
- `forecasts` — as designed
- `forecast_values` — as designed but **not partitioned**

### Hindcast
- `hindcast_forecasts` — as designed but **not partitioned**
- `hindcast_values` — as designed but **not partitioned**

### Weather archive
- `weather_forecasts` — as designed but **not partitioned**, drop gap recovery fields (`is_gap`, `gap_status`)

### Skill
- `skill_scores` — as designed
- `skill_diagrams` — as designed

### Operational support
- `alerts` — as designed (without notification-related fields: keep `notified_at` as always-NULL)
- `pipeline_health` — as designed

### Not created in v0
`dead_letter_queue`, `forecast_adjustments`, `users`, `access_tokens`, `refresh_tokens`, `audit_log`, `rating_curves`, `notification_routing`, `notification_recipients`

---

## D. Performance: fast forecast cycle

Target: full forecast cycle for 50 stations in < 60 seconds.

### D1. Pre-load model artifacts

Load model artifacts into memory at worker startup (or LRU cache on first use). ML model deserialization can take 10-30s — do it once. `ModelArtifactCache` singleton keyed by `(model_id, artifact_id)`. Pre-warm on startup. Invalidate on artifact promotion.

### D2. Batch database operations

- **Writes**: PostgreSQL `COPY` protocol (asyncpg `copy_to_table()` or Polars `write_database()`) for forecast_values. 50 stations x 21 members x 120 timesteps = 126K rows — COPY is 10-50x faster than INSERT.
- **Reads**: `WHERE station_id = ANY($1)` for batch observation fetch. ConnectorX or Polars `read_database_uri()` for bulk reads into DataFrames.
- **Pool**: asyncpg connection pool (5-10 connections) in the worker process.

### D3. Parallel execution

- NWP fetch + observation fetch overlap completely (`asyncio.gather()`)
- Phase B: `task.map()` across (model, station) pairs, `max_workers` matching CPU cores
- Group-scoped models: deserialize artifact once, share via `unmapped()`
- Station-scoped models: parallelize `predict()` across stations

### D4. Minimize Prefect overhead

- `persist_result=False` on tasks passing large objects (NWP DataFrames)
- `unmapped()` for shared data
- `log_prints=False` on high-frequency tasks
- Consider plain function calls within a single task when per-station task overhead dominates

### D5. Memory-efficient data flow

- Single Polars DataFrame for NWP data indexed by station_id (copy-on-write slicing)
- Slice per station at the last moment (inside `prepare_model_inputs()`)
- Polars lazy evaluation (`.lazy()` → chain → `.collect()`) to avoid intermediate materialization

### D6. Per-step instrumentation (mandatory from day one)

Every Flow 1 step instrumented with `time.perf_counter()` + structured logging.

Target per-step budgets (50 stations):

| Step | Target | Bottleneck |
|------|--------|-----------|
| 1.1 NWP fetch | 15-30s | Network |
| 1.3 Spatial extraction | 5s | CPU |
| 1.6 Observation fetch | 2s | DB read |
| 1.7 Prepare inputs | 3s | In-memory |
| 1.8 Run models (all) | 10-30s | CPU (parallel) |
| 1.10 Store results | 3s | DB write (COPY) |
| **Total** | **< 60s** | |

### D7. API response speed

- TTL cache (30-60s) for station metadata, latest forecast summaries, alert status
- Pre-computed ensemble summary statistics (mean, P10, P25, P50, P75, P90) stored alongside raw members
- `orjson` for serialization (3-10x faster than stdlib json)
- Cursor-based pagination
- gzip/brotli compression in Caddy

---

## E. Testing and CI/CD

### E1. Two-tier test datasets

**Tier 1 — Synthetic (unit tests, no I/O)**: Programmatic generators in `tests/conftest.py`:
`make_observations()`, `make_nwp_forecast()`, `make_station_config()`. Deterministic (seeded RNG), in-memory. Factory variants for edge cases.

**Tier 2 — Real CAMELS-CH (integration/e2e)**: 5-10 BAFU stations, 2 years hourly discharge, corresponding SMN weather, recorded ICON-CH2-EPS for 3-5 cycles. Known edge cases baked in. Known-correct golden answers for regression testing. ~50-100 MB in `tests/fixtures/reference/`.

### E2. Replay adapters

Every external dependency goes through an adapter Protocol. Test replay adapters serve recorded data with `simulated_time` parameter:
- `ReplayNwpAdapter` → recorded GRIB2/Parquet from fixtures
- `ReplayStationAdapter` → recorded observation CSVs from fixtures

Full forecast cycle runs in seconds using recorded data — no network, no waiting.

### E3. Scenario-based integration tests

| Scenario | Edge cases |
|----------|-----------|
| Normal cycle | Happy path baseline |
| Late NWP | Fallback to previous cycle |
| Missing observations | Staleness warning, forecast proceeds |
| QC failures | Spikes flagged, excluded from model inputs |
| Model failure | Fallback to next priority model |
| Threshold exceedance | Alert raised at correct level |
| Empty ensemble | Skip threshold check, metadata flag |
| Full onboarding → forecast | End-to-end init → operational path |

### E4. Test database

`testcontainers-python` for PostgreSQL + PostGIS per session (~3s startup). Transaction rollback per test for isolation. Real PostgreSQL — no SQLite, no mocks of PostGIS/JSONB/partial indexes.

Target: full integration suite < 60s locally. Individual tests < 5s.

### E5. CI pipeline (GitHub Actions)

Three parallel jobs:
- **lint**: ruff + pyright --strict (< 30s)
- **unit**: pytest tests/unit/ --cov (< 30s, no DB)
- **integration**: real PostgreSQL, replay adapters, scenario tests (< 2 min)
- **e2e**: full pipeline, golden answer comparison (< 5 min)

Total CI wall time: < 5 min.

### E6. Adapter recording tool

CLI to refresh reference dataset from public APIs:
```bash
uv run python -m sapphire_flow.tools.record_fixtures \
  --stations tests/fixtures/reference/stations.toml \
  --start 2025-01-01 --end 2025-12-31 \
  --output tests/fixtures/reference/
```

### E7. Performance regression detection

CI records Flow 1 execution time per step. Warns (doesn't fail) on > 50% regression vs baseline in `tests/fixtures/reference/performance_baseline.json`.

---

## F. Infrastructure (v0)

Docker Compose with simplified topology:

| Service | Image | Notes |
|---------|-------|-------|
| `postgres` | postgis/postgis:16-3.4 | No pg_partman, no pg_cron |
| `prefect-server` | prefecthq/prefect:3-python3.11 | |
| `prefect-worker` | custom (sapphire-flow) | **Single worker** (no pool separation) |
| `api` | custom (sapphire-flow) | FastAPI, no auth |
| `caddy` | caddy:2 | Reverse proxy |
| `init` | custom (sapphire-flow) | One-shot: migrations + deployment registration |

**Not in v0**: PgBouncer, separate training/hindcast workers, restic backup, restore rehearsal.

---

## G. Types and Protocols (full implementation)

Implement the **full** type system and Protocol definitions from `types-and-protocols.md`. Types are cheap, catch bugs early, and define the contract for all downstream implementation. This includes:

- All ID NewTypes, UtcDatetime, GeoCoord
- All enums (minus deferred ones: UserRole, AuditEventType, AdjustmentType, Calendar)
- All entity NamedTuples
- All store Protocols (minus RatingCurveStore, ForecastAdjustmentStore)
- All adapter Protocols (minus NotificationAdapter)
- ForecastModel Protocol (both StationForecastModel and GroupForecastModel)

---

## H. Implementation phases

```
Phase 1a: Types, Protocols, config loading, DB schema (Alembic)     ─┐
Phase 1b: Test infra (testcontainers, conftest, replay Protocols, CI) ├─ parallel
Phase 10: Docker Compose (simplified)                                 ─┘
          │
Phase 2: Store implementations (PostgreSQL) + integration tests  ─┐
Phase 3: Adapters (production + replay)                           ├─ parallel
          │                                                       │
Phase 3b: Record reference test dataset from public APIs          │
Phase 4: Services (QC, threshold, alert, skill, forecast input)  ─┘
          │
Phase 5: Station onboarding (simplified) ─┐
Phase 6: Observation ingest (Flow 2)       ├─ parallel
Phase 7: Model framework + training        │
Phase 9: FastAPI REST API                 ─┘
          │
Phase 8: Forecast cycle (Flow 1) + scenario tests
          │
Phase 11: End-to-end test (onboard → train → hindcast → skill → forecast → API)
```

Key principles:
- Test infrastructure built alongside production code from day one
- Every phase includes its tests — no separate testing phase
- Replay adapters built alongside production adapters (same Protocol)
- Phase 11 is the capstone: proves full pipeline against golden answers

---

## I. v1 compatibility risks

v0 is deliberately scoped down from `architecture-context.md`. The Protocol-first architecture makes most v1 additions purely additive (partitioning, PgBouncer, dashboard, notifications, forecast adjustments, Bikram Sambat calendar). Two areas require active guarding during v0 implementation to avoid dead ends:

### I1. Keep spatial type unions in service signatures

v0 uses basin-average extraction only (ICON-CH2-EPS). Nepal v1 needs elevation-band extraction (ECMWF IFS). The `GridExtractor` Protocol already returns `BasinAverageForecast | ElevationBandForecast`, but v0 implementations may be tempted to narrow signatures to just `BasinAverageForecast`.

**Rule**: Any service or flow function that handles weather forecast data must accept the full `BasinAverageForecast | ElevationBandForecast` union type, even if v0 only produces the former. Test fakes should exercise both variants where feasible.

### I2. Keep forcing source injectable in training and inference

v0 uses SMN station observations for ML model lookback windows (open decision in architecture-context.md). Nepal v1 will use ERA5-Land via `WeatherReanalysisSource`. If `prepare_model_inputs()` or training data assembly hardcodes "fetch from co-located weather station," the entire training/inference pipeline needs rework for v1.

**Rule**: Training data gathering (Flow 6 step T.2) and forecast input preparation (Flow 1 step 1.7) must accept a forcing source dependency (adapter), not directly query a specific data source. The `WeatherReanalysisSource` Protocol exists but is not implemented in v0 — the injection point must still be present.

### Not risks (safe to defer)

| v1 feature | Why safe |
|------------|----------|
| Table partitioning | Additive migration on small data |
| Rating curve columns on observations | Nullable column addition (metadata-only in PostgreSQL) |
| Stage 2 QC (2.5–2.7) | Independent flag set, does not change Stage 1 interface |
| Notification dispatch | Reads alerts, does not change alert model |
| Forecast adjustments / Flow 3 | New table + service + API endpoints, no v0 schema conflicts |
| Tiered retention / cold storage | Additive archival task, no schema changes |
