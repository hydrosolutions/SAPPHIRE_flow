# Plan 102 — dashboard: make water_level & water_temperature observations visible

**Status**: DRAFT — **grill-me COMPLETE (2026-07-06)**: layout = **multi-panel
(stacked, per-parameter axis)**; **ship independent of Plan 101** with a
**QC-failed show/hide toggle**; **scope = station-detail page only**. See DECIDED
DESIGN + IMPLEMENTATION VISION. Next: `plan-review` (WF1) → READY → implement
(hold-at-PR).
**Priority**: medium — operators report "we don't see any water level or
temperature data" on the dashboard, yet all three parameters are ingested and
served. It is a visibility/UX gap, not missing data.
**Phase**: v0b — review dashboard
**Parent**: Plan 096 (dashboard forecast graph); the multi-parameter experiment
(discharge + water_level [+ water_temperature])
**Related**:
- `src/sapphire_flow/api/templates/stations/detail.html:232-274` (obs chart:
  single `#param-select` dropdown → `/observations.json?parameter=…`; groups by
  qc_status; already labels axis "Date (UTC)")
- `src/sapphire_flow/api/routes/stations.py:388-434` (`observations.json` — returns
  ALL rows incl. `qc_statuses`, **no** qc/param filter), `:194-233` (`parameters`
  = `DISTINCT parameter` from observations → the dropdown options)
- Plan 101 (water_level `qc_failed` datum bug — why water_level renders as red
  "failed" markers even when selected)
**Created**: 2026-07-06

---

## Problem (grounded on the live local stack, 2026-07-06)

The data is present and served — the dashboard just doesn't surface it:

- The `observations` table holds **discharge, water_level, water_temperature** for
  2009/2091. The local API returns real data for each, e.g. station 2009 over the
  last 7 days: **water_temperature = 350 pts ~9.1 °C, all `qc_passed`**;
  **water_level = 350 pts ~376, mostly `qc_failed`**.
- But the station-detail **obs chart shows ONE parameter at a time** via
  `#param-select`, and the options are `sorted(DISTINCT parameter)` → **discharge
  first**, so the default view is discharge and the other two are invisible unless
  the operator knows to change the dropdown. Perceived result: "no water
  level/temperature data."
- Compounding it, **water_level currently renders as red `qc_failed` markers**
  (the Plan 101 datum/threshold bug), so even when selected it looks broken/empty
  rather than like a valid series.

So two things hide the parameters: (1) single-parameter dropdown defaulting to
discharge (discoverability), and (2) water_level's all-failed QC display (Plan 101).

## Goal

Every ingested parameter for a station is **visible and legible** on the dashboard
without hunting — discharge, water_level, and water_temperature — each with an
appropriate axis/scale and clear QC state.

## DECIDED DESIGN (grill-me 2026-07-06)

- **D1 — multi-panel (stacked), one chart per parameter.** Replace the single obs
  chart for the station-detail page with **N stacked panels**, one per parameter in
  `station["parameters"]`, each with its **own y-axis + unit**. All parameters are
  always visible — no dropdown hunting. Each panel calls the existing
  `/api/v1/stations/{id}/observations.json?parameter=…` endpoint (unchanged
  contract). Rationale: the three parameters have incompatible scales/units (m³/s,
  m a.s.l., °C), so separate axes read far better than an overlay.
- **D2 — units via a small hardcoded display map (no registry exists).** There is
  **no** parameter→unit registry today (the obs y-axis currently shows the bare
  `param` string). Add a minimal display-unit map in the template/route:
  `discharge → "m³/s"`, `water_level → "m a.s.l."`, `water_temperature → "°C"`
  (unknown params fall back to the bare name). Each panel's y-axis title becomes
  `"<param> (<unit>)"`. Keep the map in one place (a small dict) so it is easy to
  extend; a future canonical unit registry can replace it without touching the
  layout. water_level's unit is **m a.s.l.** (the absolute datum — see Plan 101).
- **D3 — ship INDEPENDENT of Plan 101 + a QC-failed toggle.** Do **not** block on
  Plan 101. Each panel keeps the existing group-by-`qc_status` rendering, plus a
  **"show QC-failed" toggle** (default **on**, but failed points **de-emphasized** —
  smaller/greyer markers) so water_level is **visible and legible now** (not an
  alarming wall of red) and simply becomes clean once Plan 101 fixes the datum
  threshold. A short inline note links water_level's current failures to Plan 101.
- **D4 — scope: station-detail page ONLY.** The observations-coverage page and the
  station list are **out of scope** for this plan (revisit separately if needed).

### Implementation vision (feeds WF1 plan-review → WF2)

- **Template (`stations/detail.html`):** replace the single `#obs-chart` +
  `#param-select`-driven obs load with a **loop that renders one panel per
  `station.parameters`** entry (server-side render N `<div>`s, or client-side build
  them from a `parameters` list injected into the page). Each panel: fetch its
  parameter, group by qc_status (existing styles), y-axis = `"<param> (<unit>)"`,
  x-axis already `"Date (UTC)"`. Add one **"show QC-failed" checkbox** (page-level
  or per-panel — plan-review to sharpen) that toggles the `qc_failed` trace
  visibility; default visible + de-emphasized style.
- **The `#param-select` dropdown drives more than obs (WATCH-OUT):** it currently
  also reloads the **baseline** and **hindcast** charts (`detail.html`
  `loadCharts()`). Those are **discharge-oriented** (climatology baseline; models
  forecast discharge). Decision for plan-review: the obs section goes
  dropdown-free (multi-panel), while **baseline + hindcast default to `discharge`**
  (or keep a smaller selector scoped to just those). Do NOT silently break the
  baseline/hindcast charts when removing the shared obs dropdown.
- **Route (`stations.py`):** no contract change needed — `parameters` is already
  computed (`:194-207`) and `observations.json` already serves per-parameter data
  with `qc_statuses`. Inject the unit map + `parameters` into the template context.
- **Verification:** render 2009/2091 at http://localhost:8010 — three panels
  (discharge m³/s, water_level m a.s.l., water_temperature °C); water_temperature a
  clean ~9 °C series; water_level visible with failed points de-emphasized + the
  Plan-101 note; the QC-failed toggle hides/shows failed markers; baseline +
  hindcast still render (discharge).

## Non-goals

- Fixing the water_level QC failures — that is Plan 101 (datum/threshold). This plan
  only ensures the series is *visible*.
- Changing ingestion or the `observations.json` contract (it already serves all
  parameters + qc_statuses).
- The forecast/hindcast charts (those are discharge-only by model design).

## Process

Grill-me **COMPLETE** (2026-07-06): D1 multi-panel, D2 hardcoded unit map, D3 ship
independent + QC-failed toggle, D4 station-detail only (see DECIDED DESIGN). One
residual for **plan-review to sharpen**: the shared `#param-select` dropdown that
also drives the baseline/hindcast charts — how to keep those working (default to
discharge vs a scoped selector) once the obs section goes multi-panel. Next: run
`plan-review` (WF1) → READY → implement. Implementation is a template + route
change (`stations/detail.html`, `stations.py` context only — no endpoint contract
change) → **hold-at-PR** with a version bump.
