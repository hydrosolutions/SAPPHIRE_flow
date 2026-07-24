# Orchestration Standards

> This document extends `docs/architecture-context.md`. It adds Prefect 3 implementation detail for the 12 data flows (plus Flow 5w) and maintenance tasks. For foundational decisions, see: flow definitions and step sequencing (architecture-context.md § Data flows), Prefect naming conventions (conventions.md § Prefect flows and tasks), retry patterns (conventions.md § Error handling at adapter boundaries), work pool topology and resource limits (cicd.md § Prefect work pool separation), and container layout (cicd.md § Docker Compose service topology). This document does not redefine the tech stack, flow step logic, or data model.
>
> **v0 simplifications**: See [`docs/v0-scope.md`](../v0-scope.md) § A6 (single work pool), § A4 (simplified onboarding), § A7 (simplified artifact lifecycle), § D4 (minimize Prefect overhead). v0 runs **two** pools — the general `default` pool plus a dedicated `ingest` pool served by `prefect-worker-ingest` for obs-feed isolation (Plan 098); only `ingest-observations` routes to `ingest`, everything else stays on `default`. The three-pool ops/training/hindcast topology described below applies to v1.

## Why Prefect 3

Prefect 3 replaces a patchwork of Luigi, bash scripts, and cron jobs with a single orchestration layer. Native Python decorators (`@flow`, `@task`) mean flows are ordinary Python — no DAG compilation step, no separate DSL to learn. Built-in retry logic with configurable backoff, a live observability UI, and work pool isolation (separate worker processes per workload class) are available without additional infrastructure. All scheduling, concurrency enforcement, and run history live in the same system.

## Flow-to-Prefect mapping

| Flow | Prefect flow function | Work pool | Trigger | Concurrency limit | Scope |
|------|-----------------------|-----------|---------|-------------------|-------|
| 1 — Forecast cycle | `run_forecast_cycle_flow` | `ops` | Cron | 1 | v0+v1 |
| 2 — Observation ingest | `ingest_observations_flow` | `ingest` (v0) / `ops` (v1) | Cron | — | v0+v1 |
| 3 — Forecast review | *(not a Prefect flow — user-driven via API/dashboard)* | — | — | — | v0+v1 |
| 4 — Pipeline monitoring | `monitor_pipeline` | `ops` | Cron | — | **v0c+** (§D5) |
| 5 — River station onboarding | `onboard_stations_flow` | `ops` | On-demand | — | v0+v1 |
| 5w — Weather station onboarding | *(not yet implemented — weather station onboarding is currently handled within `onboard_stations_flow` which processes all station kinds; a dedicated `onboard_weather_stations` flow is deferred)* | `ops` | On-demand | — | v0+v1 |
| 6/9 — Model training | `train_models_flow` | `training` | On-demand or scheduled | 1 | v0+v1 |
| 7 — Hindcast generation | `run_hindcast_flow` | `hindcast` | Subflow or on-demand | — | v0+v1 |
| 8/10 — Skill computation | `compute_skills_flow` (deployment) / `compute_skills_task` (fan-out) | `hindcast` | Subflow or on-demand | — | v0+v1 |
| 8/10 — Combined skill computation | `compute_combined_skills_flow` | `hindcast` | On-demand | — | v0+v1 |
| 11 — NWP gap recovery | `recover_nwp_gaps` | `ops` | Event-triggered (from Flow 4) | — | **v0c+** (§D5) |
| 12 — Observation reprocessing | `reprocess_observations` | `ops` | Event-triggered / on-demand | Per-station (see below) | v0+v1 |
| 13 — Model onboarding | `onboard_model_flow` | `training` (v0: `default`) | On-demand | 1 | v0+v1 |
| Backup | `backup_database_flow` | `ops` | Cron (daily) | — | v0+v1 |
| DLQ drain | `drain_dlq` | `ops` | Cron (hourly) | — | **v1** (§A1) |
| Data archival | `archive_cold_data` | `ops` | Cron (monthly) | — | **v1** (§A2) |
| Backup restore rehearsal | `rehearse_backup_restore` | `ops` | Cron (monthly) | — | **v1** (§A10) |

