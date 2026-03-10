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
