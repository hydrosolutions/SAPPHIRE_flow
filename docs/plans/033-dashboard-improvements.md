---
status: DONE
created: 2026-04-14
scope: api routes + templates + JSON endpoints
depends_on: []
---

# 033 — Dashboard Improvements: From Counts to Content

## Context

The dashboard is our primary tool for visually verifying that pipeline flows (onboarding,
ingest, training, hindcast, skill) produced correct output. Currently it shows **counts
only** — "4,181,458 observations", "167 stations" — with no way to inspect data ranges,
freshness, coverage, or actual content without diving into the raw `/tables/` browser.

Several tables with data are not surfaced anywhere in the dashboard:
- `basins` (167 rows) — basin metadata, area, CAMELS-CH attributes
- `station_weather_sources` (167 rows) — NWP source config per station
- `flow_regime_configs` (142 rows) — Q50/Q90 boundaries per station
- `model_assignments` (501 rows) — only visible deep in model detail page

The `/tables/` page reflects **all 64 tables** including 36 PostGIS Tiger geocoding
tables (`addr`, `edges`, `faces`, `county`, `zip_*`, etc.) that are not SAPPHIRE data.

The station detail page shows observations/baselines/forcing charts but is missing
basin info, weather source, flow regime, model assignments, and hindcast/skill summaries.

Hindcast values (23M rows) — the primary output for verifying model quality — have no
visualization at all.

**Goal**: Make the dashboard a verification tool. After running any flow, you should be
able to glance at the dashboard and confirm the pipeline worked correctly.

---

## Current dashboard routes and templates

```
Route                     Template                         What it shows
─────────────────────────────────────────────────────────────────────────────
GET /                     dashboard.html                   8 count cards
GET /stations/            stations/list.html               table: code, name, kind, status, obs count
GET /stations/{id}/       stations/detail.html             metadata + Plotly charts (obs, baseline, forcing)
GET /forecasts/           forecasts/list.html              paginated table (currently empty)
GET /forecasts/{id}/      forecasts/detail.html            metadata + ensemble chart
GET /models/              models/list.html                 table: name, scope, time_step, artifact count
GET /models/{id}/         models/detail.html               artifacts, assignments, skill scores table, diagrams
GET /tables/              tables/list.html                 all 64 reflected tables with row counts
GET /tables/{name}/       tables/detail.html               paginated raw rows
```

JSON endpoints (called by Plotly charts in station detail):
```
GET /api/v1/stations/{id}/observations.json?parameter=&start=&end=
GET /api/v1/stations/{id}/baselines.json?parameter=
GET /api/v1/stations/{id}/forcing.json?start=&end=
GET /api/v1/forecasts/{id}/data.json
GET /api/v1/health
```

**Key files**:
- `src/sapphire_flow/api/routes/dashboard.py` — dashboard home
- `src/sapphire_flow/api/routes/stations.py` — station list/detail + JSON endpoints
- `src/sapphire_flow/api/routes/forecasts.py` — forecast list/detail
- `src/sapphire_flow/api/routes/models.py` — model list/detail
- `src/sapphire_flow/api/routes/tables.py` — raw table browser + shared helpers
- `src/sapphire_flow/api/templates/` — all Jinja2 templates

---

## Tasks

### Task 1: Filter system tables from `/tables/` page

**Problem**: The tables page lists 64 tables. Only ~28 are SAPPHIRE tables. The rest
are PostGIS Tiger geocoding tables (`addr`, `addrfeat`, `bg`, `county`, `cousub`,
`direction_lookup`, `edges`, `faces`, `featnames`, `geocode_settings`,
`geocode_settings_default`, `layer`, `loader_lookuptables`, `loader_platform`,
`loader_variables`, `pagc_gaz`, `pagc_lex`, `pagc_rules`, `place`, `place_lookup`,
`secondary_unit_lookup`, `state`, `state_lookup`, `street_type_lookup`, `tabblock`,
`tabblock20`, `topology`, `tract`, `zcta5`, `zip_lookup`, `zip_lookup_all`,
`zip_lookup_base`, `zip_state`, `zip_state_loc`), plus `spatial_ref_sys`.

**Change**: In `tables.py`, add a set of SAPPHIRE table names and filter reflected
tables against it. Use an **allowlist** (not blocklist) — only show tables that are
part of the SAPPHIRE schema.

```python
SAPPHIRE_TABLES = {
    "alembic_version", "alerts", "basins", "clim_baselines",
    "flow_regime_configs", "forecast_qc_overrides", "forecast_values",
    "forecasts", "group_model_assignments", "hindcast_forecasts",
    "hindcast_values", "historical_forcing", "model_artifacts",
    "model_assignments", "model_states", "models", "observations",
    "parameters", "pipeline_health", "skill_diagrams", "skill_scores",
    "station_group_members", "station_groups", "station_thresholds",
    "station_weather_sources", "stations", "weather_forecasts",
}
```

