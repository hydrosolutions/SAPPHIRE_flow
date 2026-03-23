# Orchestration Standards

> This document extends `docs/architecture-context.md`. It adds Prefect 3 implementation detail for the 12 data flows (plus Flow 5w) and maintenance tasks. For foundational decisions, see: flow definitions and step sequencing (architecture-context.md § Data flows), Prefect naming conventions (conventions.md § Prefect flows and tasks), retry patterns (conventions.md § Error handling at adapter boundaries), work pool topology and resource limits (cicd.md § Prefect work pool separation), and container layout (cicd.md § Docker Compose service topology). This document does not redefine the tech stack, flow step logic, or data model.
>
> **v0 simplifications**: See [`docs/v0-scope.md`](../v0-scope.md) § A6 (single work pool), § A4 (simplified onboarding), § A7 (simplified artifact lifecycle), § D4 (minimize Prefect overhead). v0 uses a single `default` pool — the three-pool topology described below applies to v1.

## Why Prefect 3

Prefect 3 replaces a patchwork of Luigi, bash scripts, and cron jobs with a single orchestration layer. Native Python decorators (`@flow`, `@task`) mean flows are ordinary Python — no DAG compilation step, no separate DSL to learn. Built-in retry logic with configurable backoff, a live observability UI, and work pool isolation (separate worker processes per workload class) are available without additional infrastructure. All scheduling, concurrency enforcement, and run history live in the same system.

## Flow-to-Prefect mapping

| Flow | Prefect flow function | Work pool | Trigger | Concurrency limit | Scope |
|------|-----------------------|-----------|---------|-------------------|-------|
| 1 — Forecast cycle | `run_forecast_cycle` | `ops` | Cron | 1 | v0+v1 |
| 2 — Observation ingest | `ingest_observations` | `ops` | Cron | — | v0+v1 |
| 3 — Forecast review | *(not a Prefect flow — user-driven via API/dashboard)* | — | — | — | v0+v1 |
| 4 — Pipeline monitoring | `monitor_pipeline` | `ops` | Cron | — | **v0c+** (§D5) |
| 5 — River station onboarding | `onboard_station` | `ops` | On-demand | — | v0+v1 |
| 5w — Weather station onboarding | `onboard_weather_stations` | `ops` | On-demand | — | v0+v1 |
| 6/9 — Model training | `train_models` | `training` | On-demand or scheduled | 1 | v0+v1 |
| 7 — Hindcast generation | `run_hindcast` | `hindcast` | Subflow or on-demand | — | v0+v1 |
| 8/10 — Skill computation | `compute_skills` | `hindcast` | Subflow or on-demand | — | v0+v1 |
| 11 — NWP gap recovery | `recover_nwp_gaps` | `ops` | Event-triggered (from Flow 4) | — | **v0c+** (§D5) |
| 12 — Observation reprocessing | `reprocess_observations` | `ops` | Event-triggered / on-demand | Per-station (see below) | v0+v1 |
| Backup | `backup_database` | `ops` | Cron (daily) | — | v0+v1 |
| DLQ drain | `drain_dlq` | `ops` | Cron (hourly) | — | **v1** (§A1) |
| Data archival | `archive_cold_data` | `ops` | Cron (monthly) | — | **v1** (§A2) |
| Backup restore rehearsal | `rehearse_backup_restore` | `ops` | Cron (monthly) | — | **v1** (§A10) |

All cron schedules are deployment-configurable — set as `CronSchedule` parameters in each deployment definition, not hardcoded. See cicd.md § Prefect work pool separation for pool-level concurrency limits and container resource bounds.

## Task granularity

Use `@task` when:

1. The step crosses a system boundary — database read/write, HTTP call, filesystem access.
2. Independent retry is valuable — e.g. a transient NWP fetch failure should not re-run the entire flow.
3. Observability in the Prefect UI matters — tasks appear as individual run nodes with their own logs and state.

Keep inline (plain function call inside a `@flow` or `@task`) when:

1. The logic is pure computation with no side effects.
2. It is trivial glue that adds no retry or observability value.

One task per side-effect boundary is the guiding rule. Tasks should be idempotent where possible — re-running a task on retry should not create duplicate records or double-write data. See conventions.md § Prefect flows and tasks for function naming (`verb_noun`) and deployment naming (kebab-case).

## Fan-out and convergence

Flow 1 parallelizes forecast execution across stations. Two mechanisms are available:

- `task.map()` — for homogeneous work over a collection (e.g. running the same forecast task for each station/model pair).
- `task.submit()` + gather — for heterogeneous parallel work where tasks differ by inputs or type.

Illustrative sketch for Flow 1 (not implementation):

```python
# Illustrative only — not implementation
nwp = fetch_nwp(cycle_time)                    # shared work, single task
inputs = prepare_inputs.map(stations, nwp=nwp) # fan-out per station

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

check_thresholds(all_results)                  # converge
```

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
        compute_skills(station, model, artifact)
