# Architecture Context

Read this first before working on any task.

## What SAPPHIRE Flow does

Operational hydrological forecasting system. Ingests historical and real-time weather forecasts and
weather station as well as river observations, runs ensemble ML models, checks flood thresholds, and
serves results via REST API. Forecasters review and publish forecasts multiple 
times a day. Runs on Docker Compose on a single VM.

## Data flows

### Operational (recurring, scheduled)

1. **Weather ingest → post-process → forecast → alert → API**
   Fetch latest NWP ensembles → archive raw extractions → apply post-processing (bias correction, ensemble calibration — pass-through until sufficient archive) → fetch latest river/meteo observations → run forecast models → check flood thresholds → raise/resolve alerts → notify → serve via API

2. **Observation ingest → QC → observation alerts**
   Fetch latest station observations → quality control → check thresholds against observed values → raise/resolve alerts

3. **Forecast review → publish → bulletin**
   Dashboard shows forecasts + visualizations → forecaster reviews, optionally adjusts values → selects preferred model → publishes → adjustments recorded with forecaster ID and rationale → generate Excel bulletin

4. **Pipeline monitoring (watchdog)**
   Track each cycle's completion status → detect data source outages, late NWP deliveries, missing observations, stale forecasts → alert operations team (distinct from flood alerts) → log pipeline health metrics for diagnostics

### Initialization (on-demand)

5. **Station onboarding**
   Add new station to system → import historical observations → configure model assignments and weather source mappings

6. **Model training** → same as Flow 9 with `mode=initial` (see Flows 6 & 9 refinement)

7. **Hindcast generation**
   Run forecast models over a historical period for a given station/model combination. Used for: onboarding validation, model comparison, post-retraining verification, ongoing skill tracking.

8. **Skill computation** → same as Flow 10 with narrow scope (see Flows 8 & 10 refinement)

### Maintenance (yearly or on-demand)

9. **Model retraining** → same flow as Flow 6 with `mode=retrain` (see Flows 6 & 9 refinement)

10. **Skill recomputation** → same flow as Flow 8 with broad scope (see Flows 8 & 10 refinement)

11. **NWP archive management**
    Maintain archive of extracted NWP values (basin-average or point, not raw GRIB2). Permanent retention. Housekeeping: verify completeness, flag gaps, manage storage.

Other potential maintenance needs (TBD):
- Observation gap-filling between operational cycles
- Database partition management

---

## Data flow refinements

### Flow 1 — Forecast cycle

```
Trigger:  Prefect schedule (after NWP availability, e.g. every 6 h)
Flow:     run_forecast_cycle
Layer:    flows/ — orchestration only, delegates to services/adapters
```

#### Steps

| # | Step | Layer | Input | Output |
|---|------|-------|-------|--------|
| 1.1 | Fetch NWP forcing data | `adapters/` | NWP source config, cycle time | NWP forcing time series (ensemble or deterministic) |
| 1.2 | Extract spatial averages | `preprocessing/` | Raw grids, basin/band geometries | Per-basin (or elevation-band), per-member time series |
| 1.3 | Archive NWP extractions | `store/` | Extracted time series | Persisted to `weather_forecasts` |
| 1.4 | Post-process NWP | `services/` | Archived extractions, historical archive | Bias-corrected / calibrated forcing |
| 1.5 | Fetch latest observations | `store/` | Station configs, lookback window | Recent QC'd river + meteo observations |
| 1.6 | Prepare model inputs | `services/` | Post-processed NWP, observations, station configs | Per-station input bundles |
| 1.7 | Run forecast models | `models/` | Input bundles, model artifacts | Ensemble forecast values |
| 1.8 | Post-process forecasts | `services/` | Raw forecast ensembles, historical archive | Bias-corrected forecast ensembles |
| 1.9 | Store forecast results | `store/` | Forecast ensembles + model artifact version | Persisted to `forecasts` + `forecast_values` (status = `raw`) |
| 1.10 | Check flood thresholds | `services/` | Forecast ensembles, threshold config | Exceedance flags per station/level |
| 1.11 | Raise / resolve alerts | `services/` | Exceedance flags, existing alerts | New/updated alert records |
| 1.12 | Notify | `services/` | New/changed alerts | Notifications dispatched |