All cron schedules are deployment-configurable — set as `CronSchedule` parameters in each deployment definition, not hardcoded. **→ DECISION (plan 013)**: At ~1000 stations on the `default` work pool (v0), staggering forecast cycles and backup flows is a recommended operational practice to reduce contention. Operators should offset cron schedules to avoid simultaneous heavy flows (e.g., stagger `run_forecast_cycle_flow` and `train_models_flow` by at least 10 minutes). **Plan 098 note**: staggering is no longer the mechanism that protects the `*/5` observation ingest — obs ingest now runs on the dedicated `ingest` pool (`prefect-worker-ingest`), isolated from the heavy `default`-pool flows. Staggering is retained as general contention guidance for the remaining `default`-pool flows. See cicd.md § Prefect work pool separation for pool-level concurrency limits and container resource bounds.

## Task granularity

Use `@task` when:

1. The step crosses a system boundary — database read/write, HTTP call, filesystem access.
2. Independent retry is valuable — e.g. a transient NWP fetch failure should not re-run the entire flow.
3. Observability in the Prefect UI matters — tasks appear as individual run nodes with their own logs and state.

Keep inline (plain function call inside a `@flow` or `@task`) when:

1. The logic is pure computation with no side effects.
2. It is trivial glue that adds no retry or observability value.

One task per side-effect boundary is the guiding rule. Tasks should be idempotent where possible — re-running a task on retry should not create duplicate records or double-write data. See conventions.md § Prefect flows and tasks for function naming (`verb_noun`) and deployment naming (kebab-case).

When a `@task` is itself invoked via `task.map()` at high fan-out (hundreds+ concurrent invocations), inner `@task` decorators on DB-boundary helpers may be removed to avoid Prefect UI saturation. Retry responsibility moves to the outer task.

## Fan-out and convergence

Flow 1 parallelizes forecast execution across stations. Two mechanisms are available:

- `task.map()` — for homogeneous work over a collection (e.g. running the same forecast task for each station/model pair).
- `task.submit()` + gather — for heterogeneous parallel work where tasks differ by inputs or type.

`task.map()` with `unmapped()` store/connection arguments requires an in-process task runner (`ThreadPoolTaskRunner`; note: `ConcurrentTaskRunner` is a backwards-compatibility alias for the same class in Prefect 3.6+). Stores hold SQLAlchemy connections that are not pickle-serializable — distributed or subprocess runners would fail. The `default` pool retains in-process `task.map` fan-out for Flow 1. **Plan 098 note**: the `ingest` pool is a separate worker process (`prefect-worker-ingest`), not in-process with `default`; it serves only the observation-ingest flow, which does no `task.map` fan-out. **→ BENCHMARK (plan 013)**: Default `max_workers` is unbounded (`sys.maxsize`). At ~1000-station fan-out via `task.map()`, this spawns ~1000 concurrent OS threads. Cap via `ThreadPoolTaskRunner(max_workers=N)` or `PREFECT_TASK_RUNNER_THREAD_POOL_MAX_WORKERS` env var. Benchmark thread count limits, memory footprint, and connection pool pressure before deploying at >500 stations.

Illustrative sketch for Flow 1 (not implementation):

