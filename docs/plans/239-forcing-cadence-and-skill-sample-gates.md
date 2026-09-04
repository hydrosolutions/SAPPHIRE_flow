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
   **Confirmed by independent plan review (2026-09-04): this is the right property, and NO simpler
   complete check already exists.** `_validate_continuous_window`
   (`models/nwp_regression.py:621`) already demonstrates the approach — but only inside one model,
   using that feature's own lookback. `assess_future_coverage` (`services/nwp_coverage.py:128`)
   counts rows without checking their expected timestamps, and `_station_complete`
   (`services/track_resolution.py:143`) checks members share a timestamp set without comparing it to
   the issue-anchored expected set.
2. **The window is PER DECLARED INPUT SERIES — "per feature class" is TOO COARSE.**
   `SeasonalPrecipRunoffRegression` declares 7 target steps, **45** precipitation steps and **14**
   temperature steps (`models/nwp_regression.py:752,765`), all in the same past-forcing class, and
   the adapter collapses them into a single maximum `lookback_steps` of 45
   (`adapters/forecast_interface.py:659,707`). A class-wide 45-day expected set would reject a model
   whose temperature is complete over the 14 days it actually reads.
   Separately, `NwpRainfallRunoff` is future-forcing-only (`_n_lags = 0`), so its lookback is TARGET
   history: validating its forcing from `lookback_start` inspects rows it never reads.
   **Training adds one more distinction:** future forcing there is aligned only to existing target
   timestamps, not to every bucket in the requested range (`models/nwp_regression.py:257`).
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

Write the check down before writing code: the expected-bucket-set comparison, the **per declared
input series** consumed windows, and the behaviour when a model declares a resolution we cannot
serve. Three implementations were refuted without this; the fourth must not start until D1 § "What
the check must actually be" is a specification rather than a direction.

**Exit — a FINITE VERDICT TABLE a reviewer can check without reading code.** It must state the
expected outcome for, at minimum:
  * every row of D1's failure table (median / minimum / aggregate-then-validate);
  * **`NwpRainfallRunoff`** — future-forcing-only, so a missing *unconsumed* past-forcing row at
    `issue_time` must NOT skip a step whose future forcing is complete;
  * **`SeasonalPrecipRunoffRegression`** — unequal past lookbacks in one class (7 target / 45 precip
    / 14 temperature): temperature complete over its own 14 days must PASS even though the
    collapsed class maximum is 45.
Predicting only the failure table is NOT sufficient — a spec using one expected set per feature class
predicts all three of those rows correctly and still fails the Seasonal case.

Follow `docs/workflow.md`'s task shape for a non-trivial task: outcome, bounded in/out scope, exact
verification, and pre-change evidence.

## T0 DELIVERABLE — the resolution check, specified (2026-09-04)

### Outcome

One predicate: **can we serve this model, at this issue time, from the data we hold?** Answered per
DECLARED INPUT SERIES, not per frame and not per feature class.

### The check

For each declared input series — name `V`, temporality, lookback/horizon `L`, and the model's
declared `time_step` `S`, at issue time `T`:

1. **Anchor.** `T0 := floor_to_time_step(T, S)` (`services/training_data.py`, already exists and is
   UTC-calendar aligned).
2. **Expected bucket set.**
   * `past_known` (targets AND past forcing): `{ T0 - k*S : k = 0 .. L-1 }`
   * `future_known`: `{ T0 + k*S : k = 1 .. H }` where `H` is the model's `forecast_horizon_steps`
3. **Delivered set.** The timestamps present for **column `V`** in the aggregated frame for that
   temporality.
4. **Verdict.** `SERVE` iff `expected ⊆ delivered`. Otherwise `CANNOT SERVE`, naming `V` and the
   missing buckets.

### Three things this deliberately does NOT do

* **No gap statistics.** Not the median, not the minimum, not any summary of spacing. All three
  refuted attempts were spacing measures; membership in the expected set is the property.