Steps 1.2, 1.3, 1.4, and 1.8 are **conditional** — see notes.

#### Notes

- **1.1**: Generic NWP fetch. May return raw grids (requiring 1.2) or pre-extracted basin/point values (when upstream SAPPHIRE Data Gateway handles extraction).
- **1.2** *(conditional)*: Only needed when 1.1 returns raw grids. Skipped when a SAPPHIRE Data Gateway provides pre-extracted data. v0: GridExtractor on ICON-CH2-EPS.
- **1.3** *(conditional)*: Only needed when no upstream gateway handles archiving. Archive happens *before* post-processing so raw extracted values are preserved. Permanent retention.
- **1.4**: NWP *input* bias correction / ensemble calibration. Pass-through until sufficient archive (6–12 months). Distinct from forecast output correction in 1.8.
- **1.5**: Reads QC'd observations from the store. Flow 2 (observation ingest + QC) runs on its own schedule (e.g. every 30 min) — this step reads the *result*, not raw station feeds. The two flows are decoupled. Future option: add a top-up QC call at the start of Flow 1 to guarantee freshness (trivial — reuses the same QC service function).
- **1.7**: Parallelizable across stations. On model failure, falls back to next assigned model (detail in future iteration).
- **1.8** *(conditional)*: Forecast *output* bias correction (discharge / water level). Distinct from NWP input correction in 1.4. Pass-through when not configured.
- **1.10**: Probability-based: P(Q > threshold) for each danger level.
- **1.11**: Deduplication via partial unique index. Auto-resolves when exceedance no longer holds.
- **1.9**: Each forecast record links to the model artifact version that produced it. Enables traceability across model updates.
- **1.12**: Async. Failed notifications retried by sweep task (every 5 min).
- **API serving**: No explicit step — the API reads persisted results from the DB. Storing in 1.9 makes forecasts available; publishing happens via Flow 3 (forecast review).

#### Open decision: when to check thresholds

Threshold checking (1.10–1.12) can run:
- **On raw forecasts** (immediately after 1.9) — gives early warning before forecaster review.
- **On published forecasts** (after forecaster edits in Flow 3) — alerts reflect human-reviewed values.
- **Both** — initial check on raw, re-check after publication.

This is configurable. To be validated with hydromet operations staff. Flow 3 must support re-triggering 1.10–1.12 after edits regardless of chosen mode.

#### Sequencing

```
1.1 → [1.2] → [1.3] → 1.4 ─┐
                              ├→ 1.6 → 1.7 → [1.8] → 1.9 → 1.10 → 1.11 → 1.12
1.5 ─────────────────────────┘
```

Brackets denote conditional steps. The NWP pipeline (1.1–1.4) and observation fetch (1.5) run in parallel, then join at 1.6 (prepare model inputs).

### Flow 2 — Observation ingest + QC

```
Trigger:  Prefect schedule (e.g. every 30 min)
Flow:     ingest_observations
Layer:    flows/ — orchestration only, delegates to services/adapters
```

#### Steps

| # | Step | Layer | Input | Output |
|---|------|-------|-------|--------|
| 2.1 | Fetch latest station observations | `adapters/` | Station configs, last-seen timestamps | Raw river + meteo observations |
| 2.2 | Store raw observations | `store/` | Raw observations | Persisted to `observations` (status = `raw`) |
| 2.3 | Run quality control | `services/` | Raw observations, QC rule config | QC flags per value |
| 2.4 | Store QC results | `store/` | QC flags | Updated flags/status on `observations` rows |
| 2.5 | Check observation thresholds | `services/` | QC-passed observations, threshold config | Exceedance flags per station/level |
| 2.6 | Raise / resolve alerts | `services/` | Exceedance flags, existing alerts | New/updated alert records |
| 2.7 | Notify | `services/` | New/changed alerts | Notifications dispatched |

