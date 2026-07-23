---
id: 126
title: Requirement-aware ensemble cycle resolution — typed fetch-requirements + walk-back to the latest complete cycle
status: DRAFT
depends_on: [123]
owner: unassigned
created: 2026-07-18
---

# Plan 126 — Requirement-aware ensemble cycle resolution

## Status
**DRAFT — re-grounded + NARROWED 2026-07-23** after a `/plan` reckoning (the earlier stub ballooned into a
contradictory 6-phase build). Scope now locked to *just* requirement-aware ensemble cycle resolution, with the
cross-cutting decisions resolved in **`docs/design/v1-forecasting-decisions.md`** (D1 exact-51 + walk-back; D3
`pf` 00Z-only today; D4 walk-back-only; D5 narrow-126). Everything else the stub bundled is evicted (see
Non-goals). Consumed by Plan 144. Needs a confirming `/plan`.

## Problem
Two gaps make the ENSEMBLE fetch fragile, and one makes walk-back unsafe:
1. **Cycle resolution is `fc`-only.** `_resolve_effective_cycle` (`recap_gateway.py:673-702`) walks back to the
   newest *available* cycle via `resolve_latest_cycle`, but availability is probed on **control (`fc`) only**
   (Task 2B). An ENSEMBLE run can therefore lock onto a cycle where `fc` is published but `pf` is not — the ECMWF
   `fc`-before-`pf` window, **or** a 06/12/18Z cycle that never gets `pf` at all today (D3) — and then fail.
2. **The fetch carries no per-station requirements.** `fetch_forecasts(station_configs, cycle_time)`
   (`protocols/adapters.py:18`; `recap_gateway.py:704`) receives only bindings + a cycle, and Phase A submits all
   bindings in one batch (`run_forecast_cycle.py:1630`). It cannot know which stations need the full **ensemble**
   vs only **control**, nor each model's required **horizon** — so it cannot decide completeness per requirement.
3. **Walk-back can contaminate.** `_accumulate_member` mutates a **shared** accumulator immediately
   (`recap_gateway.py` accumulate path); if a candidate cycle accumulates `fc` + members 1..26 before discovering
   member 27 missing, walking back leaves those rejected rows mixed with the older cycle. No rollback exists.

## Decisions (locked — see `docs/design/v1-forecasting-decisions.md`)
- **D1 — exact-51 completeness for ENSEMBLE; walk back to the latest COMPLETE cycle.** A cycle satisfies an
  ENSEMBLE requirement only if all 51 members (`fc` + `pf` 1..50) are present for every required feature at the
  required horizon. `min_operational_ensemble_size` (`config/deployment.py:123`, default 20) stays an **output**
  eligibility gate ("publish if ≥N members survive QC") — it is **not** a fetch-time input floor.
- **D3 — `pf` is 00Z-only today.** So for ENSEMBLE requirements the walk-back lands on the latest **00Z** cycle
  (06/12/18Z have no complete ensemble). Practically the sub-daily ensemble resolves **once/day**; becomes 4×/day
  when the gateway produces `pf` at all cycles. `max_cycle_age_hours` must be large enough to reach the last 00Z
  from an 18Z-nominal run (≥ ~18–24 h) — a config check, not new machinery.
- **D4 — walk-back-only, no retry.** Extend the existing bounded walk-back; no clock/wait/cancellation surface.

## Design
- **A typed fetch-requirements object (replaces a `FetchMode` enum).** Build, per station/binding, a typed
  requirement carrying: **required IFS features**, **required horizon** (`future_steps`/`time_step`), and
  **assembly mode** (does any active model need the ENSEMBLE, or only CONTROL). It is the **union** across a
  station's active assignments. **ENSEMBLE ⊇ CONTROL** (`fc` = member 0), so a station carrying both an ensemble
  model and a control model just fetches the ensemble; how a control model then reads member 0 from an
  ensemble frame is a **downstream assembly concern (out of scope — see Non-goals)**. Thread this object through
  `fetch_forecasts` (Protocol `protocols/adapters.py:18` + implementors: `recap_gateway`, `meteoswiss_nwp`,
  `replay/nwp`, fakes/fixtures). Non-recap adapters ignore the ensemble fields.
