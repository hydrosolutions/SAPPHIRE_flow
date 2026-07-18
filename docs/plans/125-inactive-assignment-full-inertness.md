---
id: 125
title: Inactive assignments fully inert (supersede Plan 100's all-status drift contract)
status: DRAFT (stub)
depends_on: [124]
owner: unassigned
created: 2026-07-18
---

# Plan 125 — Inactive assignments fully inert

> **Stub (owner, 2026-07-18) — follow-up to [[Plan 124]].** 124 makes INACTIVE station
> assignments stop forecasting + leave the alert-priority index, but deliberately leaves the
> **fallback-priority-drift health check all-status** (respecting the locked/archived
> **Plan 100** DB-drift contract). 125 completes the picture: make an INACTIVE assignment
> **fully inert** — invisible to the drift detector too — so "inactive = gone" holds everywhere.
> Needs the `plan` workflow.

## Why it's separate from 124

Plan 100 (`docs/plans/archive/100-...md`, C1c) defined `_check_fallback_priority_drift`
(`flows/run_forecast_cycle.py:718-758`) as an **all-status DB-drift detector**: it fires DEGRADED
when any fallback-model assignment row drifts below its priority threshold, **including inactive or
raw-DB-edited rows** — a tamper/mis-config safety net. Filtering it to active-only is a **behaviour
change to a locked resilience contract**, so it must be an **explicit, owner-ratified supersession
of Plan 100 C1c**, not a silent side effect of 124's narrow fix.

## Scope (to develop)

- Filter the fallback-priority-drift check (both the station-side loop `:721-733` and the
  group-side loop `:736-753`) to **active** assignments, consistent with 124's operational filter.
- Document the **Plan 100 C1c supersession**: state precisely what drift-detection coverage is
  intentionally dropped (drift on inactive/raw-edited fallback rows no longer degrades health) and
  why that is acceptable — or provide an alternative detector if the tamper-detection value is
  still wanted.
- Reconcile any other all-status consumers of assignment status that should also become
  active-only for true full-inertness (audit `run_forecast_cycle.py` + `run_group_forecast.py`).

## Open questions (grill-me)

- Do we actually WANT to lose drift-detection on inactive rows, or keep a separate tamper check?
- Is "fully inert" needed for v1 deployment, or is 124's operational fix sufficient for now?
  (124 is the deployment-critical part; 125 is a coherence/cleanup follow-up.)

## Non-goals

- Does not re-do 124's operational filtering (forecasting/assembly/alert-priority) — builds on it.
