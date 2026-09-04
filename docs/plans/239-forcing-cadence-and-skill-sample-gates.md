---
status: DRAFT
created: 2026-09-04
plan: 239
title: Deliver each model its declared resolution (specify first — 3 attempts refuted), and make skill scores state their basis
scope: Aggregate observations AND forcings to each model declared resolution across hindcast, training and operational; when we cannot reach it, record "cannot run this model here" and continue. The REQUIREMENT is settled, the MECHANISM is not — three attempts were refuted by independent review, so T0 specifies the check before any further code. T2 (skill scores state their basis: n<30 marked, eval period = span actually used) is independent and buildable now.
depends_on: [228]
blocks: []
source: 2026-09-04 — a scoped onboarding trial (nwp_rainfall_runoff, 2020-2026) surfaced both: forcing is never cadence-checked, and 23.5% of the run's 67,326 skill scores rest on n<30 while their eval_period claims 6.7 years
---

# Plan 239 — two places where wrong numbers arrive silently

## Status

**DRAFT.** Was READY; returned to DRAFT 2026-09-04 after three refuted implementation attempts — T0 (specify the check) is now a prerequisite. T2 is independent and buildable.

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

## The rule (owner, 2026-09-04) — this governs everything below

> We get observation and weather-forecast data at different resolutions. Models define their own
> resolutions. **We have to aggregate our observations and forcings to the resolutions the models
> require.** If we cannot deliver a resolution — e.g. a model declares 2 min, our observations are
> 10 min and our forcings hourly — **we have to acknowledge that we cannot run that model.**

Two consequences that the rest of this plan follows from:

1. **Aggregation to the declared step is SAP3's job, for BOTH observations and forcings.** Plan 228
   did it for observations. Forcings are not exempt, and the in-code comments claiming
   `past_dynamic` "legitimately carries a finer, unresampled cadence"
   (`services/training_data.py`, `services/operational_inputs.py`) describe the DEFECT, not a design.
   They should be corrected, not honoured.
2. **"Cannot deliver the resolution" is a NORMAL, EXPECTED OUTCOME — not a crash.** When our data is
   coarser than the model declares, the correct behaviour is to record *this model cannot be run
   here, and why*, and carry on with the others. Not an exception that aborts a multi-station run,
   and emphatically not a silent pass-through.

## D1 — deliver each model its declared resolution (NOT YET SPECIFIED — 3 failed attempts)

**Status: the REQUIREMENT is settled (see the rule above); the MECHANISM is not.** Three
implementation attempts were each refuted by independent review, every one passing my own tests
first. Do not attempt a fourth without specifying the check.

### What is actually wrong today

`services/hindcast.py` builds `forcing_df`, None-checks it, defaults it, splits it into
past/future — and never brings it to the model's declared step. `services/training_data.py` copies
raw forcing into `past_dynamic`. `services/operational_inputs.py` does the same. All three then file
the result under the declared `time_step` (the FI keys `dynamic` as
`dict[timedelta, SpatialInputSpec]`), asserting a resolution the data may not have.

**Two in-code comments assert this is legitimate** — `services/training_data.py` and
`services/operational_inputs.py` both say `past_dynamic` "legitimately carries a finer, unresampled
cadence". **Per the owner rule those comments describe the DEFECT and must be corrected**, not
honoured. (I briefly accepted one and reported the premise as wrong. It was not.)

### The three mechanisms that FAILED, and why — do not retry these

| attempt | idea | refuted by |
|---|---|---|
| 1 | reject when the **median** input gap is coarser than the step | false-POSITIVE: gaps 36h/12h/36h give a 36h median, yet these bucket into four clean daily rows |
| 2 | reject when the **minimum** input gap is coarser | false-NEGATIVE: one 1h pair inside otherwise-daily data makes the minimum 1h, admitting it for an hourly model |
| 3 | aggregate, then run `validate_time_step_cadence` on the result | it checks only gaps **between rows present** and returns early below 2 rows — so an hourly model with daily data, scoped to a 2h window, leaves ONE row, passes, and the daily frame is still delivered. A missing FIRST or LAST bucket is invisible for the same reason |

**The lesson: 1 and 2 tried to PREDICT deliverability from input statistics; 3 checked the wrong
property of the output.** All three passed local testing and were caught only by independent review.

### What the check must actually be

