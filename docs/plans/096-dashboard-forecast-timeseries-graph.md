# Plan 096 — dashboard: forecast time-series graph

**Status**: DRAFT
**Priority**: medium — operator-requested; makes the NWP-on forecasts legible at
a glance (the whole point of collecting them).
**Phase**: v0b — review dashboard (Flow 3 adjacent)
**Parent**: the developer dashboard (`api/routes/`, `api/templates/`)
**Related**: `api/routes/forecasts.py` (`/forecasts/{id}/`,
`/api/v1/forecasts/{id}/data.json`), `api/templates/forecasts/detail.html`,
`api/templates/stations/detail.html` (existing Plotly obs chart),
`api/templates/base.html` (Plotly + htmx already loaded)
**Created**: 2026-07-03

---

## Request

The forecast now shows as a **table** on `/forecasts/{id}/`. The operator wants
to **see forecasts as a time-series graph** in the small dashboard.

## What already exists (build on it, don't add a frontend)

- **Plotly is already loaded** (`base.html:9`, `plotly-2.35.2.min.js`) and htmx
  (`base.html:8`). No new frontend/build step needed — server-rendered Jinja +
  a `<div>` + a Plotly `newPlot` call, exactly like the station page.
- **The station detail page already charts** observations + baselines via Plotly
  with param-select + date-range controls + a `loadCharts()` fetch
  (`templates/stations/detail.html:133-158`) — the pattern to mirror.
- **A forecast data endpoint exists**: `/api/v1/forecasts/{id}/data.json`
  (`forecasts.py:117`) returns values grouped by `member` (spaghetti) or
  `quantile`, keyed by `lead_time_hours`. Likely needs to also emit **absolute
  `valid_time`** (or issue_time + lead → compute client-side) and units.

## Goal

Forecasts are rendered as a **time-series graph** (x = valid_time, y = discharge
m³/s) in the dashboard: the ensemble spread + a central line, ideally with recent
**observed** discharge overlaid so the forecast reads in context.

## Open design questions (grill-me before READY)

1. **Ensemble rendering.** 21-member **spaghetti** (all trajectories) vs
   **quantile bands** (p10/p50/p90 computed from members — cleaner) vs both
   (toggle). Bands are usually more legible; offer members as an option.
2. **Placement.** (a) chart on `/forecasts/{id}/` (this forecast), and/or (b)
   overlay the **latest forecast onto the station page's obs time series** (obs
   history flowing into the forecast) — arguably the most useful view. Likely
   both; (b) is the higher-value one.
3. **Observation overlay.** Pull recent observed discharge for the station
   (existing obs endpoint) and draw it up to issue_time so the forecast continues
   from the last observation. Confirm the join (station_id, parameter, units).
4. **Multi-model.** Show only the produced (PRIMARY) forecast, or overlay
   `nwp_rainfall_runoff` vs `climatology_fallback` for the same issue? Start with
   the single produced forecast; multi-model overlay is a natural follow-on and
   ties into the skill-comparison view.
5. **Endpoint shape.** Extend `data.json` to include `valid_time` + `units` +
   (optional) an `observations` series, or add a small
   `/api/v1/stations/{id}/forecast-chart.json` that bundles latest-forecast +
   recent-obs for placement (b). Keep it one fetch.

## Non-goals

- A production/polished UI or a JS build pipeline (stays server-rendered Jinja +
  Plotly, matching the existing dashboard).
- Forecast editing/adjustment (Flow 3 review workflow — separate, v2).

## Process

DRAFT until a grill-me picks rendering (bands vs spaghetti) + placement (forecast
page vs station-page overlay), then phases → READY. Small, additive: extend the
`data.json` payload + add a Plotly chart block to the template(s), mirroring
`stations/detail.html`. Tests: the endpoint returns the expected series shape;
the page renders the chart div (light template/route test).
