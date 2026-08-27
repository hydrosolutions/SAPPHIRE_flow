---
status: DRAFT
created: 2026-08-27
plan: 203
title: Forecast-cycle scaling to ~100 stations — measure the two costs that actually grow
scope: One measurement of how NWP extraction and model execution scale with station count, and a decision on whether the 441 s download needs parallelising. No optimisation is authorised by this plan — it exists to make the next plan grounded.
depends_on: []
blocks: []
source: Measured on the mac mini 2026-08-27 while trialling `forecast_combination_strategy = pooled` (PR #214)
---

# Plan 203 — forecast-cycle scaling to ~100 stations

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ Proportionality — this plan MEASURES, it does not optimise

The owner's target: **~100 sites per deployment, 4×/day, comfortably.** The instinct after seeing a
7.5-minute cycle is to parallelise. **Do not.** The profile below shows the obvious target is the
wrong one, and the real one is not yet measured. This plan produces two numbers and a decision.
Reviewers: do not propose `task.map`, async rewrites, worker pools, caching or a queue here — that
belongs to whichever plan T1's numbers justify.

## What is already measured (mac mini, 2026-08-27T13:29Z, 2 stations)

A cycle is **~95 % NWP download, and that cost is FIXED per cycle** — it does not grow with
stations or models.

| phase | duration | scales with | evidence |
|---|---|---|---|
| NWP download | **441.2 s** | nothing — same grid every cycle | `nwp.fetch_completed duration_ms=441236 file_count=484 total_bytes=2874343206` |
| zarr archive | 7.7 s | nothing | `nwp.archive_completed duration_ms=7679.6 size_bytes=1748057101` |
| basin extraction | **13.4 s / 2 stations** | **stations** | `extraction.completed duration_ms=12951.1 stations_extracted=2` |
| model execution | **~40 s / 10 extra forecasts** | stations × models | 425.0 s (2 forecasts) → 465.5 s (12) |

**2.87 GB in 441 s ≈ 6.5 MB/s.** 484 files fetched against **24 079 `nwp.variable_skipped`** log
events — the walk inspects far more than it keeps (the v0 allowlist is `tp` + `t_2m` only,
Plan 063).

### The counter-intuitive consequence

**Model count is nearly free.** Going from 1 model to 6 per station cost **+9 % wall clock**. Any
plan that starts by parallelising model execution is optimising ~9 % of the cycle.

## The open question this plan answers

Two costs grow, and only one is measured at n=2:

1. **Extraction — 6.7 s/station at n=2.** If that is linear, 100 stations ≈ **670 s**, which would
   overtake the download and become the dominant cost. If most of it is per-cycle overhead
   (opening the zarr, loading the grid) with a cheap per-basin mask, 100 stations might cost far
   less. **n=2 cannot distinguish these**, and the difference decides whether any work is needed.
2. **Model execution — ~3.3 s/forecast.** 100 stations × 1 ML model ≈ 330 s. Probably linear, but
   it is an extrapolation from 10 forecasts.

### Projection, stated as a projection

Assuming both are linear — **the pessimistic case, and explicitly not yet established**:

```
441 s  download        (fixed)
  8 s  archive         (fixed)
670 s  extraction      (100 stations × 6.7 s)
330 s  models          (100 stations × 1 model × 3.3 s)
-----
≈ 1449 s ≈ 24 min per cycle → ~97 min/day at 4 cycles
```

**24 minutes inside a 6-hour cadence is a 7 % duty cycle.** On these numbers the target is already
met and **no optimisation is required** — which is exactly why measuring before optimising matters.
If extraction is sub-linear it is better still; if it is super-linear, T1 finds out cheaply.

## Tasks

**T1 — Measure the two scaling curves.** *(No production change.)*
*Scope (in):* run basin extraction against the ALREADY-ARCHIVED zarr
(`/data/nwp_grids/icon_ch2_eps/*.zarr`, no re-download) for n = 2, 10, 25, 50 synthetic basin
geometries, timing each; separately time `predict` for one model across the same n. Report both
curves and the fitted per-station marginal cost. Per `CLAUDE.md` § Ad-hoc Analyses this is a
**heredoc**, not a committed script.
*Scope (out):* changing extraction, the cycle, or any config; onboarding real stations.
*Exit:* two curves and a one-paragraph verdict in this plan: does the projection hold, and is the
100-station cycle under 30 min?

**T2 — Decide, and only then draft.** If T1 says the target is met: record that and **close this
plan** — no optimisation work. If extraction is super-linear or the total exceeds ~30 min, draft a
follow-up naming the specific phase to fix, with T1's numbers as its baseline.
*Exit:* an owner decision recorded here.

## Separately worth knowing (not in scope, do not fix here)

- **2.87 GB per cycle × 4 = ~11.5 GB/day** of NWP download. Not a wall-clock problem, but it is a
  network and disk cost that grows if the variable allowlist widens, and `nwp_grid_retention_days`
  is 3.
- **24 079 skipped variables per fetch.** If the STAC walk is filtering after download rather than
  before, the 441 s could fall sharply — but that is a hypothesis about the download path, NOT
  measured. It is the first thing to check IF T2 concludes work is needed.
- **Download parallelism is unknown.** 484 files at 6.5 MB/s may be serial. Also unmeasured.

## Context

`CLAUDE.md` already names **forecast-cycle parallelisation** as an outstanding v0b/v0c follow-on,
and `task.map` is a recorded v0b remainder. This plan deliberately does **not** assume that is the
right shape — on the measured profile, parallelising model execution would address ~9 % of the
cycle while the fixed download is 95 %.

**Deployment context:** the owner intends to onboard the full BAFU station set with a first ML
model on this mac mini. That is the n this plan is sizing for.
