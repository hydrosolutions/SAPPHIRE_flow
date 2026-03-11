# Architecture Context

Read this first before working on any task.

## What SAPPHIRE Flow does

Operational hydrological forecasting system. Ingests historical and real-time weather forecasts and
weather station as well as river observations, runs ensemble ML models, checks flood thresholds, and
serves results via REST API. Forecasters review and publish forecasts multiple 
times a day. Runs on Docker Compose on a single VM.

## Data flows

### Operational (recurring, scheduled)

1. **Weather ingest → post-process → forecast → alert**
   Fetch NWP forcing → [extract spatial averages] → [archive] → post-process → fetch QC'd observations → run forecast models → [bias-correct outputs] → store → check flood thresholds → raise/resolve alerts → notify

2. **Observation ingest → QC → observation alerts**
   Fetch latest station observations → quality control → check thresholds against observed values → raise/resolve alerts

3. **Forecast review → publish → bulletin**
   Dashboard shows forecasts + visualizations → forecaster optionally adjusts values → reviews (selects preferred model) → publishes → generate Excel bulletin on request

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

11. **NWP gap recovery**
    Re-fetch missing NWP archive data when gaps are detected by Flow 4. Flag unrecoverable gaps permanently. Only needed when SAPPHIRE handles archiving (not when Data Gateway is upstream).

Other maintenance tasks:
- Database backup (scheduled Prefect task — see backup and disaster recovery)
- Data archival to cold storage (scheduled Prefect task — see data retention and cold storage)
- Observation gap-filling between operational cycles (TBD)
- Database partition management (TBD)

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

Steps 1.2, 1.3, and 1.8 are **conditional** — see notes.

#### Notes

- **1.1**: Fetch NWP forcing. Source type determines what is returned — see "Weather forecast data flows" section. Three source types: (a) SAPPHIRE Data Gateway → pre-extracted values (basin-average or elevation-band), (b) point weather forecasts (e.g. SMN) with uncertainty, (c) raw gridded NWP. The adapter returns a typed weather data object (`PointForecast`, `BasinAverageForecast`, `ElevationBandForecast`, or `GriddedForecast`).
- **1.2** *(conditional)*: Spatial extraction — only needed when 1.1 returns raw grids and the model requires extracted data (basin-average or elevation-band). Skipped when the Data Gateway provides pre-extracted data, or when the model consumes gridded data directly. v0: GridExtractor on ICON-CH2-EPS.
- **1.3** *(conditional)*: Only needed when no upstream gateway handles archiving. Archive happens *before* post-processing so raw extracted values are preserved. Permanent retention. Archived data carries a `spatial_type` tag.
- **1.4**: NWP post-processing. May include bias correction (quantile mapping), ensemble calibration, downscaling, or spatial extraction — configured per model per deployment. Preserves or transforms the spatial representation (see "Weather forecast data flows"). Pass-through until sufficient archive (6–12 months) for bias correction. Distinct from forecast output correction in 1.8.
- **1.5**: Reads QC'd observations from the store. Flow 2 (observation ingest + QC) runs on its own schedule (e.g. every 30 min) — this step reads the *result*, not raw station feeds. The two flows are decoupled. Future option: add a top-up QC call at the start of Flow 1 to guarantee freshness (trivial — reuses the same QC service function). **Staleness guard**: if the most recent observation for a station is older than a deployment-configurable threshold (e.g. 6h), the forecast proceeds with a warning flag on the forecast record (`observation_staleness_hours`) visible to forecasters in the API and dashboard. Flow 4 detects prolonged staleness independently.
- **1.6**: Assembles the full input window per model. Each model declares `required_features` and `spatial_input_type` — input preparation validates that all configured sources have been transformed to the correct spatial format and merged into a single forcing object (see "Weather forecast data flows" and "Model Protocol"). Two patterns:
  - *ML models*: concatenates historical weather with NWP forecast to fill the lookback window, which is typically longer than the NWP forecast horizon. The historical weather source is an open decision — see below.
  - *Conceptual models*: runs the model over a warm-up period using observations to derive internal state (soil moisture, snow, groundwater), then switches to NWP forecast forcing at the issue time. State is always observation-derived, never carried forward from a previous forecast.
  - **Fallback for conceptual models**: if the warm-up run fails, (1) use the last successfully saved state snapshot (staleness threshold is deployment-configurable and season-dependent — shorter during wet/monsoon season when catchment state changes rapidly, longer during dry season), or (2) if too stale, cold-start with extended warm-up from observations. Any forecast produced from a snapshot or cold-start records `warm_up_source: Literal["fresh", "snapshot", "cold_start"]` and `warm_up_state_age_hours` in the forecast metadata — visible to forecasters in the API and dashboard, and flagged by Flow 4.
- **1.7**: Parallelizable across stations. On model failure, falls back to next assigned model (detail in future iteration).
- **1.8** *(conditional)*: Forecast *output* bias correction (discharge / water level). Distinct from NWP input correction in 1.4. Pass-through when not configured.
- **1.9**: Each forecast record links to the model artifact version that produced it.
- **1.10**: Probability-based: P(Q > threshold) for each danger level. See "Danger levels and threshold configuration" section below for the full config shape. Only evaluates levels where the station has a defined threshold value — undefined levels are skipped (no alert, no display). The exceedance probability that triggers an alert is deployment-configurable per danger level. Defaults must be set; hydromet operations staff confirm acceptable false alarm rates before production deployment.
- **1.11**: Deduplication via partial unique index. Auto-resolution uses hysteresis to prevent alert flapping: separate `trigger_probability` and `resolve_probability` thresholds per danger level (resolve threshold lower than trigger), and configurable minimum consecutive cycles before triggering or resolving. Without hysteresis, ensemble probability oscillation between NWP cycles causes fire-resolve-fire loops and alert fatigue.
- **1.12**: Async. Failed notifications retried by sweep task (every 5 min).
- **API serving**: No explicit step — the API reads persisted results from the DB. Storing in 1.9 makes forecasts available; publishing happens via Flow 3 (forecast review). The API also serves archived forcing time series (precipitation, temperature, and other predictors) alongside forecasts — see API design notes.

#### Open decision: ML model lookback window forcing source

ML models (e.g. LSTM) require a lookback window (typically 365 days) of historical weather forcing concatenated with the NWP forecast. The historical portion can come from:
- **Station observations** (SMN for v0) — co-located weather stations. Simple, available, but introduces a train/operational mismatch if training uses the same source.
- **Gridded reanalysis** (ERA5-Land for v1) — spatially consistent, gap-free, but daily-only for some Swiss products.
- **Archived NWP extractions** — from the NWP archive (step 1.3). Only covers the operational period, not the full lookback window.

This choice affects model skill and must be consistent between training (Flows 6/9) and operational inference. To be resolved before v0 model training begins.

#### Open decision: NWP lateness fallback

When NWP data is late (common — happens multiple times per month), the forecast cycle must decide:
- **Wait** up to a configurable maximum (e.g. 3h past expected delivery), then
- **Fall back** to the most recent available NWP cycle (e.g. use 18 UTC cycle if 00 UTC is late), or
- **Skip** if no NWP cycle is available within a configurable maximum age.