```python
# Illustrative only — not implementation
nwp = fetch_nwp(cycle_time)                    # shared work, single task
inputs = prepare_inputs.map(stations, nwp=nwp) # Step 1.7: Prepare model inputs (includes input quality assessment)

# Structure: station → model → parameter_name → ForecastEnsemble
all_ensembles: dict[StationId, dict[ModelId, dict[str, ForecastEnsemble]]] = defaultdict(dict)
# Structure: station → model → priority_value
all_priorities: dict[StationId, dict[ModelId, int]] = defaultdict(dict)

# Group-scoped models (ML): load artifact once, share across stations
for model in group_scoped_models:
    artifact = deserialize_artifact(fetch_active_artifact(group_id, model_id))
    group_results = forecast_station.map(
        [inp for inp in inputs if model assigned],
        artifact=unmapped(artifact),           # same artifact, no re-deserialization
    )

# Station-scoped models (conceptual): per-station artifact
for model in station_scoped_models:
    station_results = forecast_station.map(inputs)  # each loads its own artifact

# Step 1.10: Forecast QC — filter QC-failed forecasts before Phase C
# rule_set, overrides, baselines batch pre-fetched at flow start
for station_id, model_ensembles in all_ensembles.items():
    for model_id, param_ensembles in list(model_ensembles.items()):
        for param, ensemble in list(param_ensembles.items()):
            flags = qc_checker.check(ensemble, rule_set, overrides[station_id], baselines[station_id])
            status = aggregate_qc_status(flags)
            if status == QcStatus.QC_FAILED:
                del all_ensembles[station_id][model_id][param]  # filtered from Phase C

check_station_alerts(all_ensembles, all_thresholds, danger_levels, all_priorities, config, alert_store, clock)  # Phase C (plan 010) — QC-failed ensembles already filtered above
```

**Phase 8 implementation notes for Flow 1:**

- Step 1.7 (`prepare_inputs`) must wire input quality assessment as a sub-step: call `assess_input_quality()` from `services/input_quality.py` and attach the result to the prepared inputs so downstream steps and `forecast.input_quality_assessed` logging have access to it.
- Phase 8 must also design season-aware threshold resolution — thresholds in `InputQualityConfig` may need to vary by season (e.g. stricter thresholds during monsoon). See Plan 023 §Season-aware threshold resolution for options and the bounding invariant.

For group-scoped models, the artifact is deserialized once per model (not per station) and passed to all mapped tasks via Prefect's `unmapped()`. This eliminates deserialization overhead without changing the per-station `predict()` contract.

Individual station failures are caught inside `forecast_station` and logged; the flow continues with remaining stations. Only unexpected failures (e.g. `TypeError`, `AttributeError`) propagate to Prefect as task-level failures. See conventions.md § Flow-level strategy for the full error handling policy.

### Training fan-out

Training parallelization depends on artifact scope:

- **Station-scoped models**: fan-out across `(station, model)` pairs — each pair trains independently. Same as before.
- **Group-scoped models**: fan-out across `(group, model)` pairs. Within a group, T.2 gathers all stations' data, T.3 trains once, then T.4–T.5 (hindcast + skill) fan out per station within the group.

```python
# Illustrative only — group-scoped training
scope = determine_scope(request)               # list of (group, model, period)
for group, model, period in scope:             # parallelizable across groups
    group_data = gather_group_training_data(group, period)  # T.2: all stations
    artifact = model.train(group_data, params, rng)          # T.3: single call
    # T.4-T.5: fan out hindcast + skill per station in group
    for station in group.station_ids:
        run_hindcast(station, model, artifact, period)
        compute_skills_task(station, model, artifact)
```

## Flow composition

Four composition patterns are used across the 12 flows (plus Flow 5w):

**1. Direct subflow call** — a `@flow` calls another `@flow` in-process. Prefect tracks the parent–child relationship in the UI. Used for Flows 6/9 calling Flow 7 (hindcast) and Flows 8/10 (skill) as part of the training pipeline.

> **v1-only** (v0-scope.md §A6): Cross-pool submission requires the three-pool topology. **Plan 098 note**: v0 now runs two pools (`default` + `ingest`), but the `ingest` pool serves only `ingest-observations` (no sub-flow composition), so sub-flows still run in-process on the `default` pool.

**2. Cross-pool submission** — a parent flow submits work to a different work pool via `run_deployment()`. The parent does not block; it polls or waits for the child deployment's run to complete. Used when the training pool (`training`) needs to dispatch hindcast and skill work to the `hindcast` pool after T.3 completes.