#### Notes

- **2.1**: River and weather fetches are independent adapters — run in parallel. v0: BAFU (river) + SMN (weather). Incremental: uses last-seen timestamp per station to fetch only new data.
- **2.2–2.4**: Single `observations` table. Raw values are stored first (2.2), then QC adds flags/status in place (2.4). Raw values are never overwritten — QC is metadata on the observation, not a replacement. Flagged values are excluded from downstream use (forecasting in Flow 1 step 1.5).
- **2.3**: QC rules TBD in detail (range checks, rate-of-change, spatial consistency). Grows in sophistication over time.
- **2.5**: Direct comparison of observed value against threshold — simpler than Flow 1's probability-based check.
- **2.6–2.7**: Same alerting service as Flow 1 but with `source = observation`. Deduplication and auto-resolution work identically.
- **Relationship to Flow 1**: Flow 1 step 1.5 reads QC-passed observations from the store. The two flows are decoupled — Flow 2's schedule drives observation freshness.

#### Future: manual observation correction (v1+, low priority)

Phase 1: Dashboard page where operators can manually flag individual observation values (mark as suspect/invalid). Phase 2: Operators can edit observation values with tracked changes — each edit recorded with editor ID, timestamp, and rationale (same pattern as forecast adjustments in Flow 3). Not in scope for v0.

#### Sequencing

```
2.1 → 2.2 → 2.3 → 2.4 → 2.5 → 2.6 → 2.7
```

Fully sequential at the step level. Within 2.1, river and weather fetches run in parallel. Steps 2.2–2.5 are parallelizable across stations.

### Flow 3 — Forecast review + publish (not in v0 scope)

```
Trigger:  User-driven (forecaster action on dashboard)
Layer:    dashboard/ + api/ → services/ → store/
```

Not a Prefect flow — a sequence of user interactions via the dashboard, backed by API endpoints and services. Not in scope for v0 (no dashboard). Required from v1.

#### Steps

| # | Step | Actor | Input | Output |
|---|------|-------|-------|--------|
| 3.1 | Display forecast cycle | `dashboard/` | Cycle time, station list | Visualizations: ensemble spread, model comparison |
| 3.2 | Adjust forecast values | `api/` → `services/` | Forecaster edits + rationale | Adjustment record (append-only) |
| 3.3 | Review (select model + confirm) | `api/` → `services/` | Model choice per station | Forecast status → `reviewed` |
| 3.4 | Publish forecasts | `api/` → `services/` | Forecaster confirmation | Forecast status → `published` |
| 3.5 | Re-check flood thresholds | `services/` | Published (possibly adjusted) ensembles | Updated exceedance flags |
| 3.6 | Raise / resolve alerts | `services/` | Exceedance flags, existing alerts | New/updated alert records |
| 3.7 | Notify | `services/` | New/changed alerts | Notifications dispatched |
| 3.8 | Generate bulletin | `bulletin/` | Published forecasts | Excel file |

#### Notes

- **3.1**: Read-only. Shows all models that ran for a station so the forecaster can compare.
- **3.2**: Optional. Each adjustment is an immutable record (forecaster ID, timestamp, rationale). Original model output is never overwritten. Multiple adjustments can be made before publishing.
- **3.3**: Review combines model selection and confirmation into one action. Forecaster picks the preferred model per station; status moves to `reviewed`. Optimistic locking on status transitions.
- **3.4**: Publishes selected forecasts. Only `published` forecasts appear in the public API and bulletins.
- **3.5–3.7**: Re-triggers the same threshold/alert logic from Flow 1 (steps 1.10–1.12) on the published values. Always runs here regardless of whether Flow 1 also checked on raw (see Flow 1 open decision).
- **3.8**: On-demand — forecaster explicitly requests bulletin generation after publishing.

#### Open decisions

