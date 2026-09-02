---
status: DRAFT
created: 2026-09-02
plan: 230
title: Stop hindcasting years that cannot produce a forecast
scope: Bound the hindcast issue-time window to where observations and forcing actually overlap. No change to what a hindcast computes, only to which dates are attempted.
depends_on: [228]
blocks: []
source: 2026-09-02 — the 148-station onboarding ran for four days stepping through 1980-2030, logging ~12,900 no-observation skips per 20 minutes
---

# Plan 230 — four days of walking through dates that cannot work

## ⛔ Proportionality is a binding constraint on this plan AND on its review

This narrows a date range. It changes **no** hindcast arithmetic, no model, no storage.

**Rules binding every reviewer:**

1. **"No findings" is a complete and welcome review.** Do not manufacture findings.
2. **A finding must name a CONCRETE FAILURE** with `file:line` and a consequence.
3. **Do not propose new apparatus** — no scheduler, no parallel executor, no caching layer, no
   progress service. Parallelising stations is a **separate** known gap and is out of scope here.
4. **Do not propose changing what a hindcast computes** — that is Plan 228, already READY and in
   implementation (see § Sequencing above). A separate defect in daily aggregation is
   being fixed elsewhere; this plan must not touch it or it becomes impossible to tell which change
   fixed what.
5. **Adding length is a cost.** A previous plan in this repo was grown 148 -> 441 lines by a review
   round and had to be reset twice; do not repeat that.

## The waste — measured on the mini, 2026-09-01

`_issue_times()` (`services/hindcast.py:101-111`) walks from `period_start` to `period_end` one
`time_step` at a time, unconditionally. The period is the model's **training period**, passed
straight through (`services/onboarding.py:115-116`).

In production that period is **1980-01-01 to 2030-01-01** on all **619** artifacts — measured, not
assumed. Today is 2026-09-02, so the window includes:

- **~3.3 years in the future**, which can never have observations; and
- **1980**, a year before any forcing data exists (`historical_forcing` starts 1981-01-01).

Observed effect: **~12,900 `hindcast.skip.no_observations` in 20 minutes**, at ~10.7 per second,
each one a date the code could have known was hopeless before starting. The 148-station onboarding
ran **four days** and was still going when it was cancelled for an unrelated reason.

**This is NOT purely wasted time — an earlier draft of this plan said so and was wrong.** The
no-observations guard does not catch every impossible date. Observations only need to be non-empty
*somewhere* inside the runner's 720-step lookback (`services/hindcast.py:170`, `:298`); a
forcing-free model then consumes the last available values and emits a forecast **dated at the
future issue time** (`models/linear_regression_daily.py:133,164`).

**Measured in the quarantined output of the aborted run: 2,704 stored hindcast records carry a
future `hindcast_step`, the latest dated 2028-07-09.** Small against 8,983,489 rows, but they are
forecasts for dates that have not happened, and they feed skill scores.

**So this plan is a correctness fix as well as a performance one.** It changes no arithmetic — it
stops the system attempting, and occasionally storing, dates that cannot be hindcast. The waste also
scales with fleet size, so Nepal will feel it more than Switzerland.

## Decisions

- **D1 — Bound the window by DATA, not by a constant — and forcing bounds apply ONLY to models that
  require forcing.** The useful range is where the data a given model actually needs exists.
  Replacing 1980-2030 with a different hard-coded pair would just be a better guess, and the next
  fleet would need another one.

  **The forcing-free case is not hypothetical and must not be suppressed.** The runner deliberately
  fetches no forcing when `required_features` is empty (`services/hindcast.py:335`), and
  `LinearRegressionDaily` requires none (`models/linear_regression_daily.py:54`) — as do persistence
  and climatology. A naive "observations AND forcing overlap" rule would compute an empty window for
  every one of them and silently stop hindcasting three working models. **For a forcing-free model
  the window is bounded by observations alone.**

- **D2 — Never silently widen. Narrowing only.** If the derived range is empty or unexpectedly
  short, the hindcast must say so and skip loudly, not fall back to the old wide window. A silent
  fallback would hide the very misconfiguration this plan makes visible.

- **D3 — Do not touch the training period itself.** `training_period_start/end` on the artifact is a
  record of how the model was trained and is used elsewhere. This plan changes what the **hindcast**
  iterates, not what the artifact claims. Conflating them would alter model provenance.

## Task

**T1 — derive the hindcast window from available data.**
*In:* the window computation feeding `_issue_times`, plus one log line recording the derived range
and how much was trimmed, so the saving is visible rather than assumed.
*Out:* `_issue_times` itself (its walk is correct) · hindcast arithmetic · the training period (D3) ·
parallelising stations · the daily-aggregation defect.
*Exit:* a station whose observations start in 2000 attempts no issue time before 2000; no issue time
in the future is attempted; a station with no overlap is skipped with a clear reason; red-first
tests for each; `uv run pytest`, ruff, and the pyright ratchet pass.

## Non-goals

Parallel execution · changing hindcast computation · the daily-mean-runoff defect · the training
period · re-running the fleet (that is scheduled work, not this plan).

## Exit gates

- No issue time is attempted before data exists or after today.
- The derived window and the amount trimmed are logged once per station-model.
- An empty or too-short window fails loudly (D2), never silently reverts to the old range.
