---
status: DRAFT
created: 2026-08-31
plan: 221
title: The 500-file NWP cap stops roughly half of all forecast cycles
scope: One constant in adapters/meteoswiss_nwp.py, one red-first regression test, and the comment that records why. No change to the byte budget, the fetch logic, or any flow.
depends_on: []
blocks: []
source: 2026-08-31 live outage — forecasts stopped after 2026-08-30 18:00; nwp.fetch_failed "GRIB file count exceeded: 501 > 500"
---

# Plan 221 — a one-file overshoot is stopping forecasts

## Status

**DRAFT.** Not for implementation until the owner confirms. **This is a live outage**, not a
latent risk — see § Impact.

## ⛔ Proportionality is a binding constraint on this plan AND on its review

This changes **one integer**. It does not touch the byte budget, the fetch loop, the walk-back, or
any flow.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.** Do not manufacture findings.
2. **A finding must name a CONCRETE FAILURE** with `file:line`.
3. **Do not propose new apparatus** — no metric, no monitor, no config plumbing, no new alert.
4. **Do not propose investigating MeteoSwiss's publication composition.** § D3 bounds that
   deliberately.
5. **Adding length is a cost.**

## The defect — measured on the mini, not inferred

`_MAX_FILE_COUNT: int = 500` (`adapters/meteoswiss_nwp.py:66`) is a module constant, always
enforced. Some ICON-CH2-EPS cycles yield **501** allowlisted GRIB files. From the worker log:

```
2026-08-30 00:05  FAIL  nwp.fetch_failed  'GRIB file count exceeded: 501 > 500'
2026-08-30 06:06  OK    nwp.fetch_completed  file_count=484  total_bytes=2874343206
2026-08-30 18:06  OK    nwp.fetch_completed  file_count=484  total_bytes=2874343206
2026-08-31 00:05  FAIL  'GRIB file count exceeded: 501 > 500'
2026-08-31 06:06  FAIL  'GRIB file count exceeded: 501 > 500'
```

**Do not confuse this with the other cap.** `max_files` is a *scope limiter* passed to the adapter
(`meteoswiss_nwp.py:360`), commented out in `config.toml:413` (`# max_files = 42`) and therefore
`None` in production. The constant above is the one that fires.

## Impact

**Forecasts stopped after 2026-08-30 18:00** and two consecutive cycles have failed since. The
failure is **invisible from the outside**: `forecast_cycle` aborts gracefully
(`forecast_cycle.nwp_fetch_failed_aborting`) and the Prefect run reports **COMPLETED**, so the
schedule looks healthy while zero forecasts are written. Roughly half of cycles are affected —
which cycle is fetched depends on the age-guard walk-back, and cycles differ in file count.

## Decisions

- **D1 — Raise the ceiling; do not remove it.** It is a runaway guard: a pagination or filter bug
  could otherwise download without bound. Removing it trades a live outage for an unbounded
  failure mode.

- **D2 — The byte budget is the real guard, and it has ample headroom.**
  `_DEFAULT_MAX_DOWNLOAD_BYTES = 4 GB` (`:64`). Measured: 484 files = **2.87 GB**, i.e. ~5.9 MB per
  file, so 501 files is **~2.98 GB — still ~1 GB under the byte budget.** Cost is already bounded
  by bytes; the file count is a secondary heuristic misfiring on a boundary it was never tuned to.

- **D3 — Do NOT pick the new value from the observed maximum. This plan must not repeat Plan 213.**
  Plan 213 chose a constant from a handful of observations, and one extra day of data moved the
  maximum past it. Here the observed values are 484 and 501; **why they differ is not understood**
  (+17 files, unexplained), so the true ceiling is unknown and 501 is not evidence of a maximum.
  Set the file cap high enough that **the byte budget always binds first** — with ~5.9 MB/file and
  a 4 GB budget, anything at or above ~700 files means bytes bind before count. Proposed: **2000**,
  which is unambiguously a runaway guard rather than an operating limit. **The value is an owner
  decision; the implementer must not substitute its own number.**

- **D4 — The comment must say what the constant is FOR.** Today it is a bare
  `_MAX_FILE_COUNT: int = 500` with no rationale, which is exactly why nobody noticed it was an
  operating limit rather than a safety limit. Record: runaway guard only; the byte budget is the
  cost bound; observed real counts 484-501 as of 2026-08-31.

## Task

**T1 — raise the cap, lock the behaviour, record the rationale.**
*In:* `_MAX_FILE_COUNT` in `adapters/meteoswiss_nwp.py:66` and its comment (D4); one regression
test asserting a fetch of **501** files succeeds where it previously raised `BudgetExceededError`.
*Out:* the byte budget · `max_files` · the fetch loop · the age guard · any flow · any config
plumbing.
*Exit:* the new test is **proven RED against 500** and green after; `uv run pytest`, `ruff` and the
pyright ratchet pass. **The existing byte-budget test must still pass** — if raising the file cap
lets a fetch through that the byte budget should have stopped, that is a defect in this change.

## Deployment

**Needed to end the outage** — the fix is inert until the mini runs it. The mini is currently on
0.1.833 and mid-onboarding (batch 2); **do not redeploy while that run is in flight** — recreating
containers killed a run on 2026-08-29. Deploy after batch 2 completes, then confirm the next
scheduled cycle writes forecasts.

## Non-goals

Changing the byte budget · making either cap config-driven · explaining MeteoSwiss's 484-vs-501
composition · touching the walk-back or the age guard · alerting on silent-abort cycles (real, and
squarely Plan 214's § detection — not this plan).

## Exit gates

- `_MAX_FILE_COUNT` carries the owner's value and a comment stating it is a runaway guard.
- A test proves a 501-file fetch succeeds, RED against the old constant.
- The byte-budget guard is demonstrably still enforced.