- **Batch vs per-station publish**: Does the forecaster publish one station at a time or an entire cycle at once? Assumed per-cycle (review all, then publish batch). Needs confirmation with hydromet operations staff.
- **Forecast status transitions**: See open discussion below.

#### Sequencing

```
3.1 → 3.2 → 3.3 → 3.4 → 3.5 → 3.6 → 3.7
                           ↘ 3.8
```

Steps 3.5–3.7 (threshold re-check) and 3.8 (bulletin) can run in parallel after publication. Steps 3.2 and 3.3 are interactive and may repeat before 3.4.

---

### Resolved: forecast status transitions

```
raw → reviewed → published
```

- **`raw`**: Model output stored by Flow 1. No human interaction yet.
- **`reviewed`**: Forecaster has selected the preferred model per station (and optionally adjusted values). This is an explicit action — the forecaster confirms their review.
- **`published`**: Forecaster publishes. Only `published` forecasts appear in the public API and bulletins.

`selected` was dropped — model selection is part of the review action, not a separate status. Adjustments (3.2) are optional and recorded as append-only audit records, independent of status.

### Flow 4 — Pipeline monitoring (watchdog)

```
Trigger:  Prefect schedule (e.g. every 10 min)
Flow:     monitor_pipeline
Layer:    flows/ — orchestration only, delegates to services
```

Meta-flow — monitors the health of Flows 1 and 2 rather than processing data. Can start in v0 (basic); full implementation is a v1 deliverable.

#### Steps

| # | Step | Layer | Input | Output |
|---|------|-------|-------|--------|
| 4.1 | Check NWP delivery status | `services/` | Expected NWP schedule, `weather_forecasts` table | On-time / late / missing per NWP cycle |
| 4.2 | Check observation freshness | `services/` | Station configs, `observations` table | Per-station: last received, overdue flag |
| 4.3 | Check forecast freshness | `services/` | Expected forecast schedule, `forecasts` table | Last successful cycle, overdue flag |
| 4.4 | Check flow run health | `services/` | Prefect flow run API | Recent run statuses for Flows 1 & 2 |
| 4.5 | Evaluate pipeline status | `services/` | Results from 4.1–4.4 | Aggregated health status, new/resolved issues |
| 4.6 | Raise / resolve ops alerts | `services/` | Pipeline issues, existing ops alerts | New/updated ops alert records |
| 4.7 | Notify operations team | `services/` | New/changed ops alerts | Notifications dispatched (ops channel) |
| 4.8 | Log health metrics | `store/` | All check results | Persisted to pipeline health table |

#### Notes

- **Distinct from flood alerts**: Ops alerts go to the operations/engineering team, not flood forecasters. Different notification channel, different recipients, different urgency model.
- **4.1**: Each NWP source has an expected delivery schedule (e.g. ICON-CH2-EPS available ~5h after cycle). Late = expected but not yet arrived. Missing = past the acceptable window.
- **4.2**: Per-station staleness based on per-adapter-type config (e.g. SMN stations expected every 10 min, DHM stations every hour). Not per-station — too tedious to configure.
- **4.3**: If the last forecast cycle is older than expected, something in Flow 1 is broken.
- **4.4**: Queries Prefect's API for recent flow run states. Detects repeated failures, stuck runs.
- **4.8**: Health metrics over time enable diagnostics (e.g. "NWP has been consistently late for a week").

#### Sequencing

```
4.1 ─┐
4.2 ─┤
4.3 ─┼→ 4.5 → 4.6 → 4.7
4.4 ─┘         ↘ 4.8
```

Steps 4.1–4.4 are independent checks — run in parallel. They join at 4.5 for evaluation. Notifications (4.7) and metric logging (4.8) run in parallel after 4.6.

### Flows 8 & 10 — Skill computation (unified)

```
Trigger:  On-demand (after hindcast, after retraining) or scheduled (yearly refresh)
Flow:     compute_skills
Layer:    flows/ — orchestration only, delegates to services
```