* **No NaN judgement.** Bucket EXISTENCE only. `max_nan` is already gated per variable per frame by
  `_variables_over_nan_tolerance` (`adapters/forecast_interface.py`) and stays there.
* **No new component.** `floor_to_time_step` exists; the expected-set idea already exists inside one
  model (`models/nwp_regression.py:621`). This makes it uniform, it does not invent it.

### Why per-SERIES and not per-frame

`SeasonalPrecipRunoffRegression` declares **7** target steps, **45** precipitation steps and **14**
temperature steps (`models/nwp_regression.py:92,103,106`), and the adapter collapses them to a single
`lookback_steps = max(...) = 45` (`adapters/forecast_interface.py:659`). A frame-wide 45-bucket
expectation rejects a model whose temperature is complete over the 14 buckets it actually reads.

### VERDICT TABLE — checkable without reading code

`S` = 1 day and `T0` = 2026-01-10 unless stated. "wrongly X" marks what a refuted mechanism did.

| # | model / series | data held | expected set | verdict | refuted mechanism |
|---|---|---|---|---|---|
| 1 | any, daily, L=4 | stamps 01-07 00:00, 01-08 12:00, 01-09 00:00, 01-10 12:00 | 01-07..01-10 | **SERVE** — all four buckets occupied | median gap 36h wrongly REJECTED |
| 2 | any, **hourly**, L=4 | daily rows 01-07..01-10 plus one extra at 01-08 01:00 | 4 hourly buckets ending T0 | **CANNOT SERVE** | minimum gap 1h wrongly SERVED |
| 3 | any, **hourly**, L=2 | daily rows through T0 | 2 hourly buckets ending T0 | **CANNOT SERVE** | aggregate-then-validate wrongly SERVED (one row left, validator returns early) |
| 4 | any, daily, L=5 | 01-06..01-10 present except **01-06** (the FIRST expected bucket) | 01-06..01-10 | **CANNOT SERVE** | aggregate-then-validate wrongly SERVED (edge bucket invisible) |
| 5 | any, daily, L=5 | 01-06..01-10 present except 01-08 (interior) | 01-06..01-10 | **CANNOT SERVE** | — (all three caught this) |
| 6 | **`NwpRainfallRunoff`** past forcing | past forcing row at `T0` MISSING; future forcing complete to `H` | past forcing: **∅** (declares none — `_n_lags = 0`; its only past_known is the TARGET, lookback 1) | **SERVE** | validating forcing from `lookback_start` would wrongly REJECT |
| 7 | **`SeasonalPrecipRunoffRegression`** temperature | temperature present for the 14 buckets ending `T0`, ABSENT for days 15–45 | 14 buckets ending `T0` | **SERVE** | a class-wide 45-bucket set would wrongly REJECT |
| 8 | **`SeasonalPrecipRunoffRegression`** precipitation | precipitation missing on one day inside its 45 | 45 buckets ending `T0` | **CANNOT SERVE** — names precipitation | — |
| 9 | any, daily, L=1 | hourly data, complete for that day | 1 daily bucket | **SERVE** — aggregated (precip SUM, temp MEAN) | — |
| 10 | any, daily, L=3 | no data at all for `V` | 3 buckets | **CANNOT SERVE** | — |

Rows 1–5 are the refuted mechanisms; **6 and 7 are the two a per-feature-class spec would still get
wrong**, which is why they are mandatory.

### Verification

`uv run pytest tests/unit/services/ -k resolution` — one red-first test per row, each asserting the
verdict AND, on `CANNOT SERVE`, the series named and buckets reported. A test must fail against a
per-feature-class implementation (rows 6, 7) and against each refuted mechanism (rows 1–4).

### Pre-change evidence

`hindcast_forecasts.time_step_seconds = 86400` for all 715,103 rows currently stored, with 1,134
consecutive one-day gaps — today's daily path must still produce exactly that after T1.

