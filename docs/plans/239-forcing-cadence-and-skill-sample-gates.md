---
status: DRAFT
created: 2026-09-04
plan: 239
title: Deliver the declared time step (both directions), and make skill scores state their basis
scope: Guarantee every model receives the time step it DECLARED — aggregate when data is finer, fail loudly when it is coarser (currently silent) — across hindcast and training; and make skill scores state what they actually rest on (n<30 marked, eval period = the span actually used). Two small guards against silently-wrong numbers. No new subsystem, no change to how skill is computed.
depends_on: [228]
blocks: []
source: 2026-09-04 — a scoped onboarding trial (nwp_rainfall_runoff, 2020-2026) surfaced both: forcing is never cadence-checked, and 23.5% of the run's 67,326 skill scores rest on n<30 while their eval_period claims 6.7 years
---

# Plan 239 — two places where wrong numbers arrive silently

## Status

**DRAFT.** Not for implementation until the owner confirms.

## ⛔ DO NOT OVER-ENGINEER — binding on this plan AND on every reviewer

**Owner directive, standing.** This is **two guards**, each a handful of lines plus tests. It is not a
data-quality framework, not a coverage subsystem, not a rework of skill computation.

1. **"No findings" is a complete and welcome review.** Do not manufacture findings.
2. A finding must name a **CONCRETE FAILURE** with `file:line` and the triggering state.
3. **Do not propose new apparatus** — no coverage service, no quality registry, no config system, no
   new store or table.
4. **Do not widen scope.** Backfilling the 2021–2025 observation hole, changing metrics, and
   redesigning recompute identity (Plan 235) are all § Deferred.
5. **Adding length is a cost. Prefer deleting to adding.**

## D1 — hindcast FORCING is never resampled or cadence-checked

Plan 228 (`46467336`) fixed exactly this for **observations**: `obs_df` is now put through
`resample_to_time_step` with a `validate_time_step_cadence` backstop, because a model's
`lookback_steps` count of raw ~10-minute rows was being read as that many days.

**The forcing path got neither.** In `services/hindcast.py`, `forcing_df` is built by
`_raw_forcing_to_dataframe`, None-checked, defaulted when absent, then filtered into
`past_dynamic`/`future_dynamic` — and nothing else. It is never resampled and never validated.

**Measured, not assumed (2026-09-04):** this is currently HARMLESS, because every forcing series in
the live DB is natively daily — one clock time (`00:00:00`) per source, and exactly ONE active
reanalysis binding per station (`meteoswiss_open_data_reanalysis`, 147 stations), so the retired
`camels-ch` rows are never co-mingled. The 2020-2026 trial confirmed the output end:
`hindcast_forecasts.time_step_seconds = 86400` for all 152,130 rows, with 1,134 consecutive
`hindcast_step` gaps of exactly one day.

**It stops being harmless the moment sub-daily forcing arrives.** ERA5-Land is **hourly** (the probe
returns 24 rows/day) and is the intended past-forcing source for Nepal/12300. At that point hindcast
consumes 24 rows where it believes it has one day — the identical bug class Plan 228 just fixed, with
no backstop to catch it.

**The fix is symmetry with Plan 228, not new machinery:** put `forcing_df` through the same
`resample_to_time_step` + `validate_time_step_cadence` pair, with the same
`resolved_aggregation_methods` (precipitation SUM, temperature MEAN — already correct in
`_V0_AGGREGATION_FALLBACK`). **The backstop matters more than the resample**: it must fail LOUDLY
rather than silently ship raw rows.

### D1b — the rule is "deliver the DECLARED step", and it fails in BOTH directions

**Owner correction (2026-09-04): not every model is daily.** Some declare sub-daily steps, so the
requirement is not "aggregate to daily" — it is **give each model exactly the cadence it declared**.
That has two failure directions, and only one of them is currently handled.

**Downsampling (data finer than the model) works.** Hourly forcing → daily model aggregates
correctly, precipitation summed and temperature averaged.

**Upsampling (data COARSER than the model) fails SILENTLY — verified 2026-09-04:**

    5 DAILY rows, model declares time_step = 1 hour
      -> resample_to_time_step returns 5 ROWS, unchanged, no error, no warning
      -> a 5-day span at hourly cadence should be ~120 rows

`group_by_dynamic(every="1h")` on daily input simply puts each daily row in its own hourly bucket.
The model then receives 5 rows spaced 24 h apart and treats them as 5 consecutive hours — it believes
it has 5 hours of history when it has 5 days. **This is the exact mirror of the Plan 228 bug** (which
read raw 10-minute rows as days), and it is live for any sub-daily model the moment one is assigned.