Every forecast record must store the NWP cycle reference time used as forcing — forecasters and the API must display which NWP cycle produced each forecast, not just the forecast issue time. Flow 4 monitors NWP delivery status independently.

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
- **2.2–2.4**: Single `observations` table. Raw values are stored first (2.2), then QC adds flags/status in place (2.4). Raw values are never overwritten — QC is metadata on the observation, not a replacement. Flagged values are excluded from downstream use (forecasting in Flow 1 step 1.5). See "Quality control data model" section below for the full type definitions.
- **2.3**: QC rules TBD in detail (range checks, rate-of-change, spatial consistency). Grows in sophistication over time. QC rule version is stored with each flag — enables selective recomputation when rules change without losing the audit trail of previous flagging decisions.
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

- **3.1**: Read-only. Shows all models that ran for a station so the forecaster can compare. Also displays forcing time series (precipitation, temperature by default) alongside the hydrograph. Model admin configures which predictors are shown per station — all archived predictors are available.
- **3.2**: Optional. Each adjustment is an immutable record (forecaster ID, timestamp, rationale). Original model output is never overwritten. Multiple adjustments can be made before publishing.
- **3.3**: Review combines model selection and confirmation into one action. Forecaster picks the preferred model per station; status moves to `reviewed`. Optimistic locking on status transitions.
- **3.4**: Publishes selected forecasts. Only `published` forecasts appear in the public API and bulletins.
- **3.5–3.7**: Re-triggers the same threshold/alert logic from Flow 1 (steps 1.10–1.12) on the published values. Always runs here regardless of whether Flow 1 also checked on raw (see Flow 1 open decision).
- **3.8**: On-demand — forecaster explicitly requests bulletin generation after publishing.
- **Status transitions**: `raw → reviewed → published`. Review combines model selection and optional adjustments into one action. Adjustments are append-only audit records independent of status.

#### Open decision

- **Batch vs per-station publish**: Does the forecaster publish one station at a time or an entire cycle at once? Assumed per-cycle (review all, then publish batch). Needs confirmation with hydromet operations staff.

#### Sequencing

```
3.1 → [3.2 ⇄ 3.3] → 3.4 → 3.5 → 3.6 → 3.7
                             ↘ 3.8
```

Steps 3.2 and 3.3 form an interactive loop — the forecaster may adjust and review multiple times before publishing (3.4). Steps 3.5–3.7 (threshold re-check) and 3.8 (bulletin) run in parallel after publication.

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
- **4.1**: Each NWP source has an expected delivery schedule (e.g. ICON-CH2-EPS available ~5h after cycle). Late = expected but not yet arrived. Missing = past the acceptable window. Also performs retrospective archive completeness audit — detects gaps in the NWP archive that weren't caught in real time. When recoverable gaps are found, triggers Flow 11 (NWP gap recovery).
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

- **5.1**: Station metadata includes location (GeoCoord), station type (river/weather), basin assignment, measured parameters, flood threshold definitions, IANA timezone (e.g. `Asia/Kathmandu`, `Europe/Zurich`), forecast target parameter (discharge, water level, or both), and regulation type (`unregulated`, `reservoir`, `irrigation_diversion`, `run_of_river_hydro`, or `None` if unknown). Regulation type is used for model selection guidance and forecaster warnings — regulated stations produce systematically different forecast errors during operator-driven release changes. Thresholds are part of station metadata but may come from a different source or be added later. Initial rating curve may be uploaded here (see rating curve management). Source can be TOML bootstrap file or dashboard input.
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

### Flows 6 & 9 — Model training (unified)

```
Trigger:  On-demand (model admin, or from Flow 5) or scheduled (e.g. yearly)
Flow:     train_models
Layer:    flows/ — orchestration only, delegates to models/services
```

Flows 6 (initial training) and 9 (retraining) are the same flow. If no existing artifact → initial training (auto-promote). If existing artifact → retraining (compare + approval).

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
- **T.4–T.5**: Composes Flow 7 (hindcast) and Flows 8/10 (skill computation). Training is not complete without validation.
- **T.6** *(retraining only)*: Automated comparison on the same hindcast period. Generates a report (skill deltas per metric, per lead time, per season).
- **T.7–T.8** *(retraining only)*: Human-in-the-loop. Model admin reviews comparison report and approves or rejects. Async — flow pauses until admin acts (via dashboard or API).
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
- **H.2**: Historical weather forcing — two distinct categories that must not be conflated:
  - **`NWP_ARCHIVE`**: archived NWP forecasts that would have been available operationally at each hindcast time step. This is the only valid basis for computing operational skill scores — it correctly captures NWP error and lead-time degradation.
  - **`REANALYSIS`** (or station observations used as pseudo-perfect forcing): assesses model capability given near-perfect forcing. Useful for diagnosing whether errors come from the hydrology or the NWP, but produces optimistic skill scores that overestimate real-world operational performance.
  Every hindcast result (H.6) must carry a `forcing_type` tag (`ForcingType` enum — DB values `"nwp_archive"`, `"reanalysis"` per conventions.md casing rule). v0: forcing product TBD — may initially use station observations (producing diagnostic-only skill scores) until sufficient NWP archive accumulates for operational skill assessment.
- **H.4**: Critical — must simulate operational conditions. Each time step only sees data that would have been available at that point in time (no future leakage). The lookback window per step matches what the model expects operationally.
- **H.5**: Same model code as operational Flow 1 step 1.7. Parallelizable across time steps (each is independent given its input bundle).
- **H.6**: Hindcast results stored in dedicated tables, separate from operational forecasts — different volumes and access patterns. Each record links to the model artifact version used. As operational history grows, older operational forecasts may be archived to hindcast storage for long-term skill tracking.
- **Consumers**: Flows 8/10 (skill computation), Flows 6/9 (training validation), model admin (standalone comparison).

#### Sequencing

```
H.1 → H.2 ─┐
  ↘         ├→ H.4 → H.5 → H.6
  H.3 ──────┘
```

H.2 and H.3 run in parallel (both are store reads). They join at H.4. Steps H.4–H.5 are parallelizable across time steps.

### Flows 8 & 10 — Skill computation (unified)

```
Trigger:  On-demand (after hindcast, after retraining) or scheduled (yearly refresh)
Flow:     compute_skills
Layer:    flows/ — orchestration only, delegates to services
```

Flows 8 (initial) and 10 (recomputation) are the same flow with different scope. Flow 8 = narrow (one station/model after hindcast). Flow 10 = broad (all stations/models, yearly or after retraining).

#### Steps

| # | Step | Layer | Input | Output |
|---|------|-------|-------|--------|
| S.1 | Determine scope | `services/` | Request params (stations, models, period) or "all" | List of (station, model, period) tuples to evaluate |
| S.2 | Fetch forecast results | `store/` | Scope from S.1 | Hindcast and/or operational forecast ensembles |
| S.3 | Fetch corresponding observations | `store/` | Matching station/period pairs | QC-passed observed values |
| S.4 | Compute verification metrics | `services/` | Forecast ensembles + observations | Per-station, per-model, per-lead-time, per-season skill scores |
| S.5 | Aggregate metrics | `services/` | Station-level scores | Cross-station summaries (by model, by region, overall) |
| S.6 | Store skill results | `store/` | Computed metrics | Persisted to skill tables (versioned) |

Using `S.*` prefix since this flow serves both Flow 8 and Flow 10.

#### Notes