Flows 8 (initial skill computation) and 10 (skill recomputation) are the same flow with different scope. Flow 8 = narrow scope (one station/model after hindcast). Flow 10 = broad scope (all stations/models, yearly or after retraining).

#### Steps

| # | Step | Layer | Input | Output |
|---|------|-------|-------|--------|
| S.1 | Determine scope | `services/` | Request params (stations, models, period) or "all" | List of (station, model, period) tuples to evaluate |
| S.2 | Fetch hindcast results | `store/` | Scope from S.1 | Hindcast forecast ensembles |
| S.3 | Fetch corresponding observations | `store/` | Matching station/period pairs | QC-passed observed values |
| S.4 | Compute verification metrics | `services/` | Hindcast ensembles + observations | Per-station, per-model, per-lead-time, per-season skill scores |
| S.5 | Aggregate metrics | `services/` | Station-level scores | Cross-station summaries (by model, by region, overall) |
| S.6 | Store skill results | `store/` | Computed metrics | Persisted to skill tables (versioned) |

Using `S.*` prefix since this flow serves both Flow 8 and Flow 10.

#### Notes

- **S.1**: Scope can be a single station/model (after a hindcast), a set of models (after retraining), or everything (yearly refresh). Same flow function, different scope parameter.
- **S.4**: Standard metric set, extensible over time:
  - Ensemble: CRPS, reliability diagram data, spread-skill ratio
  - Deterministic (on ensemble median/mean): NSE, KGE, PBIAS, MAE
  - All metrics computed per lead time — skill degrades with lead time and this must be visible.
  - Seasonal breakdown with configurable season definitions (e.g. monsoon Jun–Sep, dry Oct–May for Nepal; or equal quarters for Switzerland). Season config is per-deployment, not per-station.
- **S.5**: Two audiences: developers comparing models across stations, and hydrologists seeing which model performs best at their station (used in Flow 3 for model selection).
- **S.6**: Skill results are versioned — a recomputation creates a new record, doesn't overwrite previous ones. Enables tracking skill evolution over time.
- **Consumers**: Flow 3 dashboard (hydrologist model selection), developer tools, API.

#### Sequencing

```
S.1 → S.2 ─┐
       ↘    ├→ S.4 → S.5 → S.6
      S.3 ─┘
```

S.2 and S.3 run in parallel (both are store reads scoped by S.1), then join at S.4. Steps S.4–S.5 are parallelizable across stations.

### Flows 6 & 9 — Model training (unified)

```
Trigger:  On-demand (model admin, or from Flow 5) or scheduled (e.g. yearly)
Flow:     train_models
Layer:    flows/ — orchestration only, delegates to models/services
```

Flows 6 (initial training) and 9 (retraining) are the same flow. The flow checks whether an existing artifact exists for each station/model pair: if no → initial training (auto-promote). If yes → retraining (compare + approval).

#### Steps

| # | Step | Layer | Input | Output |
|---|------|-------|-------|--------|
| T.1 | Determine scope | `services/` | Request params (models, stations, training period) or "all" | List of (station, model, period) tuples to train |
| T.2 | Gather training data | `store/` | Station configs, training period | Historical observations (+ NWP archive in v1) |
| T.3 | Run training | `models/` | Training data, model hyperparameters | New model artifact (versioned) |
| T.4 | Run hindcast | → Flow 7 | New artifact, hindcast period | Hindcast forecast ensembles |
| T.5 | Compute skill | → Flows 8/10 | Hindcast results | Skill scores for new artifact |
| T.6 | Compare against current | `services/` | New skill scores, current model's skill scores | Comparison report |
| T.7 | Request approval | `services/` | Comparison report | Pending approval record, notification to model admin |
| T.8 | Promote or reject | `services/` + `store/` | Model admin decision | Updated model registry (or rejection logged) |

Using `T.*` prefix since this flow serves both Flow 6 and Flow 9.

**Initial training (Flow 6)**: T.1 → T.2 → T.3 → T.4 → T.5 → auto-promote. Steps T.6–T.8 skipped (nothing to compare against).