**3. Event-triggered** — a flow emits a Prefect event or triggers a deployment asynchronously. Used for Flow 4 → Flow 11: when `monitor_pipeline` detects a recoverable NWP archive gap (step 4.1), it triggers `recover_nwp_gaps` without waiting for it to complete.

**4. Dual-interface (task + flow wrapper)** — a `@task` contains the computation logic and is used with `task.map()` for fan-out. A thin `@flow` wrapper calls the task and preserves standalone deployment registration. Used when the same computation needs both concurrent fan-out (inside a parent flow) and independent invocability. Example: `compute_skills_task` (used via `.map()` inside `train_models`) and `compute_skills_flow` (registered as a standalone deployment for on-demand invocation).

Composition graph:

```
Flow 5 (onboard_stations_flow)
  └→ Flows 6/9 (train_models_flow) [training pool]
       ├→ Flow 7 (run_hindcast_flow) [hindcast pool]
       └→ Flows 8/10 (compute_skills_flow) [hindcast pool]

Flow 13 (onboard_model_flow) [training pool]
  ├→ services/training.py (reused from Flow 6, not the train_models_flow)
  ├→ services/hindcast.py (reused from Flow 7, not run_hindcast_flow)
  └→ services/skill/ (reused from Flow 8, not compute_skills_flow)

Flow 4 (monitor_pipeline)
  └→ Flow 11 (recover_nwp_gaps) [ops pool]

Flow 12 (reprocess_observations) [ops pool, standalone — event-triggered from API actions]
```

Note: T.7–T.8 model approval is NOT a Prefect pause/resume. The `train_models` flow completes after writing a `pending_approval` record *(v1 — v0 auto-promotes per §A7)* and notifying the model admin. Promotion or rejection is a separate API action (`PATCH /api/v1/model-artifacts/{id}/status`) that updates the artifact status independently.

## Scheduling

| Category | Mechanism | Flows | Scope |
|----------|-----------|-------|-------|
| Cron | Prefect `CronSchedule` | 1, 2, 4, backup, DLQ drain, data archival, backup restore rehearsal | 1, 2, backup: v0+v1; 4: **v0c+** (§D5); DLQ drain: **v1** (§A1); data archival: **v1** (§A2); backup restore rehearsal: **v1** (§A10) |
| On-demand | API trigger or manual run from Prefect UI | 5, 6/9, 7, 8/10, 12 | v0+v1 |
| Subflow | Called by parent flow at runtime | 7 (from 6/9), 8/10 (from 6/9) | v0+v1 |
| Event-triggered | Prefect automation or explicit `run_deployment()` call | 11 (from 4), 12 (from API) | 11: **v0c+** (§D5); 12: v0+v1 |

Flows 7 and 8/10 appear in both on-demand and subflow categories: they can be invoked standalone by a model admin (e.g. to recompute skill scores for a specific station) or called as subflows from within `train_models`.

## Concurrency controls

**Per-flow**: `run_forecast_cycle_flow` (Flow 1) has a concurrency limit of 1 — two instances of the same cycle must not run simultaneously. This prevents double-writes on Prefect server restart or accidental duplicate triggers.

**Shared resources**: Use Prefect's `concurrency()` context manager to guard shared resources within a flow. For example, flows that write to the DB in bulk should acquire a named concurrency slot to avoid saturating the connection pool:

```python
with concurrency("db_bulk_write", occupy=1):  # from prefect.concurrency.sync import concurrency
    store.write_batch(records)
```

**→ DECISION (plan 013)**: At ~1000-station fan-out, `occupy=1` serializes all bulk writes — this is the write-throughput ceiling. The slot width is a tuning lever: widen to `occupy=2–4` with a correspondingly sized connection pool when benchmarks confirm safe. For v0 (~170 stations), `occupy=1` is sufficient.

