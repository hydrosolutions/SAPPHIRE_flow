# v0 Scope and Simplifications

> **Read this before implementing anything.** This document defines what v0 implements and how it
> differs from the full architecture in `architecture-context.md`. Implementation agents read this
> first, then `architecture-context.md` for details on specific flows.

## Guiding principles

- v0 = Swiss public data, up to ~170 stations (LINDAS-available BAFU gauges), single VM, 1-2 users. Architecture supports scaling to ~1000 stations across deployments.
- As fast as possible: aggressive performance optimization on the forecast cycle
- Research-friendly: easy to add models, run experiments, export data. v0 includes multi-parameter forecasting experiments — river stations forecast discharge, lake stations (33 in CAMELS-CH) forecast water_level. This validates the multi-target pipeline (§A13) before Nepal v1 deployment, which requires both discharge and water_level stage.
- Professional enough for hydromet handover (clean types, documented APIs, reproducible results)
- `architecture-context.md` stays as the v1 north star — don't implement v1 complexity in v0

---

## Flows: what v0 implements

| Priority | Flow | v0 scope |
|----------|------|----------|
| 1 | **Flow 5/5w** — Station onboarding | Simplified bootstrap script (see A4 below). TOML import, historical obs, QC, baselines, model assignments. No dashboard, no progress tracking. |
| 2 | **Flow 2** — Observation ingest + QC | Stage 1 QC only. No rating curves (BAFU provides Q directly). Alerting steps optional (`enable_observation_alerts`). |
| 3 | **Flow 6 → 7 → 8** — Train → hindcast → skill | Auto-promote (no approval gate). Full skill metric suite (CRPS, CRPSss, BSS, POD/FAR/CSI, peak timing, NSE, KGE, PBIAS, MAE, diagrams). |
| 3 | **Flow 13** — Model onboarding | Register + validate + smoke test + train + hindcast + skill gate + auto-promote. Sample model (LinearRegressionDaily). No approval gate (auto-promote). Reuses services from Flows 6/7/8 directly (does NOT call `train_models` flow — composes the underlying service layer to interpose the skill gate). |
| 4 | **Flow 1** — Forecast cycle | **v0a**: point weather forecast data (pre-extracted); steps 1.2, 1.3, 1.4 skipped entirely. **v0b+**: gridded NWP (ICON-CH2-EPS) with GridExtractor. Steps 1.5 (NWP post-process) and 1.9 (forecast post-process) are pass-through throughout v0. Step 1.10 (forecast QC) is active throughout v0. Alerting (1.12-1.14) controlled by `enable_forecast_alerts` (default `false`). |
| — | **API** | FastAPI with basic CRUD for stations, observations, forecasts, alerts. No auth. Health endpoint. |
| — | **Flow 12B** — Manual CSV import | Branch B only (validate CSV, ingest with `source = 'manual_import'`, run QC). Branches A (rating curve reprocessing) and C (QC re-evaluation) deferred. |

### Deferred beyond v0

| Flow | Earliest | Why |
|------|----------|-----|
| Flow 3 — Forecast review | v1 | No dashboard |
| Flow 4 — Pipeline monitoring | v0c or v1 | **→ DECISION (plan 013)**: Manual supervision suffices at Swiss v0 scale (~170 stations). Health endpoint (`/api/v1/health`) does lightweight live checks (DB ping, Prefect heartbeat) independently — does not require Flow 4. `pipeline_health` table exists but is not populated until Flow 4 is implemented. |
| Flow 9 — Model retraining (comparison) | v1 | Only initial training needed |
| Flow 10 — Skill recomputation (broad) | v1 | Flow 8 (narrow) covers v0 |
| Flow 11 — NWP gap recovery | v0c or v1 | Gaps accepted and logged |
| Flow 12 — Observation reprocessing | Branch B ad-hoc only | Branch A (rating curves) requires v1 |
| NWP lateness fallback | v0b or v1 | Manual monitoring suffices; three-stage strategy (wait → fallback cycle → skip) implemented when gridded NWP is added |
| Flow 13 approval gate | v1 | Auto-promote sufficient for v0; PENDING_APPROVAL + human review added in v1 |

---

## A. Simplifications

### A1. No table partitioning

**Full design**: 4 tables partitioned (observations yearly, forecast_values monthly, hindcast_values monthly, weather_forecasts monthly) with pg_partman, pg_cron, dead letter queue, DLQ drain task.