- **S.4**: Standard metric set, extensible over time:
  - Ensemble: CRPS, CRPS skill score (CRPSss against persistence and climatology baselines), reliability diagram data, spread-skill ratio
  - Threshold-specific: Brier Skill Score (BSS) at each configured danger level threshold — directly measures the skill of probability forecasts that drive the alert system. ROC curve data per threshold (stored for display).
  - Deterministic (on ensemble median/mean): NSE, KGE, PBIAS, MAE
  - All metrics computed per lead time — skill degrades with lead time and this must be visible.
  - Seasonal breakdown with configurable season definitions (e.g. monsoon Jun–Sep, dry Oct–May for Nepal; or equal quarters for Switzerland). Season config is per-deployment, not per-station.
  - Flow-regime stratification: scores computed separately for low flow (<Q50), high flow (Q50–Q90), and flood range (>Q90). Percentile thresholds are deployment-configurable and computed from historical observations during station onboarding. Flood-range BSS and CRPS are the primary operational metrics for model promotion decisions.
  - Baseline artifacts (climatology quantiles, persistence forecast) must be computed and stored during station onboarding (Flow 5) — required as reference for CRPSss and BSS.
  - Interpretation thresholds (e.g. NSE > 0.75 = "Very good") are timestep-dependent. Standard literature thresholds (Moriasi et al. 2007) apply to daily streamflow; sub-daily forecasts require separate, typically more lenient, classification schemes. The deployment-configurable classification must include a `timestep` field.
- **S.4 — skill sources**: Skill can be computed on both hindcasts and operational forecasts. Every skill result carries a `skill_source` tag:
  - **`HINDCAST_NWP_ARCHIVE`**: hindcast forced with archived NWP. Gold standard — reflects true operational conditions including NWP error.
  - **`HINDCAST_REANALYSIS`**: hindcast forced with reanalysis or observations. Diagnostic — isolates hydrology model skill from NWP error. Optimistic.
  - **`OPERATIONAL`**: computed on accumulated real-time forecasts. Reflects actual production performance but may be season-biased or short-record.
- **S.4 — model promotion skill priority**: The promotion comparison (T.6) uses the best available evidence, not rigidly `HINDCAST_NWP_ARCHIVE`. Priority order:
  1. `HINDCAST_NWP_ARCHIVE` — preferred
  2. `OPERATIONAL` — real performance, but may be season-biased
  3. `HINDCAST_REANALYSIS` — optimistic, but better than nothing

  "Sufficient data" thresholds are deployment-configurable: `min_samples: int` (e.g. 100 forecast-observation pairs), `min_seasons: int` (e.g. 2 — must cover wet + dry). The promotion report (T.6) shows which source was used, why, sample size, and season coverage. The model admin (T.8) sees this context.
- **S.4 — storage schema**: See "Skill score storage schema" section for table definition.
- **S.5**: Two audiences: developers comparing models across stations, and hydrologists choosing models in Flow 3.
- **S.6**: Versioned — recomputation creates a new record, doesn't overwrite. Enables tracking skill evolution over time.
- **Consumers**: Flow 3 dashboard (model selection), developer tools, API.

#### Sequencing

```
S.1 → S.2 ─┐
       ↘    ├→ S.4 → S.5 → S.6
      S.3 ─┘
```

S.2 and S.3 run in parallel (both are store reads scoped by S.1), then join at S.4. Steps S.4–S.5 are parallelizable across stations.

### Flow 11 — NWP gap recovery

```
Trigger:  Triggered by Flow 4 step 4.1 when recoverable gaps detected
Flow:     recover_nwp_gaps
Layer:    flows/ — orchestration only, delegates to adapters/store
```

Slim recovery flow — gap *detection* lives in Flow 4 (watchdog). This flow only handles the re-fetch.

#### Steps

| # | Step | Layer | Input | Output |
|---|------|-------|-------|--------|
| 11.1 | Attempt re-fetch | `adapters/` | Missing cycle list, NWP source config | Recovered data or permanent failure per cycle |
| 11.2 | Store recovered data | `store/` | Recovered NWP extractions | Persisted to `weather_forecasts`, gaps marked as filled |
| 11.3 | Flag unrecoverable gaps | `store/` | Permanently failed cycles | Gaps flagged in archive (permanent record) |

#### Notes

- **Conditional flow**: Only relevant when SAPPHIRE handles NWP archiving (Flow 1 step 1.3). Not needed when a Data Gateway manages the archive.
- **11.1**: Many NWP providers only retain recent data (days to weeks), so recovery is time-sensitive. Flow 4 should trigger this promptly when gaps are detected.
- **11.3**: Unrecoverable gaps are permanently flagged. They affect hindcast quality (Flow 7 step H.2) and post-processing calibration (Flow 1 step 1.4). Skill computation (Flows 8/10) should account for gap periods.
- **Permanent retention**: Archive data is never deleted. Storage management is about partitioning and indexing, not purging.

#### Sequencing

```
11.1 → 11.2
  ↘ 11.3
```

11.2 (store recovered) and 11.3 (flag unrecoverable) run in parallel — each cycle is either recovered or flagged.

### Scheduled maintenance — Database backup

```
Trigger:  Prefect schedule (e.g. daily)
Flow:     backup_database
Layer:    flows/ — infrastructure task
```

Scheduled `pg_dump` to local disk (same backup target as cold storage). Static Parquet files (cold storage) included in the backup procedure. Not a data flow — an infrastructure task managed by Prefect for scheduling, retry, and failure notification.

---

## Ensemble generation

Ensemble generation is **model-internal** — the system stores ensembles/quantiles regardless of how they were produced. Two strategies coexist:

1. **ML-native uncertainty**: Model directly outputs prediction intervals or quantiles (e.g. quantile regression, MC dropout, mixture density networks).
2. **NWP ensemble propagation**: Each NWP ensemble member run through a deterministic model → ensemble of forecast traces.

A student thesis will compare these approaches. Both must work within the same framework. The model Protocol outputs a consistent ensemble format; the generation method is opaque to the rest of the system.

### Ensemble representation

The system supports two canonical representations, tagged with a discriminator:

- **Members** (`EnsembleRepresentation.MEMBERS`): N member traces × H timesteps. Typical for NWP ensemble propagation (e.g. ICON-CH2-EPS = 21 members) and some ML experiments. Minimum member count: 1 (a single member = deterministic forecast).
- **Quantiles** (`EnsembleRepresentation.QUANTILES`): Q quantile levels × H timesteps. Typical for ML models (quantile regression, mixture density networks) and downscaled weather forecasts. Minimum quantile levels: 3.

Every `ForecastEnsemble` carries a `representation` tag. Downstream consumers (threshold checking, CRPS, BSS) handle both:

- **Threshold exceedance probability**: members → `count(exceeding) / N`. Quantiles → CDF interpolation (with documented accuracy caveat for tail probabilities where flood thresholds typically sit).
- **CRPS**: members → standard CRPS formula. Quantiles → quantile-weighted CRPS approximation (Laio & Tamea 2007).
- **BSS**: derived from exceedance probability regardless of representation.

**Storage**: `forecast_values` table uses `member_id INT NULL` and `quantile DOUBLE PRECISION NULL` columns with a CHECK constraint that exactly one is non-null. The parent `forecasts` row carries `representation` (`"members"` or `"quantiles"`).

This applies to both weather forecast ensembles (NWP) and runoff/water level forecast ensembles (model output). The same representation and storage pattern is used throughout.

### Model Protocol

All forecast models satisfy a single `ForecastModel` Protocol. Models are pure functions — no DB, no I/O. Artifact serialization is the model's responsibility; artifact *persistence* (reading/writing files) is the caller's.

