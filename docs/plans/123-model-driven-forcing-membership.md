---
id: 123
title: Model-driven forcing membership — CONTROL_ONLY + NONE (fc-first)
status: DRAFT
depends_on: [082]
owner: unassigned
created: 2026-07-17
---

# Plan 123 — Model-driven forcing membership (CONTROL_ONLY + NONE)

> **Re-scoped 2026-07-18 (owner) to the `fc`-first deployment-critical slice.** The earlier
> full 3-membership plan escalated (2 blockers + 4 majors) because it tangled with the
> ENSEMBLE / mixed-run path — which is **deferred and non-critical** (no ensemble models in the
> live Nepal deployment; Sandro's models are control-only). This plan does **only** `NONE` +
> `CONTROL_ONLY`, and leaves the existing ENSEMBLE fetch (1 `fc` + 50 `pf`) **unchanged**. The
> ENSEMBLE-specific improvements are deferred to [[Plan 126]]. Prior full-scope history is in git.

## Problem (fc-first)

`RecapGatewayForecastAdapter.fetch_forecasts` (`src/sapphire_flow/adapters/recap_gateway.py:704`)
**hardcodes a 51-member fetch** — `fc` (member 0, `:744`) + `for member in range(1, 51)` over all
50 `pf` (`:747`) — regardless of what the assigned models need. Consequences for the live Nepal
`fc`-only deployment (confirmed live, HRU `12300`, 2026-07-17):

1. **Hard abort on missing `pf`.** ECMWF disseminates the control (`fc`/HRES) **before** the
   perturbed members (`pf`) — a normal per-cycle window, preserved by IFS Cycle 50r1. Cycle
   resolution locks onto the newest `fc` cycle, then the unconditional `pf` loop demands 50
   members that aren't published yet → the whole NWP fetch aborts. A control-only model needs
   **none** of those `pf` members.
2. **Control-only forcing is unreadable by FI `SINGLE` models.** `_pivot_nwp_records`
   (`src/sapphire_flow/services/operational_inputs.py:181`) emits member-**suffixed** columns
   whenever any record has a `member_id`; since `fc`=`member_id=0`, a control-only fetch yields
   `precipitation_0`, but FI `SINGLE`'s `_frame_with_column`
   (`src/sapphire_flow/adapters/forecast_interface.py:987`) requires the **bare** `precipitation`
   → `ConfigurationError`. So even if the fetch succeeded, the model couldn't consume it.

## What already exists (survey — do not re-derive; no FI gap)

The single-vs-ensemble distinction is **already modelled and wired**; reuse it (no FI-repo issue):

- FI expresses it: `FutureKnownVariable.ensemble_mode: EnsembleMode` (`SINGLE`/`ENSEMBLE`)
  (`forecast_interface/input/variable.py:40`).
- FI adapter projects it: `_project_requirements` sets `any_ensemble_future`
  (`adapters/forecast_interface.py:474`) and stamps `ModelDataRequirements.ensemble_mode`
  (`:525`). Domain type carries it, default `SINGLE` (`types/model.py:271`).
- Fallback models declare `future_dynamic_features=frozenset()` (need no NWP): e.g.
  `models/linear_regression_daily.py`, `climatology_fallback.py`, `persistence_fallback.py`.
- Assignment status is now **active-filtered** for operational consumers (Plan 124, merged) —
  membership aggregation must use that same ACTIVE set.

## Goal (this plan)

Decide a **run-level forcing membership** from the ACTIVE assigned models' FI signals, and act on
two of the three states; leave ENSEMBLE as today:

- **`NONE`** — no ACTIVE assigned model declares any `future_dynamic_features` → **skip the NWP
  fetch** (reuse the flow's existing `skip_nwp_fetch` runoff-only path,
  `run_forecast_cycle.py:1538`) **and gate NWP-staleness health on `not skip_nwp_fetch`**
  (`:1628`) so a genuinely-no-NWP run does not false-degrade. **Preserve** the
  `nwp_unavailable_runtime` staleness signal (that branch's `NoCycleAvailableError` handler writes
  no health record, so the staleness check is the *only* detector of a real "NWP needed but
  unavailable" failure — do NOT gate on `not effective_runoff_only`). *(Folded in from ex-124 #1.)*
- **`CONTROL_ONLY`** — ACTIVE models need NWP but **none is `ENSEMBLE`** → fetch **`fc` only**
  (member 0), **skip the `pf` loop entirely**, never abort on missing `pf`. Normalize the fetched
  control forcing to **bare** columns (D8, below) so FI `SINGLE` models consume it.
- **`ENSEMBLE`** — any ACTIVE model is `ENSEMBLE` → **unchanged**: the current 1×`fc` + 50×`pf`
  fetch and member-suffixed columns. No new behavior here (see Non-goals / Plan 126).

### D8 — control-only forcing-column normalization

When the fetch is **control-only** (the run's record `member_id`s are a subset of `{None, 0}`,
i.e. no `pf` members present), `_pivot_nwp_records` must emit **bare** feature columns
(`precipitation`, not `precipitation_0`). Key on *control-only fetch*, **NOT** "member 0 is
present": in a full ENSEMBLE fetch, member 0 is a normal ensemble member with no control status
(see [[project_recap_ifs_fc_hres_member0]]), so aliasing it to a bare column would silently change
`SINGLE`-model input semantics. Ensemble runs keep suffixed columns for fan-out.

## Non-goals (deferred to [[Plan 126]])

- **ENSEMBLE membership improvements:** requirement-aware complete-ensemble cycle resolution (the
  `fc`-before-`pf` lag as it affects ENSEMBLE runs), and any change to the ENSEMBLE fetch/columns.
  ENSEMBLE stays exactly as it is today.
- **Mixed runs** (an ENSEMBLE model and a `SINGLE`-with-NWP model on the same station/run): the
  bare-and-suffixed column coexistence. Not needed for the control-only Nepal deployment.
- **Group-membership Phase-A/B2 discovery timing** *only insofar as it is ensemble-aggregation
  specific*; the plan must still decide, in scope, how a group of control-only models contributes
  to the run's `NONE`/`CONTROL_ONLY` decision at fetch time.

## Design forks for the `plan` workflow (grill-me)

1. **Where the membership decision lives** and how it is plumbed into `fetch_forecasts` (which
   currently takes only `(station_configs, cycle_time)`): a `ForcingMembership` arg, a per-source
   field, or a flow-level pre-fetch computation. Must aggregate over ACTIVE station **and** group
   assignments available at fetch time.
2. **`CONTROL_ONLY` fetch shape** in the adapter: parameterize the member set (skip the `pf` loop)
   without disturbing the ENSEMBLE path.
3. **D8 keying** — confirm the "member_ids ⊆ {None,0}" test and where the pivot learns it.
4. **Reconcile with "ensemble-first"** (`docs/architecture-context.md`): a control-only forecast is
   a 1-member forecast at `member_id=0`; confirm the store / `NwpCycleSource` / skill paths handle
   it without special-casing.

## References

- ECMWF timing (control disseminated ahead of perturbed members; Cycle 50r1 preserves it):
  https://www.ecmwf.int/en/about/media-centre/focus/2024/plans-high-resolution-forecast-hres-and-ensemble-forecast-ens
- Merged adapter: `src/sapphire_flow/adapters/recap_gateway.py`; FI contract:
  `interface/protocol.py`, `input/requirement.py`.