**Per-station write lock**: Flow 12 (`reprocess_observations`) must not overlap with Flow 2 (`ingest_observations`) for the same station and time period. Enforced via `concurrency("observation_write:{station_id}", occupy=1)`. **→ DECISION (plan 013)**: At ~1000 stations during reprocessing events, 1000 active named slots generate Prefect DB I/O for slot acquisition/release. Keep as-is; note this compounds with the 30-day Prefect DB retention concern (cicd.md line 141).

**Pool-level**: See cicd.md § Prefect work pool separation for per-pool default concurrency limits and container resource bounds (`mem_limit`, `cpus`). All limits are deployment-configurable.

## Caching posture

All lifecycle-flow `@task` decorators default to `cache_policy=NO_CACHE` (imported from `prefect.cache_policies`). Prefect 3's default cache policy attempts to hash every task input to compute a cache key; SAPPHIRE stores (PgStore subclasses) hold SQLAlchemy `Connection` references that are neither JSON nor pickle serialisable, so default caching crashes with `HashError`. Operational pipelines rarely hit cache anyway (each run carries distinct `cycle_time` / `period_start` / `station_id` parameters).

A targeted `cache_key_fn` excluding stores is only justified for a pure-compute `@task` with a small, hashable input set and a demonstrated recompute cost — add on a per-task basis with review.

Convention landed via Plan 060 (`docs/plans/archive/060-a3-prefect-deployment-compat-sweep.md`).

## Deployment registration

The `init` service (see cicd.md § First-boot sequence) registers all Prefect deployments on first boot after migrations complete. Each deployment specifies:

- Flow function (imported from `sapphire_flow.flows`)
- Work pool name
- Schedule (`CronSchedule` or none)
- Concurrency limit (where applicable)
- Default parameters (e.g. `mode` for `train_models`)

Deployment names follow conventions.md kebab-case convention: `forecast-cycle`, `ingest-observations`, `monitor-pipeline`, `onboard-stations`, `onboard-weather-stations`, `train-models`, `onboard-model`, `run-hindcast`, `compute-skills`, `compute-combined-skills`, `recover-nwp-gaps`, `reprocess-observations`, `backup-database`, `drain-dlq`, `archive-cold-data`, `rehearse-backup-restore`.

Deployment names for v1-only flows: `drain-dlq`, `archive-cold-data`, `rehearse-backup-restore`.

Registration is idempotent — re-running `init` updates existing deployments rather than creating duplicates.

v0 `init` registers only v0-scoped deployments whose flow functions are implemented. v1-only deployments (`drain-dlq`, `archive-cold-data`, `rehearse-backup-restore`) are registered when the corresponding features are enabled. v0-scoped deployments whose flows are not yet implemented (`onboard-weather-stations`, `reprocess-observations`) are added to the registration script when the flow code lands.

### Rolling-window ingest flows: `ingest-weather-history` and `ingest-recap-reanalysis`

Two deployments share the same **fixed-rolling-window, no-watermark** shape (Plan 071 for the sibling; Plan 146 D1/D2 for the recap-reanalysis snow channel):

