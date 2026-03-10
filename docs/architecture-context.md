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

6. **Model training**
   Initial training for new stations/models on historical data.

7. **Hindcast generation**
   Run forecast models over a historical period for a given station/model combination. Used for: onboarding validation, model comparison, post-retraining verification, ongoing skill tracking.

8. **Skill computation**
   Compute verification metrics from hindcast results. Supports station-level and cross-station aggregation.

### Maintenance (yearly or on-demand)

9. **Model retraining**
   Retrain existing models on accumulated data to account for changing conditions.

10. **Skill recomputation**
    Recompute verification metrics across all stations/models with the latest data.

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
| 1.9 | Store forecast results | `store/` | Forecast ensembles | Persisted to `forecasts` + `forecast_values` (status = `raw`) |
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

Three roles (v1, Nepal deployment):

- **Org admin**: creates/deletes user accounts, assigns read/write permissions per user, manages API keys (scoped per authority or state)
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
