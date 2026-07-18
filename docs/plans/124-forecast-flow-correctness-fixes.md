---
id: 124
title: Station-vs-group active-assignment consistency (forecast-flow correctness)
status: DRAFT
depends_on: []
owner: unassigned
created: 2026-07-17
---

# Plan 124 — Station-vs-group active-assignment consistency

> **Narrowed 2026-07-18 (owner).** Originally a 3-defect "pre-existing forecast-flow bugs"
> stub. The Plan 123 `plan`-workflow review re-assessed those: only the active-assignment
> inconsistency (below) is a genuine, standalone, currently-live correctness bug. The other
> two were **folded into [[Plan 123]]**: the NWP-staleness gating (defect #1) only becomes real
> once 123 introduces model-requirement-driven skip (`NONE` membership), and the control-only
> forcing-column normalization (defect #3 / D8) is part of 123's control-only slice. So 124 is
> now **one bounded fix** — small enough that it can likely go straight to `implement` with a
> red-first test after this `plan` pass confirms the call-site impact.

## Problem

The station and group forecast paths treat model-assignment **status** inconsistently:

- `StationStore.fetch_model_assignments` returns **all** statuses, unfiltered
  (`src/sapphire_flow/store/station_store.py:212` — `select … where station_id == …`, no status
  predicate).
- The **station** path consumes every fetched assignment as-is — batch-fetched at
  `src/sapphire_flow/flows/run_forecast_cycle.py:1440-1443` and used for input-superset assembly
  and forecasting (`src/sapphire_flow/services/run_station_forecast.py:327`), with **no**
  assignment-status filter anywhere on the path (grep-confirmed clean).
- The **group** path filters to `status == ModelAssignmentStatus.ACTIVE`
  (`src/sapphire_flow/flows/run_forecast_cycle.py:2039`).

**Consequence:** an **INACTIVE** station model-assignment is still forecast on the station path,
i.e. a retired/disabled model keeps producing operational forecasts — while the same status on a
group assignment is correctly excluded. This is a live correctness inconsistency, not latent.

Scope is bounded: `ModelAssignmentStatus` has only `ACTIVE` / `INACTIVE`, so "filter to active"
fully defines the intended behaviour.

## Verification (2026-07-17, orchestrator — CONFIRMED against the code)

`fetch_model_assignments` (`station_store.py:212`) has no status filter; the station path
(`run_forecast_cycle.py:1440-1443`) consumes all statuses and `run_station_forecast.py` applies no
status filter (grep clean); the group path filters `ACTIVE` (`run_forecast_cycle.py:2039`). The
asymmetry is real and reproducible: an inactive station assignment reaches forecasting today.

## Goal

Make station and group paths agree: **only `ACTIVE` model-assignments forecast**, on both paths.

## Open questions for the `plan` workflow (decide before implement)

1. **Fix shape.** One shared `active-assignment` helper/filter called from both the station and
   group paths (single source of truth), or filter at each path independently? The plan should
   pick the shared abstraction's location + signature (e.g. filter at the store method, or a
   small pure helper applied right after fetch).
2. **Call-site impact (the risk to walk).** Filtering to ACTIVE at the station path touches both
   input-superset assembly and forecasting (`run_forecast_cycle.py:1440`,
   `run_station_forecast.py:327`). Confirm this does **not** change `forecasts_stored` counts,
   health status, or superset shape for **currently-passing** runs (i.e. runs whose assignments
   are already all ACTIVE) — the fix must be a no-op there and only exclude genuinely-inactive
   assignments. Add a regression proving a currently-all-active run is unchanged.
3. **Should the store filter, or the caller?** If `fetch_model_assignments` itself filters ACTIVE,
   verify no other caller relies on receiving inactive assignments (e.g. admin/reporting reads).

## Acceptance (red-first)

- An **inactive** station model-assignment is **excluded** from station forecasting and input
  assembly (red against current code: it is currently included).
- A station whose assignments are **all active** produces the **same** forecasts / health as
  before (no regression).
- Station and group paths apply the **same** active-only rule.

## Non-goals

- Defect #1 (NWP-staleness gating) and defect #3 / D8 (control-only forcing-column normalization)
  are **out of scope — moved to [[Plan 123]]** (they only become real as part of 123's
  model-driven membership). See 123's "Folded-in from 124" note.
- Does not touch model-driven forcing membership (that is 123).

## Source

Plan 123 `plan`-workflow escalation (2026-07-17) + its 124-review re-assessment; narrowed by owner
decision 2026-07-18. Prior 3-defect history is in git (`717d57b` and earlier).