- `ingest-weather-history` (`SCHEDULE_INGEST_WEATHER_HISTORY`, default `0 6 * * *`) — MeteoSwiss RhiresD/RprelimD + TabsD/TminD/TmaxD/SrelD, `window_days` default 60.
- `ingest-recap-reanalysis` (`SCHEDULE_INGEST_SNOW_REANALYSIS`, default `0 5 * * *`) — the recap-Gateway JSNOW snow-reanalysis channel (`swe`/`snow_depth`/`snowmelt`), `window_days` default 21 (safely exceeds JSNOW's ~7-day publication lag). Registered unconditionally on every deployment (Swiss included): the flow is MODEL-AGNOSTIC and resolves its own in-scope recap-reanalysis-bound stations each run — on a Swiss deployment (no recap-reanalysis stations bound, no `[adapters.recap_gateway]` section) it resolves zero in-scope stations and returns a benign no-op **before** ever touching Recap config/API key. `concurrency_limit=1`, matching the sibling.

Both run on a **fixed rolling window** `[clock() - window_days, clock())` — **no persisted watermark**. Idempotency comes from `HistoricalForcingStore.store_forcing`'s `on_conflict_do_nothing()` upsert: a re-run over an overlapping window stores zero duplicate physical rows (proven for `ingest-recap-reanalysis` by `tests/integration/flows/test_ingest_recap_reanalysis_pg.py`). Health is measured by EFFECT (a real DB readback showing the store actually gained new data this run), never a fetch-success/`rows_stored` counter — `ingest-weather-history` uses the coarser `fetch_latest_valid_time` (one source-per-product-family); `ingest-recap-reanalysis` uses the finer `fetch_covered_days` per `(station_id, parameter)` because all three snow variables share ONE `source` literal and would otherwise mask a silently-stalled key behind healthy ones.

**Backfill runbook (newly-bound recap-reanalysis station, Plan 146 D2a).** The 21-day rolling window keeps *already-bound* stations current but never acquires history older than `window_days` for a station bound after scheduled ingest is already running. Operator procedure: run the `ingest-recap-reanalysis` deployment once with a wide `window_days` (e.g. `730`) and an explicit `station_ids` subset covering the newly-bound station(s) — the SAME flow entry point, just parametrized wider (mirrors `ingest-weather-history`'s parametric backfill, Plan 082 Task 3B item 4). The backfill window must cover **at least the required lookback PLUS the JSNOW publication lag (~7 days) PLUS an overlap margin**, rounded up to whole days — the exact sizing derivation (from a model's `data_requirements.lookback_steps × time_step`) is **Plan 139's to specify and own**, not restated here. This runbook ships the MECHANISM only: **146 does not enforce that backfill completes to a model's required depth before that model is marked operationally active** — that before-operational / depth-sufficiency guarantee is an explicit Plan 139 / onboarding-flow follow-on.

## Run naming

### Why

Prefect assigns random slug names (`loyal-parakeet`, `jolly-octopus`) to every flow run and task run by default. On the dashboard this makes it impossible to tell which `forecast-cycle` run is the 06Z vs 12Z cycle, or which of a thousand fanned-out station tasks failed. Templating `flow_run_name` and `task_run_name` on each decorator makes runs self-identifying in the Prefect UI, searchable by text filter, and chronologically sortable.

### Template shape (D1)

Every run-name template follows:

```
<flow-kebab-name>-<time-or-shard>-<secondary-shard>
```

- **Lead with the flow's kebab-case name** so dashboard text-filter grouping still matches and existing saved filters keep working.
- **Time axis** is formatted ISO-style `%Y-%m-%dT%H` (or `%Y%m%d` for date-only periods) so lexicographic sort equals chronological sort.
- **Distinguishing shard key** last — `station_id`, `model_id`, `member_id`, `group_id`, `parameter`.
- Target length **≤60 chars** so names survive dashboard truncation.

### Syntax

Two forms are supported. **Strings** for the common case where every placeholder is resolved by the time the run starts. **Callables** for flows where a templating parameter is `Optional` at the flow signature (e.g. `cycle_time=None` resolved from `clock()` inside the flow body) — a string template would stringify `None` as the literal `"None"`.

**String template (tasks, and flows with non-Optional params):**

```python
@task(task_run_name="ingest-obs-{window_end:%Y-%m-%dT%H}")
def _fetch_observations_task(window_end: UtcDatetime, ...): ...
```

**Callable template** (for `Optional` flow params; define one closure per flow):

```python
from prefect import runtime

def _resolve_forecast_run_name() -> str:
    params = runtime.flow_run.parameters or {}
    cycle_time = params.get("cycle_time") or runtime.flow_run.scheduled_start_time
    return f"forecast-{cycle_time:%Y-%m-%dT%H}"

@flow(name="forecast-cycle", flow_run_name=_resolve_forecast_run_name)
def run_forecast_cycle_flow(cycle_time: UtcDatetime | None = None, ...): ...
```

Task callables follow the same pattern using `runtime.task_run.parameters` and `runtime.task_run.scheduled_start_time`. All callables must be stateless — they may only read the runtime context, never close over mutable state.

### Forbidden substitutions (D5)

Only **scalar identifiers** may appear inside run-name templates. Do **not** substitute complex objects; their `__repr__` / `__format__` can render memory addresses, leak secrets from attribute trees, or change between library versions. The following parameter names are blacklisted as direct substitutions:

- `store` (store handle — complex object)
- `adapter` (adapter instance — complex object)
- `unit` **when used as a bare substitution** (complex object). Attribute access such as `unit.station_id` or `unit.group_id` **is allowed** because those attributes are scalars; see the callable examples below.
- `rng` (RNG instance)
- `clock` (clock callable)
- `model` (model instance — complex object)
- `deployment_config` (config object — nested structure)

Scalars that **are** always fine: `station_id`, `group_id`, `model_id`, `artifact_id`, `parameter`, `member_id`, `period_start`, `period_end`, `cycle_time`, `window_end`, `since`. Enums resolve via their `__str__` / `__format__`, which is stable — enum values such as `strategy` are allowed scalars even though they aren't primitives.

### Canonical templates

The table below fixes the run-name template for every `@flow` and `@task` site under `src/sapphire_flow/flows/`. Phase 2 subagents apply these verbatim. "callable" means a small stateless closure that reads `prefect.runtime.flow_run` (or `task_run`) to pick between a named parameter and the scheduled start time — follow the canonical closure shown above in the Syntax section.

| Module | Decorator site | Kind | Run-name template |
|---|---|---|---|
| `run_forecast_cycle.py` | `run_forecast_cycle_flow` | @flow | callable → `forecast-{cycle_time or scheduled_start:%Y-%m-%dT%H}` |
| `run_forecast_cycle.py` | `_fetch_nwp_task` | @task | `fetch-nwp-{cycle_time:%Y-%m-%dT%H}` |
| `run_forecast_cycle.py` | `_fetch_obs_timestamps_task` | @task | `fetch-obs-ts` |
| `ingest_observations.py` | `ingest_observations_flow` | @flow | callable → `ingest-obs-{scheduled_start:%Y-%m-%dT%H}` |
| `ingest_observations.py` | `_fetch_observations_task` | @task | `fetch-observations-{since:%Y-%m-%dT%H}` — if `since` is a dict, use a callable that picks the earliest value, falling back to `runtime.task_run.scheduled_start_time` |
| `ingest_observations.py` | `_store_raw_task` | @task | `store-raw-observations` |
| `ingest_observations.py` | `_run_qc_task` | @task | `run-qc-{station_id}-{parameter}` |
| `ingest_observations.py` | `_derive_calculated_task` | @task | `derive-calculated-stations` — Plan 015 step 2.5. Sequential post-QC step (NOT a `task.map` fan-out): the QC loop finishing is the barrier. Reads calculated stations' components' just-QC'd observations and writes `component_derived` / `missing` rows. No-op when there are no calculated stations. |
| `train_models.py` | `train_models_flow` | @flow | callable → `train-{period_start or scheduled_start:%Y%m%d}-{period_end:%Y%m%d}` (both may be `None`; callable handles) |
| `train_models.py` | `_determine_scope_task` | @task | `determine-scope` |
| `train_models.py` | `_assemble_data_task` | @task | callable → `assemble-data-{unit.station_id or unit.group_id}` [1] |
| `train_models.py` | `_train_model_task` | @task | callable → `train-model-{unit.station_id or unit.group_id}` [1] |
| `train_models.py` | `_store_artifact_task` | @task | callable → `store-artifact-{unit.station_id or unit.group_id}` [1] |
| `onboard_model.py` | `onboard_model_flow` | @flow | callable → `onboard-{model_id}-{period_start or scheduled_start:%Y%m%d}` |
| `onboard_model.py` | `_determine_onboarding_scope_task` | @task | `determine-onboarding-scope-{model_id}` |
| `onboard_model.py` | `_register_model_class_task` | @task | `register-model-class-{model_id}` |
| `onboard_model.py` | `_validate_compatibility_task` | @task | callable → `validate-compat-{model_id}-{unit.station_id or unit.group_id}` [1] |
| `onboard_model.py` | `_smoke_test_model_task` | @task | `smoke-test-model` |
| `onboard_model.py` | `_assemble_onboarding_data_task` | @task | callable → `assemble-onboarding-data-{unit.station_id or unit.group_id}` [1] |
| `onboard_model.py` | `_train_onboarding_model_task` | @task | callable → `train-onboarding-model-{unit.station_id or unit.group_id}` [1] |
| `onboard_model.py` | `_store_onboarding_artifact_task` | @task | callable → `store-onboarding-artifact-{unit.station_id or unit.group_id}` [1] |
| `onboard_model.py` | `_evaluate_skill_gate_task` | @task | `evaluate-skill-gate-{model_id}-{artifact_id}` |
| `onboard_model.py` | `_promote_artifact_task` | @task | callable → `promote-artifact-{unit.station_id or unit.group_id}-{artifact_id}` [1] |
| `onboard_model.py` | `_create_assignment_task` | @task | callable → `create-assignment-{model_id}-{unit.station_id or unit.group_id}` [1] |
| `run_hindcast.py` | `run_hindcast_flow` | @flow | callable → `hindcast-{model_id}-{period_start or scheduled_start:%Y%m%d}-{period_end:%Y%m%d}` |
| `run_hindcast.py` | `_run_station_hindcast_task` | @task | `hindcast-station-{model_id}-{station_id}` |
| `run_hindcast.py` | `_run_group_hindcast_task` | @task | `hindcast-group-{model_id}-{group.id}` |
| `compute_skills.py` | `compute_skills_flow` | @flow | `compute-skills-{model_id}-{station_id}-{parameter}` |
| `compute_skills.py` | `compute_combined_skills_flow` | @flow | `compute-combined-skills-{station_id}-{parameter}-{strategy.value}` |
| `compute_skills.py` | `compute_skills_task` | @task | `compute-skills-{model_id}-{station_id}-{parameter}` |
| `compute_skills.py` | `compute_combined_skills_task` | @task | `compute-combined-skills-{station_id}-{parameter}-{strategy.value}` |
| `backup.py` | `backup_database_flow` | @flow | callable → `backup-{scheduled_start:%Y-%m-%dT%H%M}` |
| `backup.py` | `dump_database_task` | @task | callable → `dump-db-{scheduled_start:%Y-%m-%dT%H%M}` |
| `backup.py` | `cleanup_old_backups_task` | @task | `cleanup-old-backups` |
| `onboard.py` | `onboard_stations_flow` | @flow | callable → `onboard-stations-{scheduled_start:%Y-%m-%dT%H%M}` |
| `onboard.py` | `_download_task` | @task | `download-camels-ch` |

[1] `TrainingUnit` (see `src/sapphire_flow/types/training.py`) carries both `station_id: StationId | None` and `group_id: StationGroupId | None`, with the `__post_init__` invariant that exactly one is set. Subagents implementing `train_models.py` and `onboard_model.py` must use `unit.station_id if unit.station_id is not None else unit.group_id` inside the callable — do **not** substitute the bare `unit` object (forbidden by D5). Enum values such as `strategy` in `compute_combined_skills_flow` render via their `__str__` and are allowed scalars.

### Coverage rule

Every run-name template must be covered by `tests/unit/flows/test_run_names.py` (see Plan 050 Task 2). A template that doesn't resolve against at least one real call-site parameter set will fail only at dashboard-render time in production — the unit test catches typos and missing params before deploy.