**v0**: Plain unpartitioned tables. **→ DECISION (plan 013)**: At the Swiss v0 ceiling of ~170 stations with 1 forecast parameter/station and 4 cycles/day, forecast_values grows at ~0.1 GB/day raw (~0.2–0.4 GB/day with PostgreSQL overhead). At the architectural ceiling of ~1000 stations across deployments: ~0.6 GB/day raw (~1.2–2.4 GB/day with overhead), producing ~3.7B forecast_values rows/year. Partitioning deferral remains defensible for Swiss v0 (~170 stations); revisit when a deployment exceeds ~300 stations or when cumulative forecast_values exceeds ~500M rows. Migration to partitioned tables is a one-time operation but no longer on "small data" at the 1000-station ceiling.

**Removes**: pg_partman, pg_cron extension, `dead_letter_queue` table, `drain_dlq` Prefect task, `PartitionMissingError` handling, all DLQ-related logic in stores.

### A2. No tiered data retention

**Full design**: Hot (PostgreSQL) → cold (Parquet) → delete at max_retention_days. Cold storage layout, archival task, hot/cold dispatch in stores, Parquet schema versioning.

**v0**: Everything stays in PostgreSQL. No cold storage, no archival task, no Parquet export. **→ DECISION (plan 013)**: At ~170 Swiss stations, daily storage growth is ~0.2–0.4 GB/day; the 548-day hot window (architecture-context.md line 2595) accumulates ~0.11–0.22 TB — well within a 1 TB SSD. At the ~1000-station architectural ceiling: 1.2–2.4 GB/day, accumulating ~0.64–1.28 TB in the hot window. Retention deferral is defensible for Swiss v0; revisit when total DB size approaches 500 GB. **Exception**: Resolved `alerts` rows are deleted on a schedule (default 90 days) to prevent unbounded growth — these have no analytical value and no cold-storage path.

**Removes**: `archive_cold_data` flow, cold storage directory layout, Parquet read/write in stores, hot/cold dispatch logic, schema version metadata.

### A3. No PgBouncer

**Full design**: PgBouncer in transaction mode, separate `DATABASE_URL_DIRECT` for migrations.