```

## Flow composition

Three composition patterns are used across the 12 flows (plus Flow 5w):

**1. Direct subflow call** — a `@flow` calls another `@flow` in-process. Prefect tracks the parent–child relationship in the UI. Used for Flows 6/9 calling Flow 7 (hindcast) and Flows 8/10 (skill) as part of the training pipeline.

> **v1-only** (v0-scope.md §A6): Cross-pool submission requires the three-pool topology. v0 uses a single pool — sub-flows run in-process.

**2. Cross-pool submission** — a parent flow submits work to a different work pool via `run_deployment()`. The parent does not block; it polls or waits for the child deployment's run to complete. Used when the training pool (`training`) needs to dispatch hindcast and skill work to the `hindcast` pool after T.3 completes.

**3. Event-triggered** — a flow emits a Prefect event or triggers a deployment asynchronously. Used for Flow 4 → Flow 11: when `monitor_pipeline` detects a recoverable NWP archive gap (step 4.1), it triggers `recover_nwp_gaps` without waiting for it to complete.

Composition graph:

```
Flow 5 (onboard_station)
  └→ Flows 6/9 (train_models) [training pool]
       ├→ Flow 7 (run_hindcast) [hindcast pool]
       └→ Flows 8/10 (compute_skills) [hindcast pool]

Flow 4 (monitor_pipeline)
  └→ Flow 11 (recover_nwp_gaps) [ops pool]

Flow 12 (reprocess_observations) [ops pool, standalone — event-triggered from API actions]
```

Note: T.7–T.8 model approval is NOT a Prefect pause/resume. The `train_models` flow completes after writing a `pending_approval` record *(v1 — v0 auto-promotes per §A7)* and notifying the model admin. Promotion or rejection is a separate API action (`PATCH /api/v1/model-artifacts/{id}/status`) that updates the artifact status independently.

## Scheduling

| Category | Mechanism | Flows | Scope |
|----------|-----------|-------|-------|
| Cron | Prefect `CronSchedule` | 1, 2, 4, backup, DLQ drain, data archival, backup restore rehearsal | 1, 2, backup: v0+v1; 4: **v0c+** (§D5); DLQ drain: **v1** (§A1); data archival: **v1** (§A2); backup restore rehearsal: **v1** (§A10) |
| On-demand | API trigger or manual run from Prefect UI | 5, 5w, 6/9, 7, 8/10, 12 | v0+v1 |
| Subflow | Called by parent flow at runtime | 7 (from 6/9), 8/10 (from 6/9) | v0+v1 |
| Event-triggered | Prefect automation or explicit `run_deployment()` call | 11 (from 4), 12 (from API) | 11: **v0c+** (§D5); 12: v0+v1 |

Flows 7 and 8/10 appear in both on-demand and subflow categories: they can be invoked standalone by a model admin (e.g. to recompute skill scores for a specific station) or called as subflows from within `train_models`.

## Concurrency controls

**Per-flow**: `run_forecast_cycle` (Flow 1) has a concurrency limit of 1 — two instances of the same cycle must not run simultaneously. This prevents double-writes on Prefect server restart or accidental duplicate triggers.

**Shared resources**: Use Prefect's `concurrency()` context manager to guard shared resources within a flow. For example, flows that write to the DB in bulk should acquire a named concurrency slot to avoid saturating the connection pool:

```python
async with concurrency("db_bulk_write", occupy=1):
    store.write_batch(records)
```

**Per-station write lock**: Flow 12 (`reprocess_observations`) must not overlap with Flow 2 (`ingest_observations`) for the same station and time period. Enforced via `concurrency("observation_write:{station_id}", occupy=1)`.

**Pool-level**: See cicd.md § Prefect work pool separation for per-pool default concurrency limits and container resource bounds (`mem_limit`, `cpus`). All limits are deployment-configurable.

## Deployment registration

The `init` service (see cicd.md § First-boot sequence) registers all Prefect deployments on first boot after migrations complete. Each deployment specifies:

- Flow function (imported from `sapphire_flow.flows`)
- Work pool name
- Schedule (`CronSchedule` or none)
- Concurrency limit (where applicable)
- Default parameters (e.g. `mode` for `train_models`)

Deployment names follow conventions.md kebab-case convention: `run-forecast-cycle`, `ingest-observations`, `monitor-pipeline`, `onboard-station`, `onboard-weather-stations`, `train-models`, `run-hindcast`, `compute-skills`, `recover-nwp-gaps`, `reprocess-observations`, `backup-database`, `drain-dlq`, `archive-cold-data`, `rehearse-backup-restore`.

Deployment names for v1-only flows: `drain-dlq`, `archive-cold-data`, `rehearse-backup-restore`.

Registration is idempotent — re-running `init` updates existing deployments rather than creating duplicates.

v0 `init` registers only v0-scoped deployments. v1-only deployments (`drain-dlq`, `archive-cold-data`, `rehearse-backup-restore`) are registered when the corresponding features are enabled.
