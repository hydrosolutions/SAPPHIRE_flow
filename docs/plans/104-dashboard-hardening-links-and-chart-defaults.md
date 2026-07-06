# Plan 104 — dashboard hardening: links, chart defaults, and skill-chart consistency

**Status**: DRAFT — drafted from the dashboard-investigator pass on 2026-07-06.
Do not implement until user confirms READY.
**Priority**: medium — these are review-dashboard correctness and operability
fixes not covered by Plans 100/101/102. They do not block the forecast pipeline,
but they make the dashboard misleading or less useful during operations.
**Phase**: v0b — review dashboard hardening
**Parent**: local dashboard investigation, 2026-07-06
**Related**:
- Plan 100 — forecast-feed resilience. Forecast-feed outage/staleness is out of
  scope here.
- Plan 101 — water_level QC datum/range failures. QC correctness is out of scope
  here.
- Plan 102 — station-detail multi-parameter observation visibility. This plan must
  avoid duplicating that work and should run after Plan 102 if both touch
  `stations/detail.html`.
- `src/sapphire_flow/api/templates/base.html:35-41` and
  `src/sapphire_flow/api/templates/dashboard.html:94-98` (Prefect links)
- `src/sapphire_flow/api/__init__.py:14-25` and `docker-compose.yml:169-172`
  (`PREFECT_UI_URL` becomes a browser-visible link)
- `src/sapphire_flow/api/routes/stations.py:235-252` and
  `src/sapphire_flow/api/templates/stations/detail.html:39-47` (basin context is
  displayed but not linked)
- `src/sapphire_flow/api/routes/models.py:82-126` and `:175-190` (model detail
  and skill-chart endpoint can select different active artifacts)
- `src/sapphire_flow/api/templates/models/detail.html:114-119` (skill chart fetch
  omits the artifact id shown in the table)
- `src/sapphire_flow/api/templates/stations/detail.html:191-201`, `:300-328`
  (all station charts share the last-30-days date default even when forcing and
  hindcasts are historical)
**Created**: 2026-07-06

---

## Problem

The dashboard investigation found no internal 404/500 links and no missing chart
endpoints for existing data. It did find four NEW dashboard issues that are not
covered by Plans 100/101/102:

1. **Prefect UI link is dead from the host browser.** The rendered link is
   `http://prefect-server:4200`, which is a Docker-internal hostname. The dev
   overlay exposes Prefect on host port `4200`, so the browser-visible URL should
   be `http://localhost:4200` for local development.
2. **Station basin data is displayed but not linkable.** Both stations have
   `basin_id` rows, but the station-detail basin card renders plain text only.
   The requested station→basin cross-link does not exist.
3. **Model skill chart can disagree with the table below it.** The model-detail
   route orders artifacts newest-first and displays the first active artifact's
   table. The `/api/v1/models/{model_id}/skill-chart.json` endpoint resolves an
   active artifact with unordered `LIMIT 1`, so the chart can show a different
   station/artifact than the table. On the local stack, `climatology_fallback`
   demonstrates this mismatch: the endpoint returned 175 points from the older
   artifact while the newest active artifact had 170 current skill rows.
4. **Forcing and hindcast charts default to empty date windows.** The station page
   defaults all charts to the last 30 days. Observations have current data, but
   `historical_forcing` ends at 2020-12-31 and `hindcast_forecasts` ends at
   2022-12-21, so those charts appear empty by default despite substantial data
   existing.

## Goal

Make dashboard links resolvable from the user's browser, make station basin
relationships navigable, make model skill charts represent the same artifact as
their detail tables, and prevent historical station charts from looking empty
just because the shared default date window is current-time oriented.

## Non-goals

- Fixing forecast-feed blackout or fallback behavior — Plan 100.
- Fixing `water_level` QC failures or threshold configuration — Plan 101.
- Redesigning multi-parameter observation visibility — Plan 102.
- Adding authentication, permissions, or a production-grade dashboard IA.
- Changing database schemas.

## Evidence from the investigation

- All internal links discovered from dashboard, station, forecast, model, coverage,
  and table pages returned HTTP 200.
- Prefect link failed with DNS resolution for `prefect-server`; `docker-compose.dev.yml`
  exposes host port `4200`.
- Basin rows exist for stations 2009/2091:
  `stations.basin_id -> basins.id`, but the station page emits no `<a>` for the
  basin card.
- Forecast detail charts are meaningful: latest NWP member forecasts returned
  105 values each (21 members × 5 lead times); climatology quantile forecasts
  returned 35 values each (7 quantiles × 5 lead times).
- Station default observation endpoints are meaningful:
  discharge and water_temperature return current qc_passed points; water_level
  returns current points with mostly `qc_failed` status, as expected from Plan 101.
- Station default forcing/hindcast endpoints return zero points, while historical
  windows return real data: forcing `2020-12-01..2020-12-15` returns 15
  precipitation + 15 temperature rows per station; hindcasts for the same window
  return 75 discharge hindcast steps per station.
- Five non-virtual models have skill scores and embedded diagrams; virtual models
  `_bma`, `_pooled`, `_consensus` have no active artifacts and correctly show no
  skill chart.

## Implementation Plan

### Phase 1 — Link correctness

#### Task 1A — Browser-safe Prefect UI URL

**Scope**: Make local-dev dashboard links point to a browser-resolvable Prefect UI
URL. Prefer a dev-overlay environment override for `api.environment.PREFECT_UI_URL`
to `http://localhost:4200`; keep production compose behavior explicit and
document that deployments must set a browser-visible URL.

