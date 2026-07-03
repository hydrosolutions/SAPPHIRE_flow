# Plan 099 — dashboard display timezone (label axes + UTC↔Europe/Zurich toggle)

**Status**: DRAFT
**Priority**: low-medium — UX; the unlabeled UTC axis caused a real
UTC-vs-CEST misread during the 2026-07-03 Mac-mini obs investigation (a ~20 min
BAFU delay looked like ~1 h once the +2 h offset was misattributed).
**Phase**: v0b — review dashboard
**Parent**: Plan 096 (dashboard forecast graph); the developer dashboard
**Related**: `api/templates/stations/detail.html` (obs + baseline charts, x-axis
labeled only "Date" — `:272`), `api/templates/forecasts/detail.html` (forecast
chart, x-axis "Valid time (UTC)" — `:88`), `config/deployment.py`
`default_display_timezone` (`"Europe/Zurich"`, defined but **not applied
anywhere** in `api/`), the API which stores + returns everything in **UTC**
**Created**: 2026-07-03

---

## Problem

The dashboard displays **UTC** (all timestamps are stored + returned in UTC), but
the **observations chart's x-axis is labeled only "Date"** with no timezone
(`stations/detail.html:272`), so an operator on CEST (UTC+2) reads a UTC time as
local and perceives a ~2 h error. (The newer forecast chart labels its axis
"Valid time (UTC)" — this plan makes the rest consistent.) Separately,
`default_display_timezone = "Europe/Zurich"` exists in `DeploymentConfig` but is
**never wired** to the dashboard — a dangling config.

## Goal

1. **Every dashboard time axis is unambiguously labeled with its timezone.**
2. **A UI knob toggles the displayed timezone between UTC and Europe/Zurich**
   (operator convenience for local reading), DST-correct.

## Phases (proposed)

- **P1 — label axes (minimal, ship first).** Add the timezone to the obs +
  baseline chart axis titles in `stations/detail.html` (and any other unlabeled
  time axis), matching the forecast chart's "(UTC)". Cheap, removes the foot-gun
  immediately. No data change.
- **P2 — timezone toggle (the nice-to-have).** A client-side control
  (dropdown/toggle: **UTC** / **Europe/Zurich**) that re-renders the Plotly
  charts in the chosen zone. Persist the choice (localStorage). Applies across
  the obs, baseline, and forecast charts.

## Open design questions (grill-me before READY)

1. **DST correctness (the sharp one).** Europe/Zurich is **UTC+1 (CET, winter)
   and UTC+2 (CEST, summer)** — a naive fixed `+2 h` shift would be wrong half the
   year. The toggle must convert with a real tz mechanism (browser `Intl`
   / `toLocaleString('…', {timeZone:'Europe/Zurich'})`, or a small tz lib), not a
   constant offset. Confirm the approach.
2. **Client-side vs server-side conversion.** (a) Keep the API UTC-only and
   convert in the browser on toggle (simplest, no API change); (b) API applies
   `default_display_timezone` and returns localized strings + offset. Prefer (a).
3. **Default view.** UTC (explicit, unambiguous) vs the configured
   `default_display_timezone`. Recommend defaulting to **UTC** with the toggle
   opting into local — an ops dashboard benefits from an unambiguous default.
4. **Scope.** Which charts get the toggle (obs, baseline, forecast) and does the
   observations *table* / any raw timestamp text also get relabeled, or only the
   charts?
5. **Wire `default_display_timezone`?** Either use it as the toggle's "local"
   option (so it's no longer a dangling config) or explicitly drop it. Decide.

## Non-goals

- Full i18n / per-user timezone preferences (v2).
- Changing storage or API timestamps away from UTC (UTC stays the source of truth).

## Process

DRAFT until a grill-me settles DST handling + default view, then phases → READY.
P1 is a trivial template label change (could ship on its own as a quick code PR);
P2 is a small client-side Plotly + a persisted toggle. Tests: template smoke that
the axis carries a tz label; a light check that the toggle re-renders (or unit
the conversion helper if one is added).
