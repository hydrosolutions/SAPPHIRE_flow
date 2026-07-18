---
id: 126
title: Ensemble forcing membership — requirement-aware cycle resolution + mixed runs
status: DRAFT (stub)
depends_on: [123]
owner: unassigned
created: 2026-07-18
---

# Plan 126 — Ensemble forcing membership (deferred from 123)

> **Stub (owner, 2026-07-18) — follow-up to [[Plan 123]], NOT deployment-critical.** 123 does the
> `fc`-first slice (`NONE` + `CONTROL_ONLY`) and leaves the existing ENSEMBLE fetch (1 `fc` + 50
> `pf`, member-suffixed columns) **unchanged**. 126 improves the ENSEMBLE path. There are no
> ensemble models in the live Nepal deployment (Sandro is control-only), so this waits until an
> ensemble model is actually assigned. Needs the `plan` workflow.

## Scope (to develop)

- **Requirement-aware complete-ensemble cycle resolution.** Today `resolve_latest_cycle`
  (`src/sapphire_flow/adapters/recap_gateway.py`) probes `fc` only, so an ENSEMBLE run can lock
  onto a cycle where `fc` is published but `pf` is not yet complete (the ECMWF `fc`-before-`pf`
  window) and then fail. For ENSEMBLE membership, resolve to the newest cycle where the **full
  required member set** is available (walk back past `fc`-only cycles).
- **Mixed runs.** A run with both an ENSEMBLE model and a `SINGLE`-with-NWP (control-consuming)
  model on the same station: the forcing must present **both** a bare control column (for the
  `SINGLE` consumer) **and** member-suffixed columns (for the ENSEMBLE fan-out) — the D8
  coexistence 123 deliberately left out (123 handles pure control-only vs pure ensemble).
- **Group-membership aggregation timing** for ensemble runs (Phase-A fetch vs Phase-B2 group
  discovery), if 123 didn't already resolve it for the control-only case.

## Open questions (grill-me)

- Is any ensemble model expected before v1 go-live? If not, this stays parked.
- Does the missing-`pf` graceful-degrade path (map to `RecapDataUnavailableError` → degrade) still
  need building for ENSEMBLE, or does requirement-aware cycle resolution make it moot?

## Non-goals

- Does not touch 123's `NONE`/`CONTROL_ONLY` behavior.