#### Methods

```
ForecastModel Protocol:
  required_features: frozenset[str]       # class attribute, e.g. {"precipitation", "temperature", "snow_depth"}
  train(data: TrainingData, params: ModelParams, rng: random.Random) -> ModelArtifact
  predict(artifact: ModelArtifact, inputs: ModelInputs, rng: random.Random) -> ForecastEnsemble
  serialize_artifact(artifact: ModelArtifact) -> bytes
  deserialize_artifact(raw: bytes) -> ModelArtifact
```

- **`required_features`**: class-level declaration of canonical parameter names the model needs as input. The input preparation service (Flow 1 step 1.6) validates completeness before calling `predict()`.
- **`train()`**: receives historical data and hyperparameters, returns an opaque artifact. The caller persists the serialized artifact. `rng` ensures reproducibility.
- **`predict()`**: receives a pre-loaded artifact and prepared inputs, returns a `ForecastEnsemble`. The `rng` is used for stochastic models (MC dropout, etc.); deterministic models ignore it.
- **`serialize_artifact()` / `deserialize_artifact()`**: model-specific serialization. The caller handles file I/O — models never touch the filesystem.

#### Supporting types

- **`ModelInputs`**: generic input bundle for both ML and conceptual models.
  - `forcing: polars.DataFrame | xarray.Dataset` — `polars.DataFrame` for tabular models (BASIN_AVERAGE, ELEVATION_BAND): columns = canonical parameter names (band-qualified for elevation-band, e.g. `precipitation_band_1`), rows = timesteps. `xarray.Dataset` for GRIDDED models: dimensions = time × parameter × y × x. Covers the full input window (lookback + forecast horizon for ML; warm-up + forecast for conceptual).
  - `observations: polars.DataFrame` — columns = observed parameters (discharge, water level), rows = timesteps covering the lookback / warm-up period. Always tabular regardless of model spatial type.
  - `issue_time: UtcDatetime` — the forecast issue time. Models must not use data after this point from `observations`.
  - `forecast_horizon_steps: int` — number of timesteps to forecast.
  - `time_step: timedelta` — temporal resolution (e.g. 1h, 6h, 24h).

- **`TrainingData`**: same structure as `ModelInputs` but covering the full training period. Includes target observations for supervised learning.
  - `forcing: polars.DataFrame`
  - `observations: polars.DataFrame`
  - `targets: polars.DataFrame` — the variable(s) to predict (discharge, water level).
  - `time_step: timedelta`

- **`ModelParams`**: model-specific hyperparameters. Opaque `dict[str, Any]` at the Protocol level — each model implementation defines its own expected keys. Validated by the model's `train()` method.

- **`ModelArtifact`**: opaque to the system. Could be neural network weights, calibrated parameters, or any model-specific state. The Protocol only requires that it round-trips through `serialize_artifact()` / `deserialize_artifact()`.

### Model registry schema

Two distinct entities: **model types** (the installed Python packages) and **model artifacts** (trained instances per station).

#### `models` table (model type registry)

```
models:
  id: TEXT PK                            # entry point name, e.g. "lstm_daily" — stable across versions
  display_name: TEXT                     # human-readable, e.g. "LSTM Daily"
  description: TEXT NULL
  created_at: TIMESTAMPTZ
```

Populated at startup by `ModelRegistry` scanning entry points (see conventions.md "Model discovery").

#### `model_artifacts` table (trained instances)

```
model_artifacts:
  id: UUID PK
  model_id: TEXT FK → models.id
  station_id: UUID FK → stations.id
  status: TEXT                           # ModelArtifactStatus enum
  artifact_path: TEXT                    # relative path to serialized artifact file
  training_period_start: TIMESTAMPTZ
  training_period_end: TIMESTAMPTZ
  trained_at: TIMESTAMPTZ
  promoted_at: TIMESTAMPTZ NULL          # when status changed to ACTIVE
  promoted_by: UUID NULL                 # model admin who approved (NULL for initial auto-promote)
  superseded_at: TIMESTAMPTZ NULL        # when a newer artifact replaced this one
  created_at: TIMESTAMPTZ
```

#### `model_assignments` table (which models run for which stations)

```
model_assignments:
  station_id: UUID FK → stations.id
  model_id: TEXT FK → models.id
  is_active: BOOL DEFAULT TRUE           # can be deactivated without deleting
  priority: INT DEFAULT 0                # fallback order (lower = preferred)
  created_at: TIMESTAMPTZ
  PK: (station_id, model_id)
```

#### Model artifact status and transitions

```
ModelArtifactStatus enum: TRAINING | PENDING_APPROVAL | ACTIVE | SUPERSEDED | REJECTED
Transitions:
  TRAINING → PENDING_APPROVAL (training complete, retraining mode)
  TRAINING → ACTIVE (training complete, initial mode — auto-promote)
  PENDING_APPROVAL → ACTIVE (model admin approves)
  PENDING_APPROVAL → REJECTED (model admin rejects)
  ACTIVE → SUPERSEDED (newer artifact promoted for same station/model)
```

Partial unique index: `(station_id, model_id) WHERE status = 'active'` — enforces at most one active artifact per station/model pair.

---

## Weather forecast data flows

### Spatial representations

Four types, representing how weather data is spatially organized:

```
SpatialRepresentation enum: POINT | BASIN_AVERAGE | ELEVATION_BAND | GRIDDED
```

- **`POINT`**: per-station scalar value per parameter per timestep. From point weather stations (e.g. SMN) or single grid-cell extraction.
- **`BASIN_AVERAGE`**: per-station single value per parameter per timestep, spatially averaged over a basin polygon. From Data Gateway (basin mode) or GridExtractor.
- **`ELEVATION_BAND`**: per-station, per-band value per parameter per timestep. Multiple elevation bands per basin. From Data Gateway (band mode) or GridExtractor with band geometries.
- **`GRIDDED`**: full 2D spatial grid per parameter per timestep. From raw NWP (e.g. ICON-CH2-EPS GRIB2, ECMWF IFS). Represented as `xarray.Dataset`.

Basin-average and elevation-band are both **tabular** — representable as `polars.DataFrame` (elevation-band has more columns, one per band per parameter). Gridded is structurally different (`xarray.Dataset` with spatial dimensions).

### Source types

| Source | Returns | Example |
|--------|---------|---------|
| SAPPHIRE Data Gateway (basin mode) | `BasinAverageForecast` | Nepal v1 — ECMWF IFS pre-extracted per basin |
| SAPPHIRE Data Gateway (band mode) | `ElevationBandForecast` | Nepal v1 — ECMWF IFS pre-extracted per elevation band |
| Point weather stations | `PointForecast` | SMN stations with uncertainty (members or quantiles) |
| Raw gridded NWP | `GriddedForecast` | ICON-CH2-EPS GRIB2, ECMWF IFS GRIB2 |

Each adapter returns one concrete spatial type. The adapter implementation is determined by the deployment config (see conventions.md "Adapter registration").

### Post-processing pipeline

NWP post-processing (Flow 1 step 1.4) is a **configurable chain of transforms** per model per deployment. Each transform may preserve or change the spatial representation:

| Transform | Input spatial type | Output spatial type | Example |
|-----------|-------------------|--------------------|---------|
| Bias correction (quantile mapping) | any | same | Correct systematic NWP bias |
| Ensemble calibration | any | same | Adjust spread/reliability |
| Downscaling | GRIDDED | GRIDDED | Increase spatial resolution |
| Spatial extraction (basin-avg) | GRIDDED | BASIN_AVERAGE | GridExtractor with basin polygon |
| Spatial extraction (elevation-band) | GRIDDED | ELEVATION_BAND | GridExtractor with band geometries |
| Spatial interpolation | POINT | GRIDDED | Interpolate station network to grid (rare) |

Transforms are chained. Example pipeline for a basin-average LSTM model using raw ICON-CH2-EPS:
```
GriddedForecast → [downscale] → GriddedForecast → [extract_basin_avg] → BasinAverageForecast
```

The final output spatial type **must match the model's declared `spatial_input_type`**.

### Model weather source configuration

Configured **per model per deployment**, with per-station geometry.

**Deployment-level** (configured once per model):
```
model_weather_config:
  model_id: TEXT                           # e.g. "lstm_daily"
  sources: list[WeatherSourceConfig]       # one or more sources per model

WeatherSourceConfig:
  nwp_source: TEXT                         # e.g. "icon_ch2_eps", "ecmwf_ifs"
  parameters: list[str]                    # canonical names from this source
  pipeline: list[str]                      # ordered post-processing steps
```

**Per-station** (inherits deployment default):
```
station_weather_sources:
  station_id: UUID FK
  nwp_source: TEXT
  extraction_type: SpatialRepresentation   # BASIN_AVERAGE, ELEVATION_BAND, or POINT
  geometry: GEOMETRY or JSONB              # basin polygon (for basin-avg), band polygons (for elevation-band)
  active: BOOL DEFAULT TRUE
  PK: (station_id, nwp_source)
```

Most stations inherit the deployment default extraction type. Per-station override is available for special cases (e.g. one station needs elevation-band extraction while the rest use basin-average).

### Input preparation and merging

When a model uses multiple weather sources, input preparation (Flow 1 step 1.6):
1. Runs each source through its configured post-processing pipeline
2. Transforms all sources to the model's declared `spatial_input_type`
3. Merges all parameters into a single forcing object (`polars.DataFrame` for tabular, `xarray.Dataset` for gridded)
4. Validates that all `required_features` are present

The model receives one merged forcing input. It does not know about sources — it sees parameters.

For the rare case where a model needs mixed spatial types (e.g. gridded precipitation + basin-average snow), the model declares `GRIDDED` and basin-average values are broadcast to spatially uniform grid fields. This is physically meaningful and lossless.

---

## Forecast storage schema

### Operational vs hindcast forecasts

Two distinct domain types with different metadata, storage tables, and lifecycles:

**`OperationalForecast`** — produced in real time by Flow 1. Has a publication lifecycle (`raw → reviewed → published`), forecaster adjustments, and operational metadata (`warm_up_source`, `nwp_cycle_reference_time`, `observation_staleness_hours`). Stored in `forecasts` + `forecast_values`.

**`HindcastForecast`** — produced retroactively by Flow 7. No publication lifecycle. Carries `forcing_type` (`NWP_ARCHIVE` or `REANALYSIS`) and `hindcast_step` (the simulated issue time). Stored in `hindcast_forecasts` + `hindcast_values`.

Both share the ensemble payload (member traces or quantiles) and can be used for skill computation. The skill service accepts either via a common verification interface — both provide ensemble values, issue time, station, and model needed for metric computation.

### `forecasts` table (operational)

Two tables: `forecasts` (one row per station/cycle/model) and `forecast_values` (one row per timestep per member or quantile).

### `forecasts` table

```
forecasts:
  id: UUID PK
  station_id: UUID FK
  model_id: TEXT FK                          # entry point name → models.id
  model_artifact_version: TEXT               # links to model_artifacts.id
  issued_at: TIMESTAMPTZ                     # forecast issue time
  nwp_cycle_reference_time: TIMESTAMPTZ      # which NWP cycle produced the forcing
  nwp_cycle_is_fallback: BOOL DEFAULT FALSE  # true when a non-current NWP cycle was used
  representation: TEXT                       # "members" or "quantiles"
  status: TEXT DEFAULT 'raw'                 # ForecastStatus: raw | reviewed | published
  version: INT DEFAULT 1                    # optimistic locking
  warm_up_source: TEXT NULL                  # WarmUpSource: fresh | snapshot | cold_start (NULL for ML models)
  warm_up_state_age_hours: DOUBLE PRECISION NULL  # hours since last state snapshot (NULL when fresh or ML)
  observation_staleness_hours: DOUBLE PRECISION NULL  # age of most recent observation used
  created_at: TIMESTAMPTZ
  updated_at: TIMESTAMPTZ
```

Indexes: `(station_id, issued_at DESC)` for latest-forecast queries. Partial unique: `(station_id, model_id, issued_at)` to prevent duplicate forecasts per cycle.

### `forecast_values` table

```
forecast_values:
  id: UUID PK
  forecast_id: UUID FK → forecasts.id
  valid_time: TIMESTAMPTZ                    # the forecasted timestep
  lead_time_hours: INT                       # hours from issued_at to valid_time
  member_id: INT NULL                        # non-null for member representation
  quantile: DOUBLE PRECISION NULL            # non-null for quantile representation
  value: DOUBLE PRECISION                    # forecasted value
```

CHECK constraint: exactly one of `member_id` or `quantile` is non-null.
Partitioned monthly by `issued_at` (derived from `forecasts.issued_at` via FK). Composite index: `(forecast_id, valid_time)`.

### Status enum and transitions

```
ForecastStatus enum: RAW | REVIEWED | PUBLISHED
Transitions: RAW → REVIEWED → PUBLISHED (forward only, enforced server-side)
```

### Metadata enums

```
WarmUpSource enum: FRESH | SNAPSHOT | COLD_START
EnsembleRepresentation enum: MEMBERS | QUANTILES
```

### Hindcast tables

`hindcast_forecasts` and `hindcast_values` mirror the operational tables structurally, minus the publication lifecycle fields.

#### `hindcast_forecasts` table

```
hindcast_forecasts:
  id: UUID PK
  station_id: UUID FK
  model_id: TEXT FK
  model_artifact_version: TEXT
  hindcast_step: TIMESTAMPTZ               # the simulated issue time
  forcing_type: TEXT                        # ForcingType: nwp_archive | reanalysis
  representation: TEXT                      # members | quantiles
  hindcast_run_id: UUID                    # groups all steps of one hindcast execution
  created_at: TIMESTAMPTZ
```

No `status`, `version`, `warm_up_source`, or `nwp_cycle_is_fallback` — hindcasts have no publication lifecycle or operational metadata.

Index: `(station_id, model_id, hindcast_step)`. Partitioned monthly by `hindcast_step`.

#### `hindcast_values` table

```
hindcast_values:
  id: UUID PK
  hindcast_forecast_id: UUID FK → hindcast_forecasts.id
  valid_time: TIMESTAMPTZ
  lead_time_hours: INT
  member_id: INT NULL
  quantile: DOUBLE PRECISION NULL
  value: DOUBLE PRECISION
```

Same CHECK constraint and partitioning as `forecast_values`.

### Skill source enum

```
SkillSource enum: HINDCAST_NWP_ARCHIVE | HINDCAST_REANALYSIS | OPERATIONAL
```

Every skill result carries a `skill_source` tag. See Flows 8/10 notes for the promotion priority.

### Skill score storage schema

Narrow/tall design — one row per metric per stratum. Uniform Protocol methods regardless of metric set.