**Retraining (Flow 9)**: All steps. T.6–T.8 require existing artifact for comparison and model admin approval.

#### Notes

- **T.1**: Default training period is all available data. Optionally specify date ranges (model-specific — some models benefit from a rolling window, others from full history). Cross-validation strategy is model-specific.
- **T.3**: Models are separate packages. Training interface is part of the model Protocol. Compute-intensive — may need different resource allocation than operational flows.
- **T.4–T.5**: Composes Flow 7 (hindcast) and Flows 8/10 (skill computation) directly. Training is not complete without validation.
- **T.6** *(retraining only)*: Automated comparison on the same hindcast period. Generates a report (skill deltas per metric, per lead time, per season).
- **T.7–T.8** *(retraining only)*: Human-in-the-loop. Model admin reviews comparison report and approves or rejects. Async step — flow pauses until admin acts (via dashboard or API).
- **T.8**: Promotion = new artifact becomes the active version. Old artifact retained (never deleted). Rejection logged with comparison report.
- **Parallelizable** across station/model pairs at steps T.2–T.7.

#### Sequencing

```
Initial:    T.1 → T.2 → T.3 → T.4 → T.5 → promote
Retraining: T.1 → T.2 → T.3 → T.4 → T.5 → T.6 → T.7 ... T.8
```

Sequential per station/model pair. Pairs are independent and run in parallel. Async pause between T.7 and T.8 (awaiting model admin approval, retraining only).

### Flow 7 — Hindcast generation

```
Trigger:  On-demand (from Flows 6/9, or standalone by model admin)
Flow:     run_hindcast
Layer:    flows/ — orchestration only, delegates to models/services
```

#### Steps

| # | Step | Layer | Input | Output |
|---|------|-------|-------|--------|
| H.1 | Determine scope | `services/` | Station, model, model artifact version, hindcast period, time step | List of hindcast time steps |
| H.2 | Gather historical forcing | `store/` | Station, weather source mappings, hindcast period | Historical weather forecasts or reanalysis per time step |
| H.3 | Gather historical observations | `store/` | Station, hindcast period, lookback window | QC-passed observations per time step |
| H.4 | Assemble per-step inputs | `services/` | Forcing + observations, model input requirements | Input bundle per hindcast time step (respecting data availability cutoff) |
| H.5 | Run model per time step | `models/` | Input bundles, model artifact | Forecast ensembles per hindcast step |
| H.6 | Store hindcast results | `store/` | Hindcast forecast ensembles + model artifact version | Persisted to hindcast tables |

Using `H.*` prefix since hindcast is referenced from multiple flows.

#### Notes

- **H.1**: Hindcast period and time step are caller-specified. Time step matches the operational forecast frequency (e.g. daily or 6-hourly).
- **H.2**: Historical weather forcing — source depends on what's available for the period. Could be archived NWP forecasts, reanalysis (ERA5-Land in v1), or other historical weather forecast products. v0 will use weather forecasts (product TBD).
- **H.4**: Critical — must simulate operational conditions. Each time step only sees data that would have been available at that point in time (no future leakage). The lookback window per step matches what the model expects operationally.
- **H.5**: Same model code as operational Flow 1 step 1.7. Parallelizable across time steps (each is independent given its input bundle).
- **H.6**: Hindcast results stored in dedicated tables, separate from operational forecasts — different volumes and access patterns. Each hindcast record links to the specific model artifact version used, enabling comparison across model versions.
- **Consumers**: Flows 8/10 (skill computation), Flows 6/9 (training validation), model admin (standalone comparison).

#### Hindcast vs operational storage

Hindcast and operational forecasts use separate storage. As operational history grows, older operational forecasts may be archived or migrated to hindcast storage for long-term skill tracking. Both stores record model artifact version to enable cross-version comparison.

#### Sequencing

```
H.1 → H.2 ─┐
  ↘         ├→ H.4 → H.5 → H.6
  H.3 ──────┘
```