- **Requirement-aware cycle resolution.** Extend `_resolve_effective_cycle` so the walk-back accepts a candidate
  cycle only when it satisfies the batch's requirements: **completeness** (exact-51 for ENSEMBLE features; `fc`
  for CONTROL) **and horizon** (≥ required `future_steps` for every required member). Bounded by
  `max_cycle_age_hours` (D4).
- **Candidate-local accumulation.** Fetch + validate each candidate cycle into a **fresh, candidate-local**
  accumulator; **commit only** when the full completeness + horizon contract passes; **discard** on failure so no
  partial rows and no rejected-cycle provenance ever reach the accepted result. Regression test: rejected-cycle
  member values/provenance never appear after a walk-back.
- **Completeness verification — interim vs durable.** *Durable:* a gateway **completeness manifest** (decisions
  note D2 — pending, ~1–2 mo) lists which members exist per cycle → exact detection, including a missing middle
  member. *Interim (today):* the gateway disseminates `pf` **all-together** (Plan 127: an `fc`-before-`pf` window,
  then all 50 appear at once), so a cheap probe — `fc` + the **first** `pf` member — determines `pf`
  present-or-absent for a cycle. This is sufficient **while `pf` is all-or-nothing per cycle**; a ≤2-call probe
  **cannot** detect a missing *middle* member, so the plan explicitly relies on the all-or-nothing property and
  names the manifest as the durable replacement (do not claim missing-middle detection without it).

## Non-goals (evicted from the ballooned stub; each is its own concern)
- **Mixed-column assembly** — presenting both a bare control column (for a `SINGLE` consumer) and member-suffixed
  columns (for the ensemble fan-out) from one fetch. That is a **downstream assembly** change (Plan 144 /
  operational-inputs), not cycle resolution. 126 only decides *what set to fetch*.
- **Group-membership discovery timing** (Phase-A fetch vs Phase-B2 group discovery) — a forecast-cycle
  restructuring concern; 126 scopes requirements to **station-level** assignments (like Plan 145).
- **Snow membership / broadcast** — Plans 145/146. **Per-assignment `prior_state`** sharing fix — a separate
  forecast-cycle bug-fix. 123's `NONE`/`CONTROL_ONLY` behaviour — unchanged.

## Phases (sketch — harden in `/plan`)
1. **Typed fetch-requirements object + Protocol threading** — build per-station requirements (features/horizon/
   mode) from active station assignments; thread through `fetch_forecasts`; non-recap implementors ignore it.
2. **Requirement-aware walk-back** in `_resolve_effective_cycle` — accept a candidate only on completeness +
   horizon; `max_cycle_age_hours` config check for the 00Z reach (D3).
3. **Candidate-local accumulation** — fresh accumulator per candidate, commit-on-pass, no contamination.
4. **Docs** — `docs/standards/orchestration.md` (cycle resolution) + the forecast-cycle touchpoint map; reference
   the decisions note.

## Dependencies
- **123** (the `fc`-first `NONE`/`CONTROL_ONLY` slice this extends). `docs/design/v1-forecasting-decisions.md`
  (D1/D3/D4/D5). Consumed by **144** (the ensemble fan-out relies on a complete, requirement-satisfying cycle).

## Open items / to confirm
- **Gateway completeness manifest** (decisions note D2) — the durable fix for missing-middle detection; interim
  relies on `pf` all-or-nothing per cycle.
- **`max_cycle_age_hours` value** — must reach the last 00Z from an 18Z nominal (D3); confirm the current config.
- **Where mixed-column assembly lands** — a small follow-up (144 or operational-inputs) once an ensemble model
  actually coexists with a control model on one station.