#### `skill_scores` table

```
skill_scores:
  id: UUID PK
  station_id: UUID FK
  model_id: TEXT FK
  artifact_version: TEXT                   # which model artifact was evaluated
  skill_source: TEXT                       # SkillSource: hindcast_nwp_archive | hindcast_reanalysis | operational
  forcing_type: TEXT NULL                  # ForcingType (NULL for operational)
  computation_version: INT                 # monotonically increasing per (station, model) — enables "latest" queries
  computed_at: TIMESTAMPTZ
  lead_time_hours: INT                     # forecast lead time this score applies to
  season: TEXT NULL                        # e.g. "monsoon", "dry", NULL = all-season
  flow_regime: TEXT NULL                   # FlowRegime: low | high | flood | NULL = all-regime
  metric: TEXT                             # e.g. "crps", "nse", "kge", "bss_danger_1"
  score: DOUBLE PRECISION
  sample_size: INT                         # number of forecast-observation pairs
  created_at: TIMESTAMPTZ
```

Index: `(station_id, model_id, computation_version, metric, lead_time_hours)` for the common query "latest skill for model X at station Y." The `computation_version` pattern avoids expensive `GROUP BY MAX(computed_at)` — instead `WHERE computation_version = (SELECT MAX(...))`.

#### `skill_diagrams` table

Stores structured data for reliability diagrams and ROC curves — too large for the scalar `skill_scores` table.

```
skill_diagrams:
  id: UUID PK
  station_id: UUID FK
  model_id: TEXT FK
  artifact_version: TEXT
  skill_source: TEXT
  computation_version: INT
  lead_time_hours: INT
  season: TEXT NULL
  diagram_type: TEXT                       # "reliability" | "roc"
  threshold_level: TEXT NULL               # danger level name (for ROC/BSS diagrams)
  data: JSONB                              # diagram-specific structure (see below)
  created_at: TIMESTAMPTZ
```

JSONB `data` structures:
- **Reliability diagram**: `{"bins": [{"forecast_prob": 0.1, "observed_freq": 0.08, "count": 45}, ...]}`
- **ROC curve**: `{"points": [{"fpr": 0.0, "tpr": 0.0}, {"fpr": 0.05, "tpr": 0.3}, ...], "auc": 0.85}`

#### Supporting enums

```
FlowRegime enum: LOW | HIGH | FLOOD
  LOW = below Q50, HIGH = Q50–Q90, FLOOD = above Q90
  Percentile thresholds are deployment-configurable, computed during station onboarding.
```

---

## Weather forecast (NWP) archive schema

Stores extracted NWP values (basin-average, elevation-band, or point — never raw GRIB2). Archived in Flow 1 step 1.3 before post-processing, so raw extracted values are preserved.

### `weather_forecasts` table

```
weather_forecasts:
  id: UUID PK
  station_id: UUID FK                   # station this extraction is for
  nwp_source: TEXT                      # e.g. "icon_ch2_eps", "ecmwf_ifs"
  cycle_time: TIMESTAMPTZ               # NWP model run time (e.g. 2026-03-10T00:00Z)
  valid_time: TIMESTAMPTZ               # forecast valid time
  parameter: TEXT                        # canonical name (precipitation, temperature, snow_depth, ...)
  member_id: INT NULL                    # NULL for deterministic NWP
  value: DOUBLE PRECISION
  is_gap: BOOL DEFAULT FALSE            # true if this cycle was originally missing
  gap_status: TEXT NULL                  # NULL = not a gap, "recovered" = re-fetched, "unrecoverable" = permanently lost
  created_at: TIMESTAMPTZ
```

Partitioned monthly by `cycle_time`. Composite index: `(station_id, nwp_source, cycle_time, valid_time)` for the lookback fetch in Flow 1 step 1.4. Partial index on `is_gap = TRUE` for Flow 11 recovery queries.

Permanent retention — archive data is never deleted. Storage management is about partitioning and indexing, not purging.

### Gap recovery fields

Used by Flow 11 (NWP gap recovery):
- `is_gap = FALSE, gap_status = NULL`: normal data, no gap.
- `is_gap = TRUE, gap_status = 'recovered'`: was missing, successfully re-fetched.
- `is_gap = TRUE, gap_status = 'unrecoverable'`: permanently lost. Affects hindcast quality (Flow 7) and post-processing calibration (Flow 1 step 1.4).

---

## Danger levels and threshold configuration

### Deployment-level configuration

Danger levels are **fixed per deployment** — the set of level names and their display order is defined once in deployment config. Examples:

- Switzerland (5 levels): `none`, `low`, `moderate`, `significant`, `high`, `very_high`
- Nepal (TBD with DHM): likely 3–4 levels

Each danger level has deployment-wide alert parameters:

```
DangerLevelDefinition:
  name: str                    # e.g. "significant" — unique within deployment
  display_order: int           # for dashboard sorting
  trigger_probability: float   # P(exceedance) to trigger alert, e.g. 0.50
  resolve_probability: float   # P(exceedance) to resolve alert (< trigger), e.g. 0.30
  min_trigger_cycles: int      # consecutive cycles exceeding before triggering, e.g. 1
  min_resolve_cycles: int      # consecutive cycles below before resolving, e.g. 2
```

### Per-station thresholds

Each station has threshold *values* for a subset of the deployment's danger levels. Not all levels need to be defined for every station — undefined levels are **skipped** (no evaluation, no alert, not displayed on dashboard).

```
StationThreshold:
  station_id: UUID
  danger_level: str            # references DangerLevelDefinition.name
  parameter: str               # "discharge" or "water_level"
  value: float                 # threshold value in parameter units
  source: ThresholdSource      # enum: AUTHORITY | INFERRED
```

- **`AUTHORITY`**: defined by the national agency (e.g. BAFU, DHM). Configured during station onboarding.
- **`INFERRED`**: computed from flood frequency analysis on historical data. **Deferred to v1** — requires sufficient historical record (20+ years), distribution fitting (GEV/log-Pearson III), and hydrologist review before operational use. The data model supports it from v0; the computation does not exist yet.

Deployment config includes `infer_missing_thresholds: bool` (default `false`). When `true` and the flood frequency analysis service is available (v1+), missing thresholds are inferred during onboarding and flagged with `source = INFERRED`. Forecasters see the source flag on the dashboard.

### Observation alerts

Observation alerts use the same danger levels and per-station threshold values as forecast alerts. The check is a direct value comparison (`observed_value > threshold_value`) rather than probability-based. Same hysteresis parameters apply (consecutive cycles before trigger/resolve).

---

## Quality control data model

### QC status

Observations carry an aggregate QC status:

```
QcStatus enum: RAW | QC_PASSED | QC_FAILED | QC_SUSPECT
```

- **`RAW`**: just ingested, QC has not run yet.
- **`QC_PASSED`**: all rules passed. Available for downstream use (forecasting, training).
- **`QC_SUSPECT`**: at least one rule flagged the value as suspect but not definitively wrong. Excluded from downstream use by default but visible to operators.
- **`QC_FAILED`**: at least one rule flagged the value as invalid. Excluded from downstream use.

Aggregate status is the worst flag: `QC_FAILED` > `QC_SUSPECT` > `QC_PASSED`. An observation with no flags after QC completes is `QC_PASSED`.

### QC flags

Each observation can have multiple QC flags — one per rule that evaluated it. Flags are stored in a JSONB column on the `observations` table.