**Upsampling must fail loudly.** Coarse data cannot be invented into fine data, so there is no
correct silent behaviour — the only options are refuse, or fabricate. Refuse.

## D2 — skill scores do not say what they rest on

`skill_scores` already records `sample_size` (NOT NULL) and `eval_period_start`/`eval_period_end`, so
this is **not** a missing-column problem. Two narrower defects:

1. **No minimum-sample gate.** In the 2020-2026 trial, **15,854 of 67,326 scores (23.5%) have
   `sample_size < 30`, and the minimum is 1.** A score computed from a single forecast/observation
   pair is stored, and reads, exactly like one computed from 401.
2. **`eval_period` records the REQUESTED window, not the COVERED one.** Every row says
   `2020-01-01 → 2026-08-28`. The discharge record actually runs to 2020, is **empty for 2021–2025**
   (one row in 2025), and resumes in 2026 — so the true overlap is ~2 years, not 6.7. A consumer
   reading the eval period reasonably infers an evaluation that never happened.

Together these make a thin score indistinguishable from a solid one at a glance. That is the same
failure shape as the dead-man reporting green off stale rows: the number is not absent, it is
**confidently wrong about its own basis**.

**⚠️ Deliberately NOT proposed here:** deleting, hiding, or refusing to store low-n scores. Suppression
loses information and would collide with Plan 235's recompute-identity work. State the basis; let the
consumer decide.

## Owner decisions — ANSWERED 2026-09-04

- **Q1 — minimum-sample threshold: 30.** Scores below it are RETAINED and MARKED, never suppressed.
- **Q2 — `eval_period_*` becomes the period the score was ACTUALLY CALCULATED FROM**, not the period
  requested. ("Optimally, we'd store the period from which the scores are calculated.")
- **Q3 — YES, the training path is in scope**, on the same basis as hindcast: verify its
  `resample_to_time_step` call sites and give it the same both-directions guard.

## Phases

### T1 — deliver the declared step, in both directions (D1 + D1b)
`services/hindcast.py`: route `forcing_df` through `resample_to_time_step` +
`validate_time_step_cadence`, mirroring the `obs_df` block Plan 228 added. **Add an upsample guard in
`resample_to_time_step` itself** (it is the shared seam — hindcast, training and the operational path
all call it): when the data's detected cadence is COARSER than the requested `time_step`, raise
rather than return the coarse frame. Red-first tests both ways: hourly forcing into a daily model
aggregates correctly; daily forcing into an hourly model FAILS LOUDLY instead of silently returning
5 rows for a 5-day span.

### T1b — the same audit for training (Q3)
Verify every `resample_to_time_step` call site in `services/training_data.py` actually applies the
model's declared step. The upsample guard in T1 protects them all once it lands; T1b is confirming
there is no path that bypasses the helper entirely.

### T2 — skill basis honesty (D2, gated on Q1/Q2)
Minimum-sample marker per Q1; `eval_period_*` semantics per Q2. Tests: a 1-sample score is
distinguishable from a 400-sample one without reading the raw table; a window with a data hole
reports its covered span, not its requested span.

### T3 — docs
`docs/standards/wmo.md` or the skill section: what a score's `sample_size` and eval window mean, and
that a low-n score is retained-but-marked rather than suppressed.

```json
{
  "phases": [
    {"id": "T1", "parallel": true,  "depends_on": []},
    {"id": "T2", "parallel": true,  "depends_on": []},
    {"id": "T3", "parallel": false, "depends_on": ["T1", "T2"]}
  ]
}
```

## Exit gates

1. Hourly forcing into a daily model AGGREGATES correctly; daily forcing into an hourly model FAILS
   LOUDLY. The existing daily path is unchanged — `time_step_seconds = 86400` still holds on a re-run.
   No `resample_to_time_step` call site can silently hand a model a cadence it did not declare.
2. A low-n score is distinguishable from a well-supported one without inspecting the table.
3. A scoring window containing a data hole reports its covered span.
4. Unit suite green.

## Deferred (explicitly not this plan)

Backfilling the **2021–2025 discharge hole** (a data-acquisition question, not a code one, and the
single biggest limit on any Swiss skill number today); recompute identity and mark-and-replace
(**Plan 235** owns it); the operational forcing bridge (**Plan 134**, which explicitly scopes
hindcast out); changing any metric definition; suppressing low-n scores.