### T1 — apply it to hindcast, training and operational (gated on T0)

All three bypass today. Correct the two in-code comments calling finer unresampled `past_dynamic`
legitimate. Red-first tests must include **every row of D1's failure table plus T0's two model
cases**, so no rejected mechanism can be reintroduced.

**Operational containment — the placement in the previous draft was WRONG.** Guarding
`build_future_dynamic_frame` is not enough: it is also used by the legacy station-superset assembler
(`services/operational_inputs.py:727`), and an exception there is caught at STATION level
(`flows/run_forecast_cycle.py:3058,3082`), skipping **every** assigned model for that station,
fallbacks included. The containment point that lets one model fail while its siblings continue is the
**assignment-local coverage gate** (`services/run_station_forecast.py:249`) — its current count-only
check is what needs the expected-set condition. **No new component is required.**
`reduced_daily_step_times` must still never raise; its completeness check exists to degrade and walk
back.

**Also name the per-track path:** `services/track_assembly.py:345` independently delivers raw
`past_dynamic` without resampling. Correcting only `operational_inputs.py` leaves that production
path unchanged.

### T2 — skill basis honesty (D2) — mechanically INDEPENDENT of T0/T1

Mark scores with `sample_size < 30` (retained, never suppressed), and make `eval_period_*` **the
first and last MATCHED valid times per stratum** — today every stratum receives the global
minimum/maximum hindcast steps (`services/skill/service.py:656`) and the stratum builder discards the
matched timestamps entirely (`:316`).

**⚠️ Two endpoints CANNOT express an internal hole, and D2's wording overclaimed that they could.**
With matched samples in 2020 and 2026 the min and max still describe a 6.7-year span. Narrowing the
endpoints to first/last matched is worth doing on its own, but **`sample_size` is what reveals a thin
score** — representing discontinuous coverage would need apparatus this plan will not add.

**The low-n premise is narrower than D2 implied:** the promotion gate already excludes scores below a
configurable `min_skill_samples` (`services/model_onboarding.py:900`), and the dashboard already
shows `sample_size` as "N" (`api/templates/models/detail.html:86`). **What is missing is specifically
an explicit `<30` marker on the stored row** — not sample-size data, and not a minimum-sample gate in
general.

**On sequencing:** T2 shares no mechanism with T0/T1 and is not blocked by them. It is still a task
in a DRAFT plan, so it needs the owner's READY like anything else (`docs/workflow.md`), and T3 below
is split so T2's docs do not wait on T1.

### T3a — docs for T2 (with T2, not after T1)

What a score's `sample_size` and eval window mean, and that a low-n score is retained-but-marked.
Split from T3b so T2's required doc change ships with T2 — every code change updates affected docs.

### T3b — docs for T1

The resolution rule and the per-declared-series windows, once T1 lands.

```json
{
  "phases": [
    {"id": "T0", "parallel": false, "depends_on": []},
    {"id": "T1", "parallel": false, "depends_on": ["T0"]},
    {"id": "T2", "parallel": true,  "depends_on": []},
    {"id": "T3a", "parallel": false, "depends_on": ["T2"]},
    {"id": "T3b", "parallel": false, "depends_on": ["T1"]}
  ]
}
```

## Exit gates

1. **T0**: the resolution check is specified precisely enough that a reviewer can predict its verdict
   for every row of D1's failure table without reading code.
2. **T1**: hindcast, training and operational all deliver the declared resolution or record "cannot
   run this model here" with a reason; the run continues either way. The operational completeness
   path still degrades and walks back — it must NOT raise. Tests cover every refuted mechanism.
3. **T2**: a stored score carries an explicit `<30` marker, and `eval_period_*` holds the first and
   last MATCHED valid times for that stratum. NOT a gate: two endpoints cannot express an internal
   hole — `sample_size` is what reveals a thin score.
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
