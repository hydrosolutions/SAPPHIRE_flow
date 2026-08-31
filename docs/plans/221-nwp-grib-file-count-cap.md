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
`forecast_cycle` aborts gracefully (`forecast_cycle.nwp_fetch_failed_aborting`) and the Prefect run
reports **COMPLETED** while writing zero forecasts, so *the schedule* looks healthy. Roughly half of
cycles are affected — which cycle is fetched depends on the age-guard walk-back, and cycles differ
in file count.

**The failure is NOT silent, and an earlier draft of this plan wrongly said it was.** The abort path
emits a CRITICAL `FORECAST_FRESHNESS` record with zero forecasts
(`flows/run_forecast_cycle.py:2615`), and the watchdog already treats that as a failure and sends its
existing alert (`ops/watchdog.py:2000`). **Open question for the owner, NOT a task here:** this
outage ran ~15 h and was noticed by a human, not by an alert — so either the alert fired and was not
routed or seen, or the watchdog is not running. Worth checking when host access returns; it is
arguably a bigger issue than this constant, and it is deliberately not scoped into a one-integer
fix.

## Decisions

- **D1 — Raise the ceiling; do not remove it.** It is a runaway guard: a pagination or filter bug
  could otherwise download without bound. Removing it trades a live outage for an unbounded
  failure mode.

- **D2 — The byte budget is an ESTIMATE, not an actual-download bound. An earlier draft of this
  plan called it "the real guard"; that was wrong and the correction matters.**
  `_DEFAULT_MAX_DOWNLOAD_BYTES = 4 GB` (`:64`) is accumulated from the STAC `size` field, falling
  back to `_ASSET_SIZE_ESTIMATE_BYTES` (2 MiB) when the field is absent, and **is never
  reconciled against the bytes actually downloaded** (`:777-782`). Two consequences:
  1. **"Bytes always bind first" is FALSE when sizes are missing.** At the 2 MiB fallback, 2001
     files account for only 3.91 GiB — so a 2000-file cap would fire *before* the byte budget,
     which is the opposite of the intent.
  2. **Understated metadata can let real downloads exceed 4 GB**, because nothing checks actual
     size.

  The measured arithmetic still holds where sizes *are* published: 2,874,343,206 / 484 =
  **5.939 MB/file**, so 501 projects to **2.975 GB**, and the 4 GiB threshold falls at about
  **723 files** — not ~700. Fixing the estimator is **out of scope**; the point here is that the
  owner must choose the file cap knowing the byte guard is weaker than it looks.

- **D3 — Do NOT pick the new value from the observed maximum. This plan must not repeat Plan 213.**
  Plan 213 chose a constant from a handful of observations, and one extra day of data moved the
  maximum past it. Here the observed values are 484 and 501; **why they differ is not understood**
  (+17 files, unexplained), so the true ceiling is unknown and 501 is not evidence of a maximum.
  Set the file cap well clear of any plausible legitimate count. Proposed: **2000** — unambiguously
  a runaway guard rather than an operating limit. **Choose it knowing D2:** it is *not* backed by
  a reliable byte bound, and with missing size metadata a 2000-file run could reach ~3.9 GiB on the
  estimator while the true download size is unverified. **The value is an owner decision; the
  implementer must not substitute its own number.**

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
composition · touching the walk-back or the age guard · changing any alerting — the CRITICAL freshness record and watchdog
alert already exist (see § Impact); whether they reached anyone is an open question, not a task.
**Plan 214 does not own this**: that plan concerns runs stuck in `RUNNING`, not completed cycles.

## Exit gates

- `_MAX_FILE_COUNT` carries the owner's value and a comment stating it is a runaway guard.
- A test proves a 501-file fetch succeeds, RED against the old constant.
- The byte-budget guard is demonstrably still enforced.