H.2 and H.3 run in parallel (both are store reads). They join at H.4. Steps H.4–H.5 are parallelizable across time steps.

### Flow 5 — Station onboarding

```
Trigger:  On-demand (model admin)
Flow:     onboard_station
Layer:    flows/ — orchestration only, delegates to services/adapters
```

#### Steps

| # | Step | Layer | Input | Output |
|---|------|-------|-------|--------|
| 5.1 | Register station metadata | `services/` + `store/` | Station definition (location, type, basin, parameters, thresholds) | Station record in DB |
| 5.2 | Configure weather source mappings | `services/` + `store/` | Station, NWP source config, basin/band geometries | Weather source ↔ station linkage |
| 5.3 | Import historical observations | `adapters/` + `store/` | Station, data source, date range | Raw observations persisted |
| 5.4 | Run QC on historical observations | `services/` + `store/` | Imported observations, QC rules | QC flags applied |
| 5.5 | Configure model assignments | `services/` + `store/` | Station, available models | Model ↔ station mappings |
| 5.6 | Train initial models | → Flows 6/9 (initial mode) | Station, assigned models, historical period | Trained artifacts + baseline skill scores |

#### Notes

- **5.1**: Station metadata includes location (GeoCoord), station type (river/weather), basin assignment, measured parameters, flood threshold definitions. Thresholds are part of station metadata but may come from a different source or be added later. Source can be TOML bootstrap file or dashboard input.
- **5.2**: Maps the station to its NWP forcing source(s). For basin-average models: which basin geometry. For elevation-band models: which bands. For point models: which grid cell(s). Determines what Flow 1 steps 1.1/1.2 fetch for this station.
- **5.3**: Bulk import — could be large (decades of hourly data). Adapter-specific: CSV upload, API fetch, or database migration. Handles source-specific parameter name mapping to canonical names.
- **5.4**: Same QC service as Flow 2 step 2.3, applied to the historical batch. Flagged values excluded from training data (Flows 6/9).
- **5.5**: Which models run for this station — model admin decision. Can be updated independently later.
- **5.6**: Invokes Flows 6/9 in initial mode (train → hindcast → skill, auto-promote). Validates that the station is properly configured and models produce reasonable results.

#### Sequencing

```
5.1 → 5.2 ─┐
  ↘         ├→ 5.5 → 5.6
  5.3 → 5.4 ┘
```