Apply in `table_list()` and keep `/tables/{name}/` accessible for any reflected table
(don't block detail view — sometimes you want to inspect a system table).

**Files**: `src/sapphire_flow/api/routes/tables.py`

**Verify**: `curl http://localhost:8001/tables/` should show ~28 tables, not 64.

---

### Task 2: Enrich dashboard home with date ranges and breakdowns

**Problem**: Cards show only counts. After running observation ingest you see
"4,181,458" but can't tell whether data spans 1981–2020 or 2024–2026, or whether it was
updated 3 minutes ago or 3 months ago.

**Change**: Extend `dashboard.py` to query additional summary stats per card.

For each card, add:

| Card | Add | Query |
|------|-----|-------|
| Stations | breakdown by status (operational/onboarding) | `GROUP BY station_status` |
| Observations | date range (min/max timestamp), breakdown by parameter | `MIN/MAX(timestamp)`, `GROUP BY parameter` |
| Forcing | date range (min/max valid_time), parameter list | `MIN/MAX(valid_time)`, `DISTINCT parameter` |
| Baselines | station coverage (N of M stations have baselines) | `COUNT(DISTINCT station_id)` |
| Models | nothing new (already good) | — |
| Forecasts | date range if >0 | `MIN/MAX(issued_at)` on `forecasts` |
| Hindcasts | model breakdown (count per model_id), date range | `GROUP BY model_id` + `MIN/MAX(hindcast_step)` |
| Skill Scores | model breakdown, metric list | `GROUP BY model_id` + `DISTINCT metric` |
| Active Alerts | nothing new (already good) | — |

Add two new cards:

| New Card | Content |
|----------|---------|
| Flow Regime | count, station coverage |
| Model Assignments | count, model breakdown |

Update `dashboard.html` template to render the additional info as `<span class="muted">`
lines under each count.

**Files**: `src/sapphire_flow/api/routes/dashboard.py`, `src/sapphire_flow/api/templates/dashboard.html`

**Verify**: Dashboard cards should show date ranges and breakdowns, not just counts.

---

### Task 3: Enrich station detail page with related data

**Problem**: The station detail page shows metadata + obs/baseline/forcing charts but is
missing key onboarding outputs. After onboarding a station, you can't see its basin,
weather source, flow regime, or model assignments without going to the raw table browser.

**Change**: In `stations.py` `station_detail()`, query additional tables and pass to
template. In `stations/detail.html`, add sections.

**New sections on station detail page**:

1. **Basin info** (from `basins` via `stations.basin_id` FK):
   - area_km2, regional_basin, network
   - attributes JSONB rendered as key-value pairs (CAMELS-CH catchment attributes)

2. **Weather source** (from `station_weather_sources`):
   - NWP source, extraction type, status

3. **Flow regime** (from `flow_regime_configs`):
   - P50, P90 values per parameter, observation count used

4. **Model assignments** (from `model_assignments`):
   - Table: model_id (linked to `/models/{id}/`), priority, status, time_step

5. **Hindcast summary** (from `hindcast_forecasts`):
   - Per model: count of hindcast steps, date range (first–last hindcast_step)
   - Link to hindcast visualization (Task 4)

6. **Skill summary** (from `skill_scores`):
   - Per model × lead_time: key metrics (NSE, KGE, CRPS)
   - Small table or heatmap-style colored cells

**Files**: `src/sapphire_flow/api/routes/stations.py`, `src/sapphire_flow/api/templates/stations/detail.html`

**Verify**: Open a station detail page (e.g. station 2434 Olten-Hammermühle). Should
show basin attributes, weather source = camels-ch/point, flow regime P50/P90, 3 model
assignments, hindcast summary (14,597 steps for linear_regression_daily), and skill
metrics.

---

### Task 4: Add hindcast visualization

**Problem**: Hindcast values (23M rows) are the primary output for verifying model
quality but have no visualization. The only way to check them is the raw table browser.

**Change**: Add a hindcast chart section to the station detail page (or as a separate
page linked from station detail). Shows observed vs. hindcast ensemble for a selected
model and lead time.

**New JSON endpoint**:
```
GET /api/v1/stations/{id}/hindcasts.json?model_id=...&lead_time_hours=...&start=...&end=...
```

Returns:
```json
{
  "observed": {"timestamps": [...], "values": [...]},
  "hindcast": {
    "timestamps": [...],
    "members": {"0": [...], "1": [...], ...}
  }
}
```

Query joins `hindcast_forecasts` → `hindcast_values` (filtered by station, model,
lead_time_hours) and overlays `observations` for the same period.

**Plotly chart**: Spaghetti plot of hindcast members (thin, semi-transparent) with
observed values overlaid (thick line). Controls: model selector, lead_time selector
(24/48/72/96/120/144/168h), date range picker.

**Performance note**: With 23M hindcast_values rows, the query must filter tightly
by station + model + lead_time + date range. The existing index
`ix_hindcast_forecasts_station_model_step_param` on `hindcast_forecasts` plus a join
to `hindcast_values` on `hindcast_forecast_id` should be efficient. Limit the default
date range to a reasonable window (e.g. last 365 days of data, ~365 points per member).

**Files**:
- `src/sapphire_flow/api/routes/stations.py` — new JSON endpoint
- `src/sapphire_flow/api/templates/stations/detail.html` — new chart section + JS

**Verify**: Open station detail for 2434 (Olten-Hammermühle), select
`linear_regression_daily` model, lead time 24h. Should see observed discharge overlaid
with hindcast ensemble members for ~2019-2020.

---

### Task 5: Add skill score charts to model detail page

**Problem**: The model detail page shows skill scores as a raw table of 1001 rows. This
is unreadable — you can't visually compare metrics across lead times or stations.

**Change**: Add Plotly charts above the existing skill scores table (keep table as
collapsible detail).

**Charts to add**:

1. **Metric vs. lead time** (line chart): For each key metric (NSE, KGE, CRPS, MAE),
   plot score on Y-axis vs. lead time (24–168h) on X-axis. One line per station (or
   aggregated mean if many stations). Shows skill degradation with increasing lead time.

2. **Metric summary bar chart**: For each metric at a reference lead time (e.g. 24h),
   show a bar per station. Quick way to spot outlier stations.

**New JSON endpoint**:
```
GET /api/v1/models/{model_id}/skills.json?artifact_id=...
```

Returns skill scores grouped for charting:
```json
{
  "lead_times": [24, 48, 72, 96, 120, 144, 168],
  "metrics": {
    "nse": {"station_1": [0.8, 0.7, ...], "station_2": [...]},
    "kge": {...},
    ...
  }
}
```

**Files**:
- `src/sapphire_flow/api/routes/models.py` — new JSON endpoint
- `src/sapphire_flow/api/templates/models/detail.html` — Plotly charts + controls

**Verify**: Open model detail for `linear_regression_daily`. Should see line charts
showing NSE/KGE/CRPS degradation from 24h to 168h lead time.

---

### Task 6: Add observation coverage overview

**Problem**: After running observation ingest or station onboarding, there's no way to
see which stations have data, which have gaps, or how QC flags are distributed — without
clicking into each station individually.

**Change**: Add a new route `/observations/` with a coverage matrix view.

**Coverage matrix**: Table with stations as rows, showing:
- Parameters available (discharge, water_level)
- Observation count per parameter
- Date range per parameter
- QC breakdown (% passed / suspect / failed)

Highlight stations with no observations, or with very low counts relative to their date
range (indicating gaps).

**New route**: `GET /observations/` → `observations/coverage.html`

**Files**:
- `src/sapphire_flow/api/routes/observations.py` (new file)
- `src/sapphire_flow/api/templates/observations/coverage.html` (new file)
- `src/sapphire_flow/api/__init__.py` — register new router
- `src/sapphire_flow/api/templates/base.html` — add nav link

**Verify**: `/observations/` shows all 167 stations with their observation parameters,
counts, date ranges, and QC status distribution.

---

## Task dependencies

```
Task 1 (filter tables)       — independent, quick win
Task 2 (enrich dashboard)    — independent
Task 3 (enrich station)      — independent
Task 4 (hindcast viz)        — benefits from Task 3 (station detail is the host page)
Task 5 (skill charts)        — independent
Task 6 (obs coverage)        — independent

Suggested order: 1 → 2 → 3 → 4 → 5 → 6
```

All tasks are independent and can be implemented in any order. The suggested order
prioritizes quick wins (1), then overview verification (2), then single-station
depth (3–4), then specialized views (5–6).

---

## Verification

After all tasks, the following checks should pass:

1. `curl http://localhost:8001/tables/` — ~28 SAPPHIRE tables, no Tiger/PostGIS noise
2. `curl http://localhost:8001/` — dashboard cards show date ranges, parameter
   breakdowns, coverage stats
3. `curl http://localhost:8001/stations/{id}/` — station detail shows basin, weather
   source, flow regime, model assignments, hindcast summary, skill summary
4. Hindcast chart on station detail renders observed vs ensemble for selected model/lead
5. `curl http://localhost:8001/models/{id}/` — skill score charts show metric vs lead
   time curves
6. `curl http://localhost:8001/observations/` — coverage matrix with all stations

Manual browser check: open dashboard, click through to a station (e.g. 2434), verify
all sections load without errors, charts render with data.
