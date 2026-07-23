---
status: DRAFT
created: 2026-07-23
plan: 144
title: Two-track probabilistic forecasting (daily ≤15 d + sub-daily ≤3 d) over the IFS ensemble
scope: v1 forecast architecture for DHM/Nepal — drive BOTH a daily track (≤15 d) and a sub-daily track (≤3 d, 4×/day) from the IFS ensemble (51 members = fc + pf 1..50) via the recap Data Gateway. Per-member forcing assembly, `task.map` fan-out over members, deterministic-per-replicate model calls, ensemble aggregation. Includes a forcing-assembly SEAM so we are NOT blocked on a future gateway ensemble-operational endpoint. Forecast cycle / orchestration.
depends_on: [081, 082, 121, 139]
blocks: []
supersedes: []
---

# Plan 144 — Two-track probabilistic forecasting over the IFS ensemble

## Status
**DRAFT — architecture, aligned with owner 2026-07-23; needs `/plan` before READY.** Grounded in a live probe
of the recap gateway (HRU 12300, [[reference_recap_gateway_12300_products]]) and the confirmed model contract
(models are deterministic-per-replicate; the ensemble fan-out lives in SAP3, not the model). This is v1 (Nepal
DHM) architecture; it sits on the forcing spine (Plans 081/082/121) and the per-basin models (Plan 139, aquacast
FI models).

## Problem
v1 must produce **probabilistic** river forecasts on two time-scales, from the same upstream ensemble:
- a **daily** track out to **~15 days**, and
- a **sub-daily** track out to **~3 days** (take more if the gateway delivers it), refreshed **4×/day**.

The upstream is the ECMWF **IFS ensemble via the recap gateway**: **51 members** (control `fc` + perturbed
`pf` 1..50), 3-hourly, ~15-day horizon (live-confirmed at 00Z; 06/12/18Z `pf` availability TBD); plus JSNOW
snow products (swe hourly 6–10 d; hs/rof subscription added, forecast not yet materialized). Our models
(aquacast, via the ForecastInterface) **cannot consume an ensemble** — each call takes one deterministic
replicate's forcing. So SAP3 must (1) assemble each member's forcing series, (2) fan out one model call per
member, (3) aggregate the 51 outputs into the ensemble forecast — for each track.

## Design decisions (owner-aligned 2026-07-23)

- **D1 — both tracks are probabilistic (51 members).** Not "daily=control, sub-daily=ensemble" — both tracks
  run the full 51-member ensemble; they differ only in temporal resolution (daily-aggregated vs native
  sub-daily) and horizon (≤15 d vs ≤3 d+). Control (`fc`) is member 0 of the same ensemble, not a separate path.

- **D2 — per-member forcing = ERA5-Land (shared past) ⧺ per-member gap-fill ⧺ per-member forecast.** The
  **observed past** (older than the reanalysis-lag cutoff, ~6–7 d) is ERA5-Land — **shared across all members**.
  The **gap-fill window** (reanalysis cutoff → current cycle) and the **forecast tail** are **per-member**: each
  member's own trajectory, so near-term ensemble spread is preserved (using control gap-fill for all members
  would collapse near-term spread). Provenance columns (`source`/`source_run`) drive the leakage-free join.

