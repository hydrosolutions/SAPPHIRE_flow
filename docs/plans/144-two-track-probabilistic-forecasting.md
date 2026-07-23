---
status: DRAFT
created: 2026-07-23
plan: 144
title: Multi-track probabilistic forecasting — per-station tracks driven by assigned models, over the IFS ensemble
scope: Run one probabilistic forecast per assigned model at that model's own (resolution, horizon), by partitioning a station's assignments into forcing-requirement tracks (e.g. a daily ≤15 d track and a 3-hourly ≤3 d sub-daily track), assembling per-member forcing at each track's resolution, and reusing the EXISTING ensemble fan-out. Thin orchestration that COMPOSES existing infrastructure (ensemble_fanout, ForecastEnsemble, forecast_qc) + Plans 134/126/139 + a new snow-forcing plan. Forecast cycle.
depends_on: [126, 134, 139, 145]
blocks: []
supersedes: []
---

# Plan 144 — Multi-track probabilistic forecasting over the IFS ensemble

## Status
**DRAFT — re-grounded 2026-07-23 after a `/plan` reckoning + owner fork decisions.** The first draft was written
too high and proposed building machinery that already exists; this version COMPOSES the existing stack. Owner
resolved the design forks (below). Needs a confirming `/plan` before READY. Live gateway facts (HRU 12300:
ERA5-Land hourly; IFS fc/pf 3-hourly, ~15 d, 50 pf members + fc; JSNOW swe hourly): a sanitized probe artifact
should be checked into the repo (open item) rather than cited from a memory file.

## Problem
v1 must produce **probabilistic** forecasts, and a station may carry **models with different forcing
requirements** — e.g. a daily model (≤15 d) and a 3-hourly sub-daily model (≤3 d, refreshed at 00/06/12/18Z).
The forecast cycle today selects **one** `time_step` from the highest-priority assignment and does **one** input
assembly per station (`run_forecast_cycle.py:1787-1866`, `operational_inputs.assemble_station_operational_inputs`
single global `time_step`), so it cannot serve two different (resolution, horizon) requirements at once. And the
forcing must be assembled **per ensemble member** (the models are deterministic-per-replicate), then fanned out.

## What ALREADY exists — 144 composes, does NOT rebuild
- **`services/ensemble_fanout.py`** — runs one deterministic model call **per member** and assembles the result;
  **requires STATELESS models** (raises `_STATEFUL_ENSEMBLE_UNSUPPORTED` on any prior/returned state). This *is*
  the "ensemble wrapper" — do not build another. Each member is a **single** deterministic call.
- **`types/ensemble.py` `ForecastEnsemble.from_members`** (member-form assembly) + **`services/forecast_qc.py`**
  (spread/QC). 144 does not compute quantiles/spread itself.
- **Plan 134** — the **control** operational-forcing bridge + resolution rules (**6-hourly floor** for the
  gateway control `operational`). Governs the control/daily bridge; the sub-daily *ensemble* path here is separate.
- **Plan 126** — **ensemble forcing membership: requirement-aware cycle resolution + mixed runs.** Owns member
  availability + the cycle walk-back. 144 consumes it.
- **Plan 139** — the daily model for 12300. The **sub-daily** model is built by **aquacast** (external).
- **Recap adapter** already emits SAP3 **canonical units** (precip m→mm, temp K→C); the FI adapter only
  labels/validates units — no numeric conversion at the FI boundary. (Snow unit magnitudes still unresolved — 139.)

## Design decisions (owner-resolved 2026-07-23)
- **D1 — tracks are data-driven by the assigned models, not a fixed "2".** Partition a station's active
  assignments into **forcing-requirement groups** by their required (resolution/`time_step`, horizon). A station
  with one model = one track; a station with a daily + a sub-daily model = two tracks. Run **one input assembly
  per distinct group**, feed each model only its group's timestamps/horizon, then merge/store all result sets.
  This is the concrete fix for the "one assembly per station" blocker (`run_forecast_cycle.py:1787-1866`).