**v0**: Direct PostgreSQL connections. One API process + one Prefect worker = no connection pooling needed. Use asyncpg's built-in pool or SQLAlchemy's pool. **→ DECISION (plan 013)**: PgBouncer deferral remains safe for v0. At ~1000 stations with sub-daily ingest, connection pressure increases (particularly v0b's GridExtractor subflow concurrency), but asyncpg's built-in pool with 5-10 connections is sufficient for a single worker process. Revisit if connection pool exhaustion is observed or if multiple worker processes are introduced.

**Removes**: PgBouncer container, dual connection string config, transaction-mode gotchas.

### A4. Simplified station onboarding

**Full design**: Flow 5 is a 12-step Prefect flow with 7 phases, progress tracking, failure handling per station, model readiness branches (A/B/C/D).

**v0**: A Python script (or simple Prefect flow) that:
1. Reads TOML with station definitions (v0 defaults: `network = "bafu"`, `ownership = "own"`)
2. Inserts stations, basins (with `network`), weather source mappings
3. Imports historical observations (bulk CSV/API)
4. Runs QC (reuses same QC service)
5. Computes climatology quantiles (CRPSss reference), persistence baseline, and flow regime boundaries (Q50/Q90)
6. Configures model assignments (**Prerequisite**: Model types must be onboarded via Flow 13 before they can be assigned to stations in this step.)
7. Triggers training (Flow 6 → 7 → 8). Station remains in `onboarding` status.
8. Marks stations `operational` after ≥1 model artifact reaches `active` status (auto-promoted in v0)

Training is triggered as part of onboarding (step 7), not as a separate workflow. No onboarding dashboard, no progress tracking, no model readiness branches.

For `station_kind = 'weather'` stations (Flow 5w), steps 5–8 are skipped — weather stations provide forcing data, not forecasts. They become `operational` after QC (step 4).

**v0a** skips catchment attribute fetching (step 5.2) — the initial linear regression model does not require static attributes. **v0b** adds catchment attribute fetching when ML models with `data_requirements.static_features` are introduced.

### A5. Full skill metrics (keep as designed)

Implement the full skill metric suite: CRPS, CRPSss (climatology + persistence baselines), BSS per danger level, POD/FAR/CSI, peak timing error, NSE, KGE, PBIAS, MAE, sharpness (mean prediction interval width, mean ensemble range) — per lead time, per season, per flow regime. Plus reliability diagrams, ROC curves, rank histograms.

**Rationale**: Pure computation, high research value. CRPS and all metrics are implemented from scratch (~20 lines of numpy each) — `properscoring` is abandoned (last release 2015, numpy compatibility risk) and `xskillscore` adds a heavy xarray dependency. The effort is wiring, not algorithms.

### A6. Single Prefect work pool

**Full design**: Three work pools (ops, training, hindcast) with per-pool concurrency and resource limits.

**v0**: Single `default` pool. **→ DECISION (plan 013)**: Resource isolation remains unnecessary for v0. Training runs are infrequent and manual. At ~170 Swiss stations, training does not compete materially with the forecast cycle. Operational note: avoid running `train_models` concurrently with `run_forecast_cycle` on resource-constrained VMs. Pool separation (three-pool topology) is warranted when training becomes scheduled/frequent or when a deployment exceeds ~500 stations.

### A7. Simplified model artifact lifecycle

**Full design**: 5 statuses (training → pending_approval → active → superseded → rejected), approval gate.

**v0**: The PostgreSQL enum and Python `ModelArtifactStatus` define all 5 values (`training`, `pending_approval`, `active`, `superseded`, `rejected`) for forward compatibility — `ALTER TYPE ... ADD VALUE` cannot run inside a transaction, so adding values later is painful. v0 wires only 3 transition paths: `training` → `active` (auto-promote after skill gate), `active` → `superseded` (replaced by newer artifact), and `training` stays on gate rejection. The `pending_approval` and `rejected` statuses are defined but not reachable in v0.

Flow 13 (model onboarding) uses the same auto-promote path: `training` → `active`. The approval gate (skill comparison, human review, `pending_approval` → `active`/`rejected` transitions) is a v1 addition. Flow 13 adds a skill gate evaluation step that in v0 does not block promotion by default (`skill_gate_thresholds = {}`); configuring thresholds activates blocking (gate rejection leaves artifact in `training` status).

**Worst-across-strata aggregation**: The skill gate evaluates the worst score across all strata (lead time × season × flow regime) against each threshold. "Worst" is direction-aware: `min(scores)` for `higher_is_better` metrics (e.g. CRPSS, NSE — higher is better, so the minimum is the worst), and `max(scores)` for `lower_is_better` metrics (e.g. CRPS, RMSE — lower is better, so the maximum is the worst). A model must meet the threshold in every stratum to pass. Strata with fewer than `min_skill_samples` forecast-observation pairs are excluded before aggregation to prevent noisy low-sample strata from producing spurious rejections.

**`SKIPPED_INSUFFICIENT_EVAL`**: If **zero** strata survive the `min_skill_samples` filter (i.e. no stratum has enough pairs to be evaluated), the unit outcome is `SKIPPED_INSUFFICIENT_EVAL` rather than `GATE_REJECTED`. This distinguishes "model failed quality bar" from "insufficient observation data to evaluate the model." `GATE_REJECTED` is reserved for cases where scores exist but fall below thresholds.

### A8. No notification system

**Full design**: 3 channels (email, SMS, webhook), routing config, recipient management, retry sweep.

**v0**: Alerts logged to alerts table. Visible via API. No notification dispatch.

### A8a. Alert thresholds: ABOVE direction only

**Full design**: `ThresholdDirection.ABOVE` (flood) and `BELOW` (low-flow/drought). Direction is a field on `DangerLevelDefinition`.

**v0**: All danger levels use `ABOVE` (flood alerting). `BELOW` is supported by the type system but not exercised.

### A8b. Threshold checking on raw forecasts only

**Full design**: Configurable — check on raw forecasts, published forecasts, or both (see architecture-context.md).

**v0**: Raw only. Flow 3 (forecast review) is deferred, so no `reviewed`→`published` transition exists. All forecasts stay `raw`. Threshold checks (1.12-1.14) run immediately after model output, when enabled via `enable_forecast_alerts`.

### A8c. Per-source alert enablement

**Full design**: All alert sources active by default.

**v0**: Three independent flags in `DeploymentConfig`, all default `false`:
- `enable_forecast_alerts` — gates Flow 1 Phase C (steps 1.12–1.14)
- `enable_observation_alerts` — gates Flow 2 steps 2.8–2.10
- `enable_pipeline_alerts` — gates Flow 4 steps 4.6–4.7

Rationale: per-source flags allow incremental activation during testing — pipeline alerts first (ops team, low risk), then observation alerts (simple value-vs-threshold), then forecast alerts (probability-based, needs hysteresis tuning). Aligns with the three `AlertSource` enum values (`forecast`, `observation`, `pipeline`).

### A8d. Multi-model alert strategy

**Full design**: Four strategies (primary, pooled, bma, consensus) selectable per deployment via `alert_model_strategy` config. BMA is the recommended default for mature multi-model deployments. Cascading fallback: bma → pooled → primary.

**v0**: `alert_model_strategy` config field exists with default `primary`. The strategy enum, config field, convergence structure, and type traceability (`model_ids` on `ExceedanceResult` and `Alert`) are implemented from day one. Only `PrimaryModelStrategy` is exercised at runtime.

**v0b**: `pooled` strategy implemented when second model is onboarded per station. Deployers with multiple models per station switch config to `pooled`.

**v1**: `bma` strategy implemented with weight training pipeline (linked to Flow 8/10 skill recomputation). Deployers switch config to `bma` once weights are trained. `consensus` strategy implemented if stakeholder demand exists.

### A9. No forecast adjustments

**Full design**: forecast_adjustments table with 4 adjustment types, audit trail, envelope operations.

**v0**: Deferred entirely. No dashboard = no forecaster adjustments. Table schema can exist but no service logic needed.

### A10. Simple backup

**Full design**: restic with encryption, 7/4/12 retention, monthly automated restore rehearsal, 12-step recovery.

**v0**: `pg_dump` to local disk (cron or Prefect task). No restic, no encrypted backup chain, no restore rehearsal. Document a manual restore procedure.

### A11. Point weather data first, gridded later

**Full design**: `WeatherForecastSource` returns either `GriddedForecast` (raw NWP grid) or `dict[StationId, WeatherForecastResult]` (pre-extracted). Gridded sources go through `GridExtractor` for bulk spatial extraction (steps 1.2–1.4).

**v0a**: Use pre-extracted point weather forecast data only. Steps 1.2 (grid archive), 1.3 (spatial extraction), and 1.4 (extraction archive) are skipped entirely — the adapter returns `dict[StationId, PointForecast]` directly. This simplifies initial development: no GRIB2 parsing, no xarray dependency, no basin geometry processing.

**v0b+**: Add gridded NWP support (ICON-CH2-EPS GRIB2 via STAC API) with `GridExtractor` for basin-average extraction. Steps 1.2–1.4 become active. The `WeatherForecastSource` Protocol already supports both return types — this is an adapter swap, not an architecture change.

**Compatibility**: Service and flow signatures accept `WeatherForecastResult` (the full union type) from day one. Only the adapter implementation changes between v0a and v0b.

### A12. ML lookback forcing: SMN station observations

**Full design**: Configurable forcing source for ML model lookback windows — station observations, gridded reanalysis, or archived NWP (see architecture-context.md).

**v0**: Use SMN station observations (hourly, 1981-present) co-located with BAFU river gauges. Simple, immediately available, sufficient for Swiss v0 scale (~170 LINDAS-available BAFU gauges). **→ DECISION (plan 013)**: The binding constraint is how many BAFU gauges have co-located SMN weather stations with sufficient hourly history (1981–present) for ML training — this is a subset of ~170. "~1000 stations" is the multi-deployment architectural ceiling (including non-Swiss deployments where SMN is irrelevant). Swiss v0 is SMN-bounded; non-Swiss v1 deployments use ERA5-Land. The forcing source is injected via adapter dependency, not hardcoded — `prepare_model_inputs()` and training data assembly accept a forcing source parameter (see §I2). **v1**: Switch to ERA5-Land via `WeatherReanalysisSource` Protocol for Nepal.

### A13. Generalized model input container

**Full design**: 4-slot data contract (past_targets, past_dynamic, future_dynamic, static) with ModelDataRequirements declaring per-slot feature needs. GroupModelInputs uses stacked DataFrames for batch ML inference.

**v0**: Implemented (plan 008). `GroupModelInputs` and `stack_model_inputs()` provide the stacked DataFrame container with `for_station()` slicing. `predict_batch()` accepts `GroupModelInputs` in the hindcast path; operational forecast path (Flow 1) will use the same stacking when implemented. past_dynamic and future_dynamic use the same reanalysis source in training/hindcast (future_dynamic filled from reanalysis as teacher forcing). Multi-target predictions supported from day one. v0 exercises this with discharge (river) and water_level (lake) forecasting — skill computation, store filtering, and training orchestration are all parameter-scoped.

**A14. ForecastInterface adapter**

**v0a**: Not needed — `LinearRegressionDaily` implements `StationForecastModel` directly.

**v0b**: Active when FI-compatible ML models are onboarded. The `ForecastInterfaceAdapter`
bridges `hydrosolutions/ForecastInterface` types to SAPPHIRE Flow internals — converting
`ModelOutput` → `ForecastEnsemble` on output, and `GroupModelInputs`/`StationModelInputs`
→ FI input format on input. External dependency: `ForecastInterface` is under active
development; input types are contributed via PR from SAPPHIRE Flow (see plan 014).

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
| Foreign forecast tables (v1) | `foreign_forecasts`, `foreign_forecast_values` — types/protocols defined, DB tables deferred |
| Virtual station formulas (v1) | `calculated_station_formulas` table, DB triggers for component-must-be-gauged invariant (plan 015) |

**Rationale**: Empty "for later" tables add migration maintenance burden and clutter the schema.

---

## C. Database schema (v0 subset)

25 tables. No partitioning, no DLQ, no auth, no cold storage dispatch.

### Reference data
- `parameters` — as designed (canonical parameter names, units, aggregation methods). Seeded via Alembic migration with the 10 canonical parameters defined in `architecture-context.md`.

### Core entities
- `stations` — as designed (without override columns); includes `network`, `ownership`, `wigos_id`, `gauging_status` columns; unique constraint is `(network, code)`; `forecast_targets` is JSONB nullable (NULL for weather stations; e.g. `["discharge"]` or `["discharge","water_level"]`)
- `basins` — as designed; includes `network` column; unique constraint is `(network, code)`
- `station_thresholds` — as designed
- `flow_regime_configs` — as designed

### Observations
- `observations` — as designed but **not partitioned**, drop `rating_curve_id` and `rating_curve_correction_version` columns (v1)

### Models
- `models` — as designed
- `station_groups` — as designed
- `station_group_members` — as designed
- `model_artifacts` — as designed; status enum has all 5 values (`training | pending_approval | active | superseded | rejected`) for forward compatibility, v0 wires 3 transition paths only (see §A7); includes `sha256_hash TEXT NOT NULL` column (OWASP A08 integrity control — see `security.md`)
- `model_assignments` — as designed
- `group_model_assignments` — as designed; records (`group_id`, `model_id`, `time_step`, `status`, `priority`, `created_at`); unique on `(group_id, model_id)`
- `model_states` — as designed
- `station_weather_sources` — as designed

### Forecasts
- `forecasts` — as designed; includes `qc_status` and `qc_flags` columns (migration 0012)
- `forecast_values` — as designed but **not partitioned**
- `forecast_qc_overrides` — per-station QC threshold overrides; unique on `(station_id, rule_id, parameter, time_step_seconds)` (migration 0012)

### Hindcast
- `hindcast_forecasts` — as designed but **not partitioned**; includes `qc_status` and `qc_flags` columns (migration 0012)
- `hindcast_values` — as designed but **not partitioned**

### Weather archive
- `weather_forecasts` — as designed but **not partitioned**, drop gap recovery fields (`is_gap`, `gap_status`)
- `historical_forcing` — as designed; permanent retention (no cold-storage tiering in v0)

### Skill
- `skill_scores` — as designed
- `skill_diagrams` — as designed

### Operational support
- `alerts` — as designed, plus `model_ids` (JSONB, `[]` for observation/pipeline alerts) and `alert_model_strategy` (TEXT, NULL for observation/pipeline alerts) for forecast alert traceability (see §A8d). Keep `notified_at` as always-NULL.
- `pipeline_health` — as designed

### Not created in v0
`dead_letter_queue`, `forecast_adjustments`, `users`, `access_tokens`, `refresh_tokens`, `audit_log`, `rating_curves`, `notification_routing`, `notification_recipients`

---

## D. Performance: fast forecast cycle

Target: full forecast cycle in < 60 seconds at Swiss v0 scale (~170 stations). At the ~1000-station architectural ceiling, the target shifts to per-station budget (< 60 ms/station wall-clock with parallelism) pending benchmarks — see plan 013 Task 3.

### D1. Pre-load model artifacts

Load model artifacts into memory at worker startup (or LRU cache on first use). ML model deserialization can take 10-30s — do it once. `ModelArtifactCache` singleton keyed by `(model_id, artifact_id)`. Pre-warm on startup. Invalidate on artifact promotion.

### D2. Batch database operations

- **Writes**: PostgreSQL `COPY` protocol (asyncpg `copy_to_table()` or Polars `write_database()`) for forecast_values. At ~170 Swiss stations: 170 × 21 members × 120 timesteps = ~429K rows/cycle. At ~1000-station ceiling: 1000 × 21 × 120 = 2.52M rows/cycle. **→ BENCHMARK (plan 013)**: COPY performance at 2.52M rows/cycle is untested; verify before deploying at >500 stations. COPY is 10-50x faster than INSERT.
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

Target per-step budgets (~170 Swiss stations; scale linearly for larger deployments — see plan 013 Task 3 for ~1000-station re-derivation):

| Step | Target (~170 stations) | Target (~1000 stations) | Bottleneck | Scaling |
|------|----------------------|------------------------|-----------|---------|
| 1.1 NWP fetch | 15-30s | 15-30s | Network | Fixed (one grid fetch) |
| 1.3 Spatial extraction | 5s | 5-15s | CPU | O(n) but bulk-extracted in one pass |
| 1.6 Observation fetch | 2s | 5-10s | DB read | O(n) with batch `ANY($1)` |
| 1.7 Prepare inputs | 3s | 10-15s | In-memory | O(n) per station |
| 1.8 Run models (all) | 10-30s | 10-30s | CPU (parallel) | Parallelized via `task.map()` — wall-clock ~ constant if threads available |
| 1.10 Forecast QC | < 1s | 2-5s | In-memory | O(n) per station |
| 1.11 Store results | 3s | 10-30s | DB write (COPY) | O(n) rows; **→ BENCHMARK**: 2.52M rows/cycle via COPY untested. Bottlenecked by `db_bulk_write` slot (see orchestration.md DECISION) |
| 1.12–1.14 Alert checking | < 5s | 5-15s | In-memory | O(n) per station |
| **Total** | **< 60s** | **~60-150s (pending benchmarks)** | | |

**Plan 013 Task 3 notes**: At ~1000 stations, the < 60s headline target is unlikely without chunked fan-out and `max_workers` tuning. The per-station budget target (< 60 ms/station) is a better framing for larger deployments. Steps 1.8 and 1.11 are the binding constraints — 1.8 depends on `ThreadPoolTaskRunner` `max_workers` tuning (see orchestration.md BENCHMARK), 1.11 depends on `db_bulk_write` slot width and COPY throughput. The `db_bulk_write` single-slot bottleneck (orchestration.md lines 164-169) directly constrains step 1.11. Network-bound step 1.1 remains fixed-cost regardless of station count (one grid fetch, bulk extraction).

### D7. API response speed

- TTL cache (30-60s) for station metadata, latest forecast summaries, alert status
- Pre-computed ensemble summary statistics (mean, P10, P25, P50, P75, P90) stored alongside raw members
- `orjson` for serialization (3-10x faster than stdlib json)
- Cursor-based pagination
- gzip/brotli compression in Caddy

---

## E. Testing and CI/CD

> Full CI pipeline, Docker topology, log management, and deployment procedures are in [`docs/standards/cicd.md`](standards/cicd.md). This section covers v0 simplifications only.

### E1. Two-tier test datasets

**Tier 1 — Synthetic (unit tests, no I/O)**: Programmatic generators in `tests/conftest.py`:
`make_observations()`, `make_nwp_forecast()`, `make_station_config()`. Deterministic (seeded RNG), in-memory. Factory variants for edge cases.

**Tier 2 — Real CAMELS-CH (integration/e2e)**: 5-10 BAFU stations, 2 years hourly discharge, corresponding SMN weather, recorded ICON-CH2-EPS for 3-5 cycles. Known edge cases baked in. Known-correct golden answers for regression testing. ~50-100 MB in `tests/fixtures/reference/`.

### E2. Replay adapters

Every external dependency goes through an adapter Protocol. Test replay adapters serve recorded data (gating mechanism is adapter-specific: `ReplayStationAdapter` uses a `simulated_time` callback for upper-bound filtering; `ReplayNwpAdapter` uses presence-based gating — if no fixture exists for the requested `cycle_time`, it raises `AdapterError`):
- `ReplayNwpAdapter` → recorded Zarr fixtures (`GriddedForecast` path only — v0 has only gridded NWP; matches "each adapter returns one concrete type" principle). Plan 021.
- `ReplayStationAdapter` → recorded observation Parquet from fixtures. Plan 020.
- `ReplayForecastInterfaceLoader` → recorded `ModelOutput` fixtures (Parquet + JSON sidecar) for FI-wrapped model testing (test utility, not a Protocol implementor — see Plan 020 Step 3)

Full forecast cycle runs in seconds using recorded data — no network, no waiting.

### E3. Scenario-based integration tests

| Scenario | Edge cases |
|----------|-----------|
| Normal cycle | Happy path baseline |
| Late NWP | Fallback to previous cycle |
| Missing observations | Staleness warning, forecast proceeds (Plan 023 — `assess_input_quality()`) |
| QC failures | Spikes flagged, excluded from model inputs |
| Model failure | Fallback to next priority model |
| Threshold exceedance | Alert raised at correct level |
| Empty ensemble | Skip threshold check, metadata flag |
| Full onboarding → forecast | End-to-end init → operational path |
| Model onboarding (Flow 13) | Register → compatibility → smoke test → train → hindcast → skill gate → promote → assign. Cover: incompatible unit skipped (SKIPPED_COMPAT), smoke test failure (FAILED_SMOKE_TEST), gate rejection (GATE_REJECTED), insufficient eval data (SKIPPED_INSUFFICIENT_EVAL), successful promotion (PROMOTED). |

### E4. Test database

`testcontainers-python` for PostgreSQL + PostGIS per session (~3s startup). Transaction rollback per test for isolation. Real PostgreSQL — no SQLite, no mocks of PostGIS/JSONB/partial indexes.

Target: full integration suite < 60s locally. Individual tests < 5s.

### E5. CI pipeline (GitHub Actions)

Four parallel jobs:
- **lint**: ruff + pyright --strict (< 30s)
- **unit**: pytest tests/unit/ --cov (< 30s, no DB)
- **integration**: real PostgreSQL, replay adapters, scenario tests (< 2 min)
- **e2e**: full pipeline, golden answer comparison (< 5 min)

Total CI wall time: < 5 min.

### E6. Adapter recording tool

CLI to refresh reference dataset from public APIs:
```bash
uv run python -m sapphire_flow.tools.record_fixtures \
  --source bafu \
  --stations tests/fixtures/reference/stations.toml \
  --start 2025-01-01 --end 2025-12-31 \
  --output tests/fixtures/reference/
```

### E7. Performance regression detection

CI records Flow 1 execution time per step. Warns (doesn't fail) on > 50% regression vs baseline in `tests/fixtures/reference/performance_baseline.json`.

---

## F. Infrastructure (v0)

> Full service topology, health checks, volume layout, and systemd integration: [`docs/standards/cicd.md`](standards/cicd.md). Container privilege model and secrets management: [`docs/standards/security.md`](standards/security.md).

**Local development**: Internal service ports (postgres, Prefect UI, API) are not exposed in the base `docker-compose.yml`. Use the dev overlay:
```
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

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

### F1. Config profiles

Switch deployment configuration via `SAPPHIRE_CONFIG` environment variable pointing to a TOML
file. Default `config.toml` at repo root (Swiss profile). Other profiles in `config/` directory
(e.g., `config/uk.toml`). `load_config()` reads `SAPPHIRE_CONFIG` when no path argument is
provided.

---

## G. Types and Protocols (full implementation)

Implement the **full** type system and Protocol definitions from `types-and-protocols.md`. Types are cheap, catch bugs early, and define the contract for all downstream implementation. This includes:

- All ID NewTypes, UtcDatetime, GeoCoord
- All enums (minus deferred ones: UserRole, AuditEventType, AdjustmentType, Calendar); within non-deferred enums, `ObservationSource.COMPONENT_DERIVED` is deferred to v1
- All entity dataclasses (frozen)
- All store Protocols (minus RatingCurveStore, ForecastAdjustmentStore)
- All adapter Protocols (minus NotificationAdapter). v0b adds `ForecastInterfaceAdapter` for FI-wrapped ML models.
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
Phase 4: Services (QC, threshold, alert, skill, forecast input, input quality)  ─┘
          │
Phase 5: Station onboarding (simplified)           ─┐ ✓ done
Phase 6: Observation ingest (Flow 2)               ├─ parallel
Phase 7: Model framework + training                │  ✓ done
Phase 7b: Model onboarding (Flow 13) + sample model─┤  ✓ done
Phase 9: FastAPI REST API                          ─┘
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

v0 is deliberately scoped down from `architecture-context.md`. The Protocol-first architecture makes most v1 additions purely additive (partitioning, PgBouncer, dashboard, notifications, forecast adjustments, Bikram Sambat calendar). Four areas require active guarding during v0 implementation to avoid dead ends:

### I1. Keep spatial type unions in service signatures

v0a starts with point weather forecast data (pre-extracted). v0b+ adds basin-average extraction from gridded NWP (ICON-CH2-EPS via GridExtractor). Nepal v1 needs elevation-band extraction (ECMWF IFS). The `GridExtractor` Protocol already returns `BasinAverageForecast | ElevationBandForecast`, but implementations may be tempted to narrow signatures to just one concrete type.

**Rule**: Any service or flow function that handles weather forecast data must accept the full `WeatherForecastResult` union type (`PointForecast | BasinAverageForecast | ElevationBandForecast`), even if the current v0 phase only produces one variant. Test fakes should exercise multiple variants where feasible.

### I2. Keep forcing source injectable in training and inference

v0 uses SMN station observations for ML model lookback windows (resolved — see §A12). Nepal v1 will use ERA5-Land via `WeatherReanalysisSource`. If `prepare_model_inputs()` or training data assembly hardcodes "fetch from co-located weather station," the entire training/inference pipeline needs rework for v1.

**Rule**: Training data gathering (Flow 6 step T.2) and forecast input preparation (Flow 1 step 1.7) must accept a forcing source dependency (adapter), not directly query a specific data source. The `WeatherReanalysisSource` Protocol exists but is not implemented in v0 — the injection point must still be present.

### I3. Decouple alert-selection priority from fallback priority

`ModelAssignment.priority` is extended in v0 from "fallback order" to also mean "alert-selection priority" (whose ensemble drives alerts when all models succeed). These semantics are consistent today (priority 0 = run first = use for alerts) but could diverge in v1 if a fast-but-less-accurate model gets priority 0 for fallback speed but should not drive alert decisions.

**v1 action:** Add `alert_priority: int | None` to `ModelAssignment` (and `group_model_assignments`). When set, overrides `priority` for alert selection. When NULL, falls back to `priority`. This is an additive, nullable column — safe migration on small data.

### I4. Keep `future_dynamic` extensible for ensemble-aware models

v0 models receive NWP forcing as a 2D DataFrame in the `future_dynamic` slot of `ModelInputs` (timesteps × features). For NWP ensemble propagation (Paradigm A), the flow runs N forward passes through the same model — one per NWP member. This works but precludes **permutation-invariant ensemble processing** (Hohlein et al., AIES 2024), where the model ingests all NWP members simultaneously and learns inter-member relationships (e.g. ensemble agreement as a signal for forecast confidence).

Permutation-invariant processing requires `future_dynamic` to carry a member dimension (members × timesteps × features) — a 3D tensor. If `prepare_model_inputs()` or `ModelInputs` is locked to 2D DataFrames, adding this later requires rework across input preparation, training data assembly, and model Protocols.

**Rule**: Do not add validation that rejects a `member_id` column in `future_dynamic`. When ensemble-aware models are introduced (v0b+ or v1), `ModelDataRequirements` gains an `ensemble_input: bool` field; input preparation passes raw NWP members when `True`, collapsed statistics when `False`. This is an additive change — but only if v0 doesn't accidentally close the door.

**Context**: Paper 0 lit review (§3.7) confirms no ML streamflow model has used permutation-invariant NWP input yet — Hohlein et al. demonstrated it for weather post-processing only. This is a research opportunity, not an immediate need.

### I5. Do not hard-code "all stations are GAUGED" in flow code

v0 operates exclusively with BAFU automatic gauging stations (`gauging_status = GAUGED`). It is tempting to skip the `gauging_status` check and write flow logic that assumes continuous observations are always available.

**Rule**: Flow code that gates on observation availability (e.g. QC dispatch, alert evaluation, model inference scheduling) must branch on `station.gauging_status`, not assume `GAUGED`. Manual stations (per-station `AutomationLevel`, plan 017) and ungauged stations (plan 015) will be introduced in v1. If v0 flow code never consults `gauging_status`, every such code path needs retrofitting before these plans can land.

### Not risks (safe to defer)

| v1 feature | Why safe |
|------------|----------|
| Table partitioning | Additive migration — small data at Swiss v0 scale (~170 stations). At the ~1000-station architectural ceiling (~3.7B forecast_values rows/year), migration is no longer trivial; plan partitioning before exceeding ~500M cumulative rows. See §A1 DECISION (plan 013). |
| Rating curve columns on observations | Nullable column addition (metadata-only in PostgreSQL) |
| Stage 2 QC (2.5–2.7) | Independent flag set, does not change Stage 1 interface |
| Notification dispatch | Reads alerts, does not change alert model |
| Forecast adjustments / Flow 3 | New table + service + API endpoints, no v0 schema conflicts |
| Tiered retention / cold storage | Additive archival task, no schema changes |

---

## J. v0 API endpoints

v0 subset of the full API routes in `conventions.md`. No auth, no forecast adjustments,
no review/publish workflow. Request/response Pydantic schemas are Phase 9 work —
derived from domain dataclasses at implementation time.

```
# v0 API endpoints (no auth, no forecast adjustments)

GET    /api/v1/stations                    # list stations (paginated)
GET    /api/v1/stations/{id}               # station detail
GET    /api/v1/stations/{id}/observations  # observations for station
GET    /api/v1/stations/{id}/forecasts     # forecasts for station

GET    /api/v1/forecasts/{id}              # forecast detail with ensemble values
GET    /api/v1/alerts                      # list alerts (filterable by status, source)
POST   /api/v1/alerts/{id}/acknowledge     # acknowledge an alert

POST   /api/v1/flows/{flow}/trigger        # manually trigger a flow run
GET    /api/v1/health                      # health check + pipeline status
GET    /api/v1/health/detail               # detailed component status (no auth in v0)
# Health checks are live (DB ping, Prefect worker heartbeat) — independent of Flow 4

# Deferred to v1:
# POST   /api/v1/forecasts/{id}/adjust     (no Flow 3)
# PATCH  /api/v1/forecasts/{id}/status     (no review/publish workflow)
# POST   /api/v1/users                     (no auth)
# GET    /api/v1/users                     (no auth)
# PATCH  /api/v1/users/{id}               (no auth)
# POST   /api/v1/access-tokens            (no auth)
# GET    /api/v1/access-tokens            (no auth)
# DELETE /api/v1/access-tokens/{id}       (no auth)
# POST   /api/v1/access-tokens/{id}/regenerate (no auth)
```