- **D3 — forcing-assembly SEAM (the un-blocking decision).** Define an `EnsembleForcingProvider` Protocol
  (`assemble(hru_code, member, cycle, variables, resolution) -> DataFrame`). Two implementations behind one
  interface:
  - **`ClientStitchedEnsembleForcing` (build NOW, unblocked):** SAP3 assembles per-member forcing client-side —
    ERA5-Land reanalysis (`ecmwf.era5_land_reanalysis`) for the shared past, then slab-stitches member *m*'s
    near-analysis from recent `pf` cycles for the gap window, then appends member *m*'s `ifs_forecast(pf,
    member=m)` tail. Expensive (≈50 members × gap-days × cycles of fetches per basin per run) but feasible; may
    start with a member subset for dev.
  - **`GatewayEnsembleOperational` (drop-in LATER):** a single server-side call per member (or one call
    returning all members) once the gateway ships an ensemble version of `operational` (the pending
    `nwp-ensemble` endpoint — see the upstream ask in §Open items). Swapping the provider is a one-line change;
    **the rest of the plan does not depend on which implementation is active.** *This seam is why the ~1–1.5
    month gateway timeline does NOT block Plan 144.*

- **D4 — fan-out in the SAP3 forecast-cycle flow via `task.map`, deterministic model per member.** The forecast
  flow maps over the 51 members: each mapped task assembles one member's forcing (D2/D3) and calls the model
  with **only that member's series** (FI `ensemble_mode=SINGLE`). The **"ensemble wrapper" is the flow, not a
  model** — the model never sees an ensemble, matching its constraint. SAP3 then aggregates the 51 per-member
  forecasts into the ensemble product (quantiles/spread) for storage + API. One **trained artifact is reused**
  across all 51 calls (artifact scope unchanged — STATION/GROUP, one artifact, called per member).

- **D5 — future ensemble-consuming model is a drop-in, not now.** If a future model can ingest a forcing
  ensemble directly, the FI already carries `ensemble_mode` on `FutureKnownVariable`; that model would take one
  call with all members instead of the fan-out. Design for `SINGLE` now; keep the aggregation boundary clean so
  the fan-out can collapse later without reworking storage/API.

- **D6 — daily vs sub-daily derive from the SAME per-member series.** Assemble each member's forcing once, then
  derive: **daily** = aggregate to daily (sum flux e.g. precip, mean state e.g. temp) and feed the daily model
  (≤15 d); **sub-daily** = keep native resolution (3-hourly IFS / hourly JSNOW) and feed the sub-daily model
  (≤3 d+). Units are gateway-native (metres, Kelvin, UTC — [[reference_recap_gateway_12300_products]]);
  transform to each model's required units at the FI boundary.

## Non-goals
- The gateway ensemble-operational endpoint itself (upstream; we adapt to it via D3). Rating-table
  level→discharge (DHM-obs track). Alerting/dashboard (v1.x). The onboarding path (Plan 143).

## Phases (sketch — harden in `/plan`)
1. **`EnsembleForcingProvider` Protocol + `ClientStitchedEnsembleForcing`** (D2/D3) — the per-member stitcher
   (ERA5 past + per-member gap-fill slabs + per-member forecast), with provenance-aware joins; fakes for tests.
2. **Ensemble fan-out in the forecast cycle** (D4) — `task.map` over members; per-member model call
   (`ensemble_mode=SINGLE`); 51→ensemble aggregation (quantiles/spread) + storage.
3. **Two-track wiring** (D6) — daily aggregation + daily model; sub-daily native + sub-daily model; horizon caps.
4. **`GatewayEnsembleOperational` provider** (D3) — drop-in behind the seam once the endpoint ships.
5. **Verification** — live per-member forcing correctness (provenance continuity), ensemble spread sanity,
   `task.map` scaling, horizon/cadence per cycle.

## Dependencies
- **081** (recap-dg-client forcing adapter) / **082** (gateway operational readiness) / **121** (Flow-6 +
  integration follow-ons) — the forcing spine this builds on.
- **139** (Nepal 12300 model) and the **aquacast FI adapter** — the deterministic-per-replicate models fanned out.
- Artifact-scope + model onboarding (one artifact reused per member).
- v0b remainder **`task.map` parallelisation** — this is its first real operational consumer.

## Open items / to confirm
- **`pf` cycle availability at 06/12/18Z** (only 00Z live-confirmed) — governs the 4×/day sub-daily cadence.
- **Per-member gap-fill cost vs the gateway endpoint** — the client-side stitch is heavy; the upstream ask
  (§Gateway request in the session) may land in ~1–1.5 months (holidays). Seam D3 keeps us unblocked meanwhile.
- **hs/rof forecast availability** (subscription added; forecast not yet materialized for 12300).
- **Member count for production vs dev** (all 50 pf, or a subset while client-side stitching).
- **Horizon per cycle** (00/12Z reach ~15 d; 06/18Z shorter) — daily-track horizon is cycle-dependent.
