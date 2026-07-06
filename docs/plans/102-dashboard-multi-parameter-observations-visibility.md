# Plan 102 — dashboard: make water_level & water_temperature observations visible

**Status**: DRAFT
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

## Design options (grill-me before READY)

- **D1 — how to show multiple parameters.**
  - (a) **Multi-panel** — one small stacked chart per available parameter on the
    station-detail page (recommended: each parameter has a different unit/scale, so
    separate y-axes read better than an overlay). Keeps the existing
    `observations.json` per-parameter endpoint; just render N panels from
    `station["parameters"]`.
  - (b) **Single chart, multi-select overlay** with a secondary y-axis — compact but
    mixing m³/s, m a.s.l., and °C on shared axes is hard to read.
  - (c) **Keep the dropdown but default smarter** + show a per-parameter
    availability/count summary so operators at least see the parameters exist.
  - Recommend (a); confirm.
- **D2 — units/axis labelling.** Each parameter needs its own y-axis label + unit
  (discharge m³/s, water_level m a.s.l., water_temperature °C). Confirm the unit
  strings and whether they come from config/registry vs hardcoded.
- **D3 — QC display honesty.** water_level is all `qc_failed` until Plan 101 lands.
  Decide: does this plan depend on Plan 101 (so water_level shows as valid), ship
  independently (water_level shows as red-failed but at least visible + a note), or
  both? Recommend: ship 102 independently (visibility is orthogonal), and let Plan
  101 fix the QC colour. Cross-reference, do not block.
- **D4 — scope.** Station-detail page only, or also the station list / observations
  coverage page (`stations.py:16` coverage already lists parameters)? Recommend
  detail page first.

## Non-goals

- Fixing the water_level QC failures — that is Plan 101 (datum/threshold). This plan
  only ensures the series is *visible*.
- Changing ingestion or the `observations.json` contract (it already serves all
  parameters + qc_statuses).
- The forecast/hindcast charts (those are discharge-only by model design).

## Verification (local stack is up)

Render the station-detail page for 2009/2091 at http://localhost:8010 and confirm
all three parameters appear with correct units/axes; water_temperature shows a
clean ~9 °C series; water_level appears (as failed markers pre-101, as valid
post-101).

## Process

DRAFT until a grill-me settles D1 (multi-panel vs overlay), D2 (unit source), D3
(101 dependency). Then plan-review → implement. Implementation is a template +
route change (`stations/detail.html`, possibly `stations.py`) → **hold-at-PR** with
a version bump.