1. **Compare against the EXPECTED BUCKET SET.** The question is "does every slot the model will read
   exist across the consumed window?" — not any gap statistic, and not the existing validator, which
   answers a different question and is correctly scoped to `past_targets`.
2. **The window is PER FEATURE CLASS, not one window.** `NwpRainfallRunoff` is future-forcing-only
   (`_n_lags = 0`); its lookback is TARGET history. Validating its forcing from `lookback_start`
   inspects rows it never reads, so a missing unconsumed row at `issue_time` falsely skips a step
   whose future forcing is complete. Past-forcing, future-forcing and targets each have their own
   consumed window, and models declare them separately.
3. **It must cover hindcast, training AND operational** — all three bypass today.
4. **⚠️ The operational path needs particular care:** `_aggregate_nwp_records_to_time_step` is called
   both for model input AND by `reduced_daily_step_times`, which feeds
   `track_resolution.resolve_candidate`'s completeness check. That check is DESIGNED to notice
   missing coverage and walk back; raising there escapes `resolve_candidate`, sits outside
   `run_forecast_cycle`'s fatal set, and aborts the whole cycle. Guard the model-input caller only.

### Confirmed GOOD (keep from the abandoned branch)

Containment is right and reviewed: a resolution failure skips that hindcast step, the station runner
records a failed step and continues, and group hindcast drops only that station-step. The
`declared_lookback_steps=None` fallback is sensible; both production callers supply the declaration.
`deliver_at_time_step` should be **deleted** — it ended with no production caller.

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


### T0 — SPECIFY the resolution check (BLOCKS T1; do not skip)

Write the check down before writing code: the expected-bucket-set comparison, the per-feature-class
consumed windows (past-forcing / future-forcing / targets), and the behaviour when a model declares a
resolution we cannot serve. Three implementations were refuted without this; the fourth should not
start until D1 § "What the check must actually be" is a specification rather than a direction.
**Exit: a reviewer can predict the outcome for every row of D1's failure table from the spec alone.**

### T1 — apply it to hindcast, training and operational (gated on T0)

All three bypass today. Correct the two in-code comments calling finer unresampled `past_dynamic`
legitimate. Guard the operational MODEL-INPUT caller only — never `reduced_daily_step_times`, whose
completeness check must keep degrading. Red-first tests must include **every row of D1's failure
table**, so no rejected mechanism can be reintroduced.

### T2 — skill basis honesty (D2) — INDEPENDENT of T0/T1, buildable now

Mark scores with `sample_size < 30` (retained, never suppressed) and make `eval_period_*` the span
the score was actually calculated from. Shares no mechanism with the resolution work and is not
blocked by it.

### T3 — docs

What a score's `sample_size` and eval window mean, and that a low-n score is retained-but-marked.
Add the resolution rule itself once T1 lands.

```json
{
  "phases": [
    {"id": "T0", "parallel": false, "depends_on": []},
    {"id": "T1", "parallel": false, "depends_on": ["T0"]},
    {"id": "T2", "parallel": true,  "depends_on": []},
    {"id": "T3", "parallel": false, "depends_on": ["T1", "T2"]}
  ]
}
```

## Exit gates

1. **T0**: the resolution check is specified precisely enough that a reviewer can predict its verdict
   for every row of D1's failure table without reading code.
2. **T1**: hindcast, training and operational all deliver the declared resolution or record "cannot
   run this model here" with a reason; the run continues either way. The operational completeness
   path still degrades and walks back — it must NOT raise. Tests cover every refuted mechanism.
3. **T2**: a low-n score is distinguishable from a well-supported one without inspecting the table,
   and a scoring window containing a data hole reports its covered span.
4. Unit suite green; no in-code comment still calls finer unresampled `past_dynamic` legitimate.

## Implementation history — read before attempting T1

Branch `feat/plan-239-declared-time-step` (unpushed, superseded) carried three refuted attempts and
**three independent review rounds, each finding real defects my own tests had passed**. It is kept
only as the record in D1's failure table. Do not resume it; T0 first.

## Deferred (explicitly not this plan)

Backfilling the **2021–2025 discharge hole** (a data-acquisition question, not a code one, and the
single biggest limit on any Swiss skill number today); recompute identity and mark-and-replace
(**Plan 235** owns it); the operational forcing bridge (**Plan 134**, which explicitly scopes
hindcast out); changing any metric definition; suppressing low-n scores.