**Out of scope**: Moving Prefect behind Caddy, adding auth, or changing Prefect
server networking.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py
```

Manual smoke:

```bash
curl -sS http://127.0.0.1:8010/ | rg 'http://localhost:4200'
curl -sS http://127.0.0.1:8010/models/ | rg 'http://localhost:4200'
```

#### Task 1B — Station→basin cross-link

**Scope**: Add a minimal read-only basin detail page and link station-detail basin
cards to it. Include basin metadata and the stations belonging to that basin, with
station links back to `/stations/{station_id}/`.

**Out of scope**: Full basin management, basin maps, geometry rendering, or basin
editing.

**Implementation notes**:
- Add a dashboard route such as `/basins/{basin_id}/` in the existing dashboard
  route layer or a small new `basins.py` router.
- Include `basin_id` in the station-detail basin context.
- Render the basin name/code as a link in `stations/detail.html`.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py
```

Manual smoke:

```bash
curl -sS -i http://127.0.0.1:8010/basins/bdf942fe-6754-412a-8d7f-62c6bd90148c/
curl -sS http://127.0.0.1:8010/stations/d2667187-66b9-4c4a-9d1d-21a658120ed2/ | rg '/basins/'
```

### Phase 2 — Chart correctness

#### Task 2A — Pin model skill chart to the displayed artifact

**Scope**: Ensure the model-detail skill chart and skill-score table always use
the same active artifact. The route should select the active artifact deterministically,
and the template should pass that selected `artifact_id` to
`/api/v1/models/{model_id}/skill-chart.json?artifact_id=...`.

**Out of scope**: Redesigning skill diagrams, changing skill-score computation, or
aggregating all station artifacts into a combined model-level chart.

**Implementation notes**:
- In `model_detail`, compute and pass `active_artifact_id` in context.
- In `model_skill_chart_json`, default active-artifact resolution must be ordered
  consistently, e.g. active artifacts newest-first by `created_at`.
- The template fetch should include `artifact_id={{ active_artifact_id }}` when a
  table is rendered.
- Add a regression test with two active artifacts for one model where the older
  and newer artifacts have different skill-row counts; assert the HTML fetch URL
  and JSON endpoint target the same artifact.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py tests/unit/api/
```

Manual smoke:

```bash
curl -sS 'http://127.0.0.1:8010/models/climatology_fallback/' | rg 'skill-chart.json\\?artifact_id='
curl -sS 'http://127.0.0.1:8010/api/v1/models/climatology_fallback/skill-chart.json?artifact_id=9b834e5c-ed8e-45ec-93ea-3baec8623eb2' | jq '.series | length'
```

#### Task 2B — Historical-aware station chart defaults

**Scope**: Stop the station-detail forcing and hindcast charts from defaulting to
empty current-time windows when historical data exists. Use chart-specific default
ranges or explicit quick-range controls so observations can remain realtime-focused
while forcing/hindcasts open on their latest available historical data.

**Out of scope**: The multi-parameter observation layout from Plan 102, new data
endpoints, or changing existing endpoint contracts.

**Implementation notes**:
- If Plan 102 is implemented first, build on its station-detail JS split and keep
  observations independent from baseline/forcing/hindcast controls.
- Add station-detail context for available ranges:
  `historical_forcing` min/max by station, and `hindcast_forecasts` min/max by
  station/parameter.
- A conservative UI is acceptable: keep observation defaults as last 30 days, but
  initialize forcing and hindcast date inputs/range presets to the latest available
  historical window.
- Empty chart messages should state whether no data exists at all or only no data
  exists in the selected range.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py
```

Manual smoke:

```bash
curl -sS 'http://127.0.0.1:8010/api/v1/stations/d2667187-66b9-4c4a-9d1d-21a658120ed2/forcing.json?start=2020-12-01T00:00:00&end=2020-12-15T23:59:59' | jq '.series | keys'
curl -sS 'http://127.0.0.1:8010/api/v1/stations/d2667187-66b9-4c4a-9d1d-21a658120ed2/hindcasts.json?parameter=discharge&start=2020-12-01T00:00:00&end=2020-12-15T23:59:59' | jq '.hindcast_steps | length'
```

### Phase 3 — Regression coverage and documentation

#### Task 3A — Cross-link and empty-state regression tests

**Scope**: Add focused dashboard tests that prevent recurrence of this investigation's
NEW findings:
- Prefect links render as browser-safe local URLs under dev/test configuration.
- Station basin card links to a real basin page.
- Model skill chart fetch includes the selected artifact id.
- Virtual models with no artifacts remain valid empty pages, not errors.
- Station historical chart controls/messages distinguish no-data from wrong-range.

**Out of scope**: Full browser automation or Plotly rendering tests.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py tests/unit/api/
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
```

#### Task 3B — Dashboard ops notes

**Scope**: Update affected dashboard/operator documentation with the resolved
browser-facing Prefect URL behavior and the intended meaning of empty historical
charts.

**Out of scope**: Rewriting the full API docs or broader deployment docs.

**Verification**:

```bash
uv run pytest tests/integration/api/test_dashboard*.py
```

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1-links",
      "tasks": ["1A", "1B"],
      "parallel": true
    },
    {
      "id": "phase-2-chart-correctness",
      "tasks": ["2A", "2B"],
      "parallel": false,
      "depends_on": ["phase-1-links"]
    },
    {
      "id": "phase-3-tests-docs",
      "tasks": ["3A", "3B"],
      "parallel": false,
      "depends_on": ["phase-2-chart-correctness"]
    }
  ]
}
```

## Self-review

- Scope excludes known Plan 100/101/102 issues.
- Each task has explicit scope, out-of-scope, and verification commands.
- The only station-detail overlap is Task 2B; it explicitly waits for or builds on
  Plan 102 to avoid competing rewrites of the same JS.
- No database schema change is required.
- The plan keeps fixes read-only/user-facing and does not change forecasting,
  ingestion, QC, or model computations.
