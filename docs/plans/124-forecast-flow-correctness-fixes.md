---
id: 124
title: Forecast-flow correctness fixes (pre-existing bugs surfaced by the Plan 123 review)
status: DRAFT (stub)
depends_on: []
owner: unassigned
created: 2026-07-17
---

# Plan 124 — Forecast-flow correctness fixes

> **Stub — near-term priority (owner, 2026-07-17).** The Plan 123 `plan`-workflow review
> surfaced several correctness issues that are **pre-existing in the live forecast flow**
> (`run_forecast_cycle.py` + the input-assembly/FI path), independent of 123's new feature.
> Per the owner: *"we cannot afford bugs"* and the critical path is `fc`-first — so these
> are fixed **before** [[Plan 123]] resumes. Each item below is **review-flagged and must be
> VERIFIED (reproduced red-first) before fixing** — Codex grounded them at `file:line`, but
> some are latent (trigger only under specific configs). Run the `plan` workflow to develop +
> pressure-test, then `implement` with a red-first test per bug.

## Candidate defects (verify → fix, most operationally-relevant first)

### 1. Runoff-only / no-NWP runs still run NWP-staleness health
The flow runs `_check_nwp_grid_staleness` whenever `nwp_enabled` is true
(`run_forecast_cycle.py:1628`); that check marks stale when **no latest cycle exists**
(`:691`) and degrades pipeline health via `nwp_grid_stale` (`:2267`). A station/run whose
assigned models need **no NWP** (all fallback/runoff-only) can therefore emit a **false
NWP-staleness health degradation**. **Likely affects the live Swiss deployment** (runoff-only
stations). Fix: gate NWP-staleness on *"NWP is required for this run"*, not merely
*"the adapter is enabled."* Regression: a runoff-only run emits no NWP-delivery health record
and no `nwp_grid_stale` degradation.

### 2. Station-vs-group active-assignment semantics are inconsistent
`fetch_model_assignments` returns **all** statuses (`station_store.py:212`). The **station**
path uses every fetched assignment for superset assembly and forecasting
(`run_forecast_cycle.py:1440`, `run_station_forecast.py:327`), while the **group** path
filters to **active** assignments (`run_forecast_cycle.py:2034`). So an *inactive* station
assignment is still forecast on the station path — an inconsistency that can forecast with a
retired/disabled model. Fix: define and share **one active-assignment set** for both paths.
Regression: an inactive assignment is excluded from station forecasting + input assembly.

### 3. Forcing-column shape breaks FI `SINGLE` consumers in mixed runs
`_pivot_nwp_records` emits **only** member-suffixed (`feature_member`) columns when any member
is present (`operational_inputs.py:181`), but FI `SINGLE` prediction goes through direct
`model.predict` and `_frame_with_column` requires the **bare** feature name
(`forecast_interface.py:986`). A run that mixes an `ENSEMBLE` model and a `SINGLE`-with-NWP
model on the same station therefore breaks the `SINGLE` consumer. **Latent** — triggers only
under a mixed assignment; confirm whether any current/near config hits it. Fix: project a
**bare control column whenever `member_id=0` is present** (even in full ensemble runs), while
retaining suffixed columns for fan-out. Regression: a `SINGLE` and an `ENSEMBLE` model both
forecast in one mixed run. (This is the D8 territory shared with 123 — decide ownership.)

## Notes / non-goals

- Does **not** implement Plan 123's model-driven membership feature — only the pre-existing
  correctness fixes that must be clean first. (123's group-membership Phase-A/B2 timing concern
  stays with 123, since nothing aggregates run-level membership today.)
- Verify each defect against the code and (where feasible) a repro before committing to a fix —
  do not fix a "bug" that cannot be reproduced.
- Source: Plan 123 `plan`-workflow escalation, 2026-07-17 (see `docs/plans/123-...md`
  "Open blockers").