```
QcFlag:
  rule_id: str               # e.g. "range_check", "rate_of_change"
  rule_version: str           # e.g. "1.0.0" — enables selective recomputation
  status: QcStatus            # QC_PASSED, QC_SUSPECT, or QC_FAILED (not RAW)
  detail: str | None          # human-readable explanation, e.g. "value 500 exceeds max 200"
```

### Observations table columns

```
observations:
  id: UUID PK
  station_id: UUID FK
  timestamp: TIMESTAMPTZ
  parameter: TEXT              # canonical name (e.g. "discharge", "precipitation")
  value: DOUBLE PRECISION      # the observed value (never overwritten by QC)
  qc_status: TEXT              # aggregate QcStatus enum value
  qc_flags: JSONB              # list[QcFlag], empty list when status = RAW
  qc_rule_version: TEXT NULL   # version of the QC ruleset that last evaluated this row
  created_at: TIMESTAMPTZ
```

Partitioned yearly by `timestamp`. Composite index: `(station_id, timestamp, qc_status)` for the filtered fetch in Flow 1 step 1.5 (`WHERE qc_status = 'qc_passed'`).

### Manual observation correction (v1+)

Not in v0 scope. When implemented, adds `overridden_by: UUID NULL`, `overridden_at: TIMESTAMPTZ NULL`, `override_rationale: TEXT NULL` columns. A manual override changes `qc_status` to `QC_PASSED` or `QC_FAILED` regardless of automated flags, with full audit trail.

---

## Rating curve management

Real-time station data is typically water level. Discharge is derived via rating curves. Rating curves change over time (especially after major flood events).

- **Storage**: Per-station, versioned. Each curve is a list of (water level, discharge) pairs with interpolation. Includes a valid-from date and version identifier.
- **Upload**: Initial curve during station onboarding (Flow 5 step 5.1). Updated periodically (e.g. yearly by DHM) via API or dashboard.
- **Bidirectional conversion**: Water level → discharge and discharge → water level, using the active rating curve for that station.
- **Temporal versioning**: Forecasts and hindcasts reference the rating curve version valid at the time of production. Historical forecasts are never retroactively re-converted.
- **Forecast target flexibility**: Some stations forecast discharge, others water level, potentially both. Model admin configures this per station.
- **Retroactive reprocessing risk**: Many national services (including BAFU) retroactively reprocess historical discharge series with updated rating curves. If training data uses reprocessed discharge but operational data uses the current rating curve, a mismatch arises when a major flood shifts channel geometry. The data ingestion adapter for historical observations should record whether discharge series are retrospectively reprocessed. For Nepal v1, confirm with DHM before data ingestion whether historical discharge was reprocessed and with which curve versions.
- **v0 (Switzerland)**: BAFU provides real-time discharge directly (well-maintained rating curves). Rating curve storage may not be needed for v0 but the data model should support it.
- **v1 (Nepal)**: DHM provides real-time water level + historical discharge. Rating curves available for daily data but not sub-hourly. DHM will upload updated rating tables yearly.

### Rating curve schema

```
rating_curves:
  id: UUID PK
  station_id: UUID FK
  version: INT                             # monotonically increasing per station
  valid_from: TIMESTAMPTZ
  valid_to: TIMESTAMPTZ NULL               # NULL = currently active
  points: JSONB                            # list of {"water_level": float, "discharge": float}
  interpolation: TEXT DEFAULT 'linear'     # "linear" or "log-linear"
  uploaded_by: UUID NULL
  created_at: TIMESTAMPTZ
```

Index: `(station_id, valid_from DESC)` for temporal lookup. Partial unique index: `(station_id) WHERE valid_to IS NULL` — enforces at most one active curve per station.

---

## Operational support schemas

### `alerts` table

```
alerts:
  id: UUID PK
  station_id: UUID FK
  source: TEXT                             # AlertSource: forecast | observation
  danger_level: TEXT                       # references DangerLevelDefinition.name
  status: TEXT                             # AlertStatus: raised | acknowledged | resolved
  trigger_probability: DOUBLE PRECISION NULL  # NULL for observation alerts
  trigger_value: DOUBLE PRECISION NULL     # observed or forecast value that triggered
  triggered_at: TIMESTAMPTZ
  acknowledged_at: TIMESTAMPTZ NULL
  acknowledged_by: UUID NULL
  resolved_at: TIMESTAMPTZ NULL
  consecutive_trigger_cycles: INT DEFAULT 1  # tracks hysteresis cycle count
  notified_at: TIMESTAMPTZ NULL            # NULL = notification pending
  created_at: TIMESTAMPTZ
```

Deduplication partial unique index: `(station_id, danger_level, source) WHERE status IN ('raised', 'acknowledged')` — prevents duplicate active alerts.

```
AlertStatus enum: RAISED | ACKNOWLEDGED | RESOLVED
AlertSource enum: FORECAST | OBSERVATION
```

### `forecast_adjustments` table

Append-only audit trail. Original model output is never overwritten.

```
forecast_adjustments:
  id: UUID PK
  forecast_id: UUID FK → forecasts.id
  forecaster_id: UUID FK
  adjusted_at: TIMESTAMPTZ
  rationale: TEXT
  adjustments: JSONB                       # list of {"valid_time": str, "lead_time_hours": int,
                                           #          "original_value": float, "adjusted_value": float,
                                           #          "member_id": int | null}
```

INSERT only for `sapphire_api` — no UPDATE or DELETE.

### `pipeline_health` table

Low-volume operational monitoring table. Not partitioned.

```
pipeline_health:
  id: BIGSERIAL PK
  check_type: TEXT                         # nwp_delivery | observation_freshness | forecast_freshness | flow_run_health
  checked_at: TIMESTAMPTZ
  status: TEXT                             # ok | warning | critical
  subject: TEXT                            # station code or NWP source name
  detail: JSONB                            # check-type-specific payload
  cycle_time: TIMESTAMPTZ NULL             # relevant NWP or forecast cycle
  created_at: TIMESTAMPTZ
```

Index: `(check_type, checked_at DESC)` for "last N checks of type X" queries. Retention: 90 days (handled by archival task).

### `basins` table

```
basins:
  id: UUID PK
  code: TEXT UNIQUE                        # human-readable reference, e.g. "BASIN-01"
  name: TEXT
  geometry: GEOMETRY(MULTIPOLYGON, 4326)   # PostGIS
  area_km2: DOUBLE PRECISION NULL
  created_at: TIMESTAMPTZ
```

Spatial index: `GIST (geometry)` for spatial queries in station onboarding (Flow 5 step 5.2).

### `flow_regime_configs` table

Per-station flow regime boundaries, computed from historical observations during station onboarding. Required for stratified skill computation (Flows 8/10 step S.4).

```
flow_regime_configs:
  id: UUID PK
  station_id: UUID FK
  q50: DOUBLE PRECISION                    # 50th percentile discharge (m³/s)
  q90: DOUBLE PRECISION                    # 90th percentile discharge (m³/s)
  computed_at: TIMESTAMPTZ
  observation_count: INT                   # number of observations used
  version: INT                             # monotonically increasing per station
  created_at: TIMESTAMPTZ
```

Versioned — recomputation with new data creates a new record. Skill computation references a specific version for reproducibility.

---

## Skill metric interpretation

Skill scores are accompanied by human-readable interpretation labels. Configured as dictionaries mapping score ranges to labels per metric:

```
NSE: (0.75, 1.0) → "Very good", (0.65, 0.75) → "Good", (0.50, 0.65) → "Satisfactory", (-∞, 0.50) → "Unsatisfactory"
```

Classification schemes are deployment-configurable (different agencies may use different standards) and must include a `timestep` field — daily and sub-daily forecasts require different thresholds (see S.4 notes). Skill scores are computed per lead time (Flows 8/10 step S.4) — lead time degradation is a display concern, not an architectural one. The API and dashboard use per-lead-time skill data to communicate forecast reliability.

---

## Timezone handling

- **Storage**: UTC always. `UtcDatetime` NewType enforced at boundaries.
- **Station metadata**: Each station has an IANA timezone identifier (e.g. `Asia/Kathmandu`, `Europe/Zurich`). Not a fixed offset — handles DST transitions correctly via `zoneinfo`.
- **Display**: API and dashboard convert UTC → local timezone for presentation.
- **Daily aggregation**: Uses the station's local timezone to define day boundaries. A hydrological day in Nepal (00:00–00:00 NPT) differs from a UTC day. This affects cold storage aggregation and dashboard display.
- **No data loss from UTC storage**: UTC timestamps uniquely identify each observation regardless of DST shifts. Spring-forward and fall-back transitions are handled correctly.

---

## Data retention and cold storage

- **Hot storage (PostgreSQL)**: Full resolution data, rolling 2-year window. Queried by API, dashboard, and operational flows.
- **Cold storage (Parquet on local disk)**: Full resolution data, permanent. Organized by station/year/parameter. Not queryable by the application directly.
- **Daily aggregates**: Retained permanently in PostgreSQL (small footprint). Aggregated using local timezone day boundaries.
- **Archival process**: Scheduled Prefect task (e.g. monthly). Exports data older than 2 years from hot tables to Parquet, verifies file integrity, then deletes exported rows. Idempotent — export first, verify, then delete.
- **Recovery**: When Flows 6/9 (training) or Flow 7 (hindcast) need historical data beyond the hot window, cold Parquet files are read on demand via Polars (native Parquet support). No permanent re-import needed.
- **Cold storage layout**: Path pattern `cold/{table}/{station_code}/{year}/{parameter}.parquet`. Columns identical to the hot table. Schema version in Parquet metadata key `sapphire_schema_version`.
- **Hot/cold dispatch**: Store Protocol `fetch_range` methods transparently dispatch based on whether the requested time range overlaps with the hot window boundary. Callers (training, hindcast) do not need to know whether data comes from PostgreSQL or Parquet. The hot window boundary is computed from `retention_window` in deployment config.
- **Retention window is deployment-configurable** (e.g. 2 years for Nepal, different for Switzerland).
- **Backup**: Parquet files are static once written — included in the backup procedure alongside database dumps. Incremental backups are efficient (only new files).
- **Risks and mitigations**:
  - Disk failure → mitigated by including Parquet in backups to external storage
  - Schema drift → store schema version in Parquet metadata, handle migration in read path
  - Archival job failure → idempotent design (export → verify → delete)
  - Retraining/hindcast jobs slower when reading cold data → acceptable for infrequent operations (yearly retraining)

---

## Backup and disaster recovery

Current scope: disaster recovery (DR), not high availability (HA).

- **HA (High Availability)**: Redundant systems with automatic failover. Out of scope for current phase — requires replicated infrastructure. Can be added later; the architecture is stateless at the application layer (all state in PostgreSQL), so migration to Docker Swarm/Kubernetes with replicated DB is feasible without redesign.
- **DR (Disaster Recovery)**: Ability to restore the system after failure from backups.

### DR plan

1. **Automated database backups**: Scheduled Prefect task (daily `pg_dump` to external storage). Cold storage Parquet files backed up alongside.
2. **Health endpoint**: FastAPI `/health` endpoint returning JSON with component status (DB connectivity, Prefect agent status, last successful forecast cycle age). External monitoring systems poll this — we provide the standard interface, the hydromet integrates with their monitoring infrastructure.
3. **Documented recovery procedure**: Step-by-step instructions for restoring from backup on a fresh VM. Part of operational documentation delivered to the hydromet.

Communication to hydromet: database backups are automated and recovery procedures are documented. HA (automatic failover) is not included in this project phase but can be added later if required.

---

## External consumers

API-first data export confirmed. Known consumers for v1 (Nepal):

- **DHM forecast dashboard** — full access to all stations and parameters
- **Nepal DRRMA Bipad portal** — flood alerts and forecast data
- **Other government authorities** — scoped access per agency
- **Hydropower agencies** — specific stations relevant to operations
- **Neighbouring countries** — border-relevant stations

All consumers pull from the REST API. API keys are scoped per consumer (per authority or state). No push-based integrations. The API supports JSON (default) and CSV export.

---

## Notification channels

Notification delivery mechanism TBD — pending input from DHM. Candidates:

- **Email** — standard, reliable
- **SMS** — critical for areas with limited internet connectivity
- **Webhook** — for integration with external systems (e.g. Bipad portal)

Architecture supports pluggable notification adapters. Channel selection is per-alert-type and per-recipient configurable.

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
                 →  adapters/
                 →  preprocessing/
```

- **flows/ and api/**: orchestration and HTTP. No business logic. May call `services/`, `adapters/`, and `preprocessing/` directly.
- **services/**: all business logic. Receives stores via dependency injection. Does not call adapters.
- **store/**: data access behind Protocols. No business logic.
- **adapters/**: external data source I/O. Does not call services or stores — returns domain types to the caller.
- **preprocessing/**: transforms adapter output (e.g. GridExtractor). Same constraints as adapters.
- **models/**: pure functions. No DB, no I/O. Model artifact loading/saving is handled by the flow or service layer — models receive a pre-loaded artifact object (for inference) or return an artifact object (from training). The I/O is external to the model package.

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
| Ensemble generation is model-internal | ML-native uncertainty and NWP ensemble propagation coexist. Model Protocol outputs a consistent ensemble format. |
| Rating curves versioned per station | Bidirectional water level ↔ discharge conversion. Temporal versioning for hindcast consistency. |
| Cold storage for historical data | 2-year hot window in PostgreSQL, full-resolution Parquet on local disk (permanent). Deployment-configurable. |
| IANA timezone per station | UTC storage, local display. Daily aggregation uses local day boundaries. |
| DR, not HA | Automated DB backups, `/health` endpoint, documented recovery. No automatic failover in current phase. |
| QC rules versioned | Rule version stored with each QC flag. Enables recomputation when rules change. |
| API-first data export | External consumers (Nepal authorities, international) ingest our API. JSON default, CSV export supported. No push-based export. |
| Forcing time series served via API | Dashboard displays precipitation + temperature by default alongside forecasts. Model admin configures which predictors are shown. |
| Hindcast forcing type tagged | Every hindcast carries `forcing_type` (`NWP_ARCHIVE` or `REANALYSIS`). Operational skill metrics computed only on NWP-archive-forced hindcasts. |
| Snow depth as required input | Snow depth (`H_SNOW`) extracted alongside P and T for snow-dominated catchments. Required for Alpine and high-elevation forecasting. |
| Alert hysteresis | Separate trigger/resolve probability thresholds per danger level. Prevents alert flapping from ensemble probability oscillation. |
| Forecast metadata transparency | Every forecast records NWP cycle reference time, `warm_up_source`, and `observation_staleness_hours`. Visible to forecasters. |

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