- **D2 — the sub-daily promise is 3-hourly, ≤3 days, refreshed 00/06/12/18Z; the daily track is ≤15 days.** Both
  tracks are **probabilistic** (per-member; control `fc` is member 0). Deliverable resolutions:
  - **Sub-daily 3-hourly** is achievable from the **raw ensemble** (IFS `pf` is natively 3-hourly; ERA5-Land is
    hourly), assembled **client-side per member** — NOT via Plan 134's control bridge (6-hourly). Plan 134's 6h
    floor applies to the control `operational` endpoint; this ensemble path builds its own 3h series. **Must
    live-verify** the 3h per-member gap-fill seam (Plan 134's explicit caution that finer-than-6h "is not
    promised until live-verified").
  - **Daily** per-member forcing may be assembled at 6h and **aggregated up** to daily — you can aggregate up,
    never synthesize down.
- **D3 — per-member forcing = ERA5-Land (shared past) ⧺ per-member gap-fill ⧺ per-member forecast, at the
  track's resolution.** Behind an `EnsembleForcingProvider` **seam**: `ClientStitchedEnsembleForcing` (build now —
  ERA5 for the shared observed past, per-member near-analysis slabs from recent `pf` cycles for the gap window,
  per-member `ifs_forecast(pf, member=m)` tail; all at the requested resolution) vs `GatewayEnsembleOperational`
  (drop-in when the gateway ships a **3-hourly** ensemble-operational endpoint — the pending upstream ask). The
  seam is why the ~1–1.5-month gateway timeline does not block 144.
- **D4 — fixed per-model horizon + walk-back (compose Plan 126).** Each model declares a **fixed** horizon (FI
  derives it from `future_steps`); the cycle uses the latest run that supplies **≥** that many clean steps for
  **every** required member, **walking back** to an earlier cycle when the freshest (e.g. a short 06/18Z) run is
  insufficient. NOT cycle-dependent horizon shortening — the FI/coverage contract refuses a model without its full
  declared horizon (`forecast_interface.py:471-527`, `run_station_forecast.py:123-150`). Plan 126 owns this
  resolution; 144 wires the per-track model call to it.
- **D5 — reuse `ensemble_fanout`; the aquacast track models MUST be stateless.** Each track's model runs through
  the existing fan-out (one deterministic call per member). **Add a hard gate/acceptance test** that the daily
  (139) and sub-daily (aquacast) models return `new_state=None` and need no prior state; a stateful model cannot
  use the fan-out unchanged (per-member state would be a separate effort).
- **D6 — the sub-daily model is external (aquacast), integrated when ready.** 144 builds the **track** (assembly
  + fan-out + storage) so the sub-daily model drops in on delivery; the daily track uses Plan 139. Until the
  aquacast sub-daily model lands, the sub-daily track has assembly + fan-out but no consumer — that is expected.

## Non-goals (owned elsewhere)
- The ensemble fan-out / `ForecastEnsemble` / spread-QC (exist). The control forcing bridge + 6h resolution rules
  (**Plan 134**). Ensemble membership / cycle walk-back mechanics (**Plan 126**). **Snow-forcing wiring**
  (`fetch_snow_forecast` has no production caller — the Plan 139 gap; owner: **Plan 145 (snow-forcing wiring)** —
  144 depends on it for any JSNOW-fed model). The sub-daily model itself (aquacast). Rating/obs (DHM track).

## Phases (sketch — harden in `/plan`)
1. **`EnsembleForcingProvider` seam + `ClientStitchedEnsembleForcing`** (D3) — per-member stitch at a requested
   resolution (3h for sub-daily, 6h→daily for daily), provenance-aware; fakes for tests. Live-verify the 3h seam.
2. **Per-track partitioning + assembly in the forecast cycle** (D1) — group active assignments by forcing
   requirement; one assembly per group at its resolution/horizon; feed each model only its own group.
3. **Fan-out per track via the existing `ensemble_fanout`** (D5) — one deterministic call per member;
   `ForecastEnsemble` assembly + `forecast_qc` spread; merge/store both tracks' results. + the stateless gate.
4. **Horizon walk-back** (D4) — wire per-track model calls to Plan 126's requirement-aware cycle resolution.
5. **`GatewayEnsembleOperational` provider** (D3) — drop-in behind the seam when the 3h ensemble endpoint ships.

## Dependencies
- **126** (ensemble membership / cycle walk-back) · **134** (control forcing bridge + resolution floor) ·
  **139** (daily model) · **145** (snow-forcing wiring) · **aquacast** (sub-daily model). Reuses existing
  `ensemble_fanout` / `ForecastEnsemble` / `forecast_qc`.

## Open items / to confirm
- **06/12/18Z `pf` availability** (only 00Z live-confirmed) — governs the 4×/day sub-daily refresh; drives D4
  walk-back frequency. Confirm with the gateway team.
- **Live-verify the 3h per-member gap-fill seam** (Plan 134 caution) — the crux of the 3-hourly promise.
- **aquacast model statelessness** (D5) + **sub-daily model readiness** (D6).
- **Plan 145** (snow-forcing wiring) — drafted 2026-07-23; hard dependency for JSNOW-fed models; needs `/plan`.
- **Check in a sanitized 12300 probe artifact** so the resolution/horizon/units facts are repo-verifiable.
- **Gateway ensemble-operational must be 3-hourly** (not the control bridge's 6h) — update the upstream ask.