5.2 (weather source config) and 5.3–5.4 (historical import + QC) run in parallel after 5.1. Both must complete before 5.5 (model assignment needs weather sources and QC'd observations). 5.6 follows 5.5.

---

## Component map

```
src/sapphire_flow/
├── types/          # Domain NamedTuples
├── schemas/        # Pydantic models (system boundary validation only)
├── protocols/      # Store, adapter, model, notification Protocols
├── adapters/       # External data source implementations
├── models/         # Forecast model implementations (separate packages)
├── store/          # PostgreSQL store implementations
├── services/       # Business logic (alerting, QC, skill, forecast prep)
├── flows/          # Prefect flow definitions (orchestration only)
├── api/            # FastAPI routes (JSON + CSV export)
├── bulletin/       # Excel bulletin generation
├── dashboard/      # HTMX review dashboard (review, adjust, visualize)
├── config/         # Settings (config.toml + env vars)
└── preprocessing/  # NWP spatial extraction (GridExtractor)
```

## Layering rule

```
flows/ and api/  →  services/  →  store/
```

- **flows/ and api/**: orchestration and HTTP. No business logic.
- **services/**: all business logic. Receives stores via dependency injection.
- **store/**: data access behind Protocols. No business logic.
- **models/**: pure functions. No DB, no I/O.

### Test layer mapping

Follows from the layering rule. See CLAUDE.md for test writing conventions.

| Layer | Test type | Strategy |
|-------|-----------|----------|
| `types/`, `protocols/` | Unit | Pure validation logic. Known-answer tests for `__new__` invariants. |
| `models/` | Unit | Pure functions — deterministic input/output. Known-answer tests from literature or reference implementations for numerical correctness. |
| `services/` | Unit | Bulk of test coverage. Fake stores injected via Protocols. No DB, no I/O. |
| `store/` | Integration | Thin tests against real PostgreSQL (test container). Verify SQL correctness, not business logic. |
| `adapters/` | Integration | Recorded responses (VCR-style) for external APIs. Contract tests to detect upstream format changes. |
| `flows/` | Integration | Lightweight orchestration tests. Verify task wiring, not business logic (already covered in `services/`). |
| `api/` | Integration | FastAPI test client. Verify routing, serialization, status codes. Business logic tested via `services/`. |
| End-to-end | E2E | Small reference dataset through full ingest → forecast → alert cycle. Few tests, slow, run in CI not locally. |

### Cross-cutting standards (TBD — detail in `docs/standards/`)

- **Security**: State-of-the-art practices (OWASP top 10, secrets management, least-privilege DB users, API authentication). Detail to be specified in `docs/standards/security.md`.
- **CI/CD & deployment**: Automated test → build → deploy pipeline. Single-command deployment for hydromets with limited IT capacity. Docker Compose on a single VM must remain simple to operate. Detail to be specified in `docs/standards/cicd.md`.

## Tech stack

| Component | Choice |
|-----------|--------|
| Language | Python 3.11+ |
| Data frames | Polars |
| Database | PostgreSQL 16 + PostGIS |
| Connection pool | PgBouncer (transaction mode) |
| Orchestration | Prefect 3 |
| API | FastAPI |
| Dashboard | HTMX + Jinja2 |
| Reverse proxy | Caddy |
| Type checker | pyright --strict |
| Linter/formatter | ruff |

## Key design decisions

| Decision | Summary |
|----------|---------|
| Types in this package | All Protocols and domain types in `protocols/` and `types/`. No separate SDK. |
| NamedTuple domain types | Pydantic only at system boundaries. |
| Entity-based store Protocols | One Protocol per entity, not a generic repository. |
| Station config in DB | TOML is bootstrap import only. Runtime config in database. |
| Ensemble-first | All forecasts are ensembles or quantiles. Models reduce internally. |
| UTC everywhere | `UtcDatetime` NewType. Naive datetimes rejected at boundaries. |
| Parse, don't validate | Raw → Pydantic at boundary → domain NamedTuple. |
| Clock injection | No `datetime.now()` in business logic. |
| Models as separate packages | Discovered via Python entry points. |
| Forecast adjustments tracked | Every manual adjustment recorded with forecaster ID, timestamp, and rationale. |
| API-first data export | External consumers (Nepal authorities, international) ingest our API. JSON default, CSV export supported. No push-based export. |

## Access management

Five roles (v1, Nepal deployment):

- **Org admin**: creates/deletes user accounts, assigns read/write permissions per user, manages API keys (scoped per authority or state)
- **IT admin**: responsible for deployment, integration with external systems, monitoring production workflows, infrastructure
- **Model admin**: hydrological domain expert. Station onboarding, model configuration (which models run for which stations, hyperparameters, training schedules), approves/rejects model promotions after retraining
- **Forecaster**: reviews, adjusts, and publishes forecasts via dashboard
- **API consumer**: read-only access via API key

v0 defers auth — single-user, no access control.

## v0 scope

Swiss public data. Three sub-phases:

- **v0a**: Core daily pipeline (CAMELS-CH + SwissMetNet + ICON-CH2-EPS + BAFU)
- **v0b**: Sub-daily algorithm R&D (CAMELS generic, LSTM/transformer models)
- **v0c**: Swiss sub-daily operational validation

Between v0 and v1, the pipeline will be validated with additional public datasets (candidates: UK, Germany, US, New Zealand — not yet defined) to stress-test adapter generality before Nepal deployment.

v1 adds Nepal (ECMWF IFS, DHM stations, elevation-band NWP extraction, ERA5-Land).
