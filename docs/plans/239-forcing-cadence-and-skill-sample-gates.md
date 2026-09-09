---
status: PARTIAL
created: 2026-09-04
plan: 239
title: Deliver each model its declared resolution (specify first — 3 attempts refuted), and make skill scores state their basis
scope: Aggregate observations AND forcings to each model declared resolution across hindcast, training and operational; when we cannot reach it, record "cannot run this model here" and continue. The REQUIREMENT is settled, the MECHANISM is not — three attempts were refuted by independent review, so T0 specifies the check before any further code. T2 (skill scores state their basis: n<30 marked, eval period = span actually used) is independent and buildable now.
depends_on: [228]
blocks: []
source: 2026-09-04 — a scoped onboarding trial (nwp_rainfall_runoff, 2020-2026) surfaced both: forcing is never cadence-checked, and 23.5% of the run's 67,326 skill scores rest on n<30 while their eval_period claims 6.7 years
---

# Plan 239 — two places where wrong numbers arrive silently

## ⛔ HALTED — this plan is inside the timezone/time-alignment topic (owner decision, 2026-09-08)

The owner halted all timezone / time-alignment work pending consolidation by the time-grid track, and
named Plans 226 and 234. **This plan was not named, and it should have been — the owner extended the
halt to it on 2026-09-08 once that was pointed out.** `T1b, T2, T3a and T3b do not proceed.`

**Why it is inside the topic, by the halt's own boundary test.** The halt told Plan 234 that "if work
there starts touching bucket boundaries or valid_time phase rather than aggregation method, it has
crossed into the halted topic." T1b is a bucket-boundary gate by construction — its expected-set
table (§ *The check*) is defined as `{T0 - k*S}` and `{T0 + k*S}` around
`T0 := floor_to_time_step(T, S)`, and `floor_to_time_step` is one of the two helpers Plan 254 T2
exists to make phase-aware. Building that gate now means specifying completeness against a phase-zero
grid that Plan 252 proposes to replace, and then rebuilding it.

**What is NOT unwound.** T0 and T1a are merged and DEPLOYED (see below). Halting new work does not
unwind shipped work, and this plan's remaining tasks are held, not reverted. The reasons not to
revert T1a are in the next section.

## Status

**HALTED — T0 and T1a DONE, MERGED (#263) and DEPLOYED (0.1.889). T1b, T2, T3a, T3b are HELD.**

T1a shipped separately from T1b by an owner decision: T1a changes what models are FED (forecast
values move), T1b changes whether they RUN AT ALL. Bundled, a surprise after deployment would have
had two possible causes and no way to separate them.

⚠️ **DEPLOYED 2026-09-08 06:52 UTC — this line is superseded.** It previously read "NOT yet deployed
to the mac mini … reaches staging only on the next deploy." `57aac024` (T1a) **is** version
`0.1.889`, which is the build the mini has been running since 06:52 UTC. Its behaviour change is
live on staging now.

### 🔴 T1a is live against artifacts that predate it — a train/serve skew, measured not assumed

**Measured on staging 2026-09-08.** Every model artifact was trained on or before **2026-09-04**
(newest: `nwp_rainfall_runoff`, 2026-09-04 09:51 UTC; `linear_regression_daily` 2026-08-31;
`climatology_fallback` 2026-08-31). T1a changed how `past_dynamic` is prepared in **both** the
training and the operational paths, and this plan's own status says T1a "changes what models are
FED (forecast values move)". Nothing has been retrained since it went live. So every active artifact
was trained on the old preparation and is now served the new one.

**What bounds the damage on THIS deployment — measured, and a snapshot, not a property:**

- Swiss `historical_forcing` is already **daily** — the only non-zero inter-sample gap in the last
  10 days is `86400 s`. The daily models declare a daily step, so the resample has at most one
  sample per bucket here; it is not a 24→1 aggregation. On a deployment with sub-daily forcing it
  would be.
- Output **labelling did not change**. Measured per cycle across the deploy boundary, each cycle
  produces exactly ONE valid_time phase, both before and after, tracking the cycle start:
  09-07 00:00Z→`8 s`, 06:00Z→`21602 s`, 12:00Z→`43202 s`, 18:00Z→`64802 s`, 09-08 00:00Z→`3 s`,
  06:00Z→`21605 s`, 07:24Z→`26658 s`. `nwp_rainfall_runoff` sits at phase `0` throughout. So Plan
  226's wall-clock anchoring defect is untouched by T1a — neither fixed nor worsened.
- 🪤 **The magnitude of any value shift is NOT yet measurable.** Only one post-deploy cycle exists
  (07:24Z, itself off the 00/06/12/18 schedule). Do not claim T1a is harmless from the absence of a
  visible jump in one cycle; that is a well-formed answer to the wrong question.

**⛔ Do NOT revert T1a, and do NOT retrain yet.** Reverting restores a known defect (models fed at
the wrong resolution) to remove a skew that is bounded-small on this deployment and on a test system.
Retraining *now* would be the genuine double retrain: Plan 254 T6 moves Switzerland's daily boundary
to 23:00Z and requires a retrain anyway, so the correct sequence is **settle the boundary, then
retrain once, onto the final grid.** Until then staging runs with a known, recorded skew.

📌 **The double-retrain risk was real — it was attached to the wrong plan.** It was raised against
Plan 226 and refuted there correctly (226 changes labelling, not training inputs, and contains the
words "retrain" and "artifact" zero times). It belongs *here*: T1a is what changed training inputs,
and it is already live.

Three independent Codex rounds on T1a — 4 findings, then 2, then 1, every one real. Two were
defects introduced by the fix for the previous round; the details are in § T1a and the commit
messages, and are worth reading before starting T1b, which touches the same boundaries.

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

**One membership test; three ways to build the set it tests against.** The predicate is always the
same — *is every slot this input will read present?* — but WHERE that set comes from differs by
caller, and that is what the previous version got wrong by assuming a single anchor+count shape.

Given the model's declared `time_step` `S`, and `T0 := floor_to_time_step(T, S)`:

| caller | expected set | why |
|---|---|---|
| **past** (targets and past forcing), lookback `L` | `{T0 - k*S : k = 1..L}` | Buckets are LEFT-LABELLED, so `T0` is the bucket currently IN PROGRESS. Existing complete-lookback bounds deliberately cover `[T0-L*S, T0)`. **Including `T0` demands an incomplete bucket and falsely refuses.** |
| **future**, horizon `H` | `T == T0` → `{T0 + k*S : k = 0..H-1}`; otherwise `{T0 + k*S : k = 1..H}` | When the issue instant sits exactly on a boundary the whole `T0` bucket is future data and existing aggregation RETAINS it. Starting unconditionally at `T0+S` then demands one bucket too far. |
| **training** | the timestamps ACTUALLY CONSUMED — the target timestamp set the forcing is joined onto | Training has **no issue time**; it joins forcing onto whatever target timestamps exist. No anchor-and-horizon describes that: targets at Jan 1 and Jan 3 consume exactly those two, while anchoring at Jan 1 would wrongly demand Jan 2. |

So the core takes an **explicit expected set**; `past`/`future` are two small helpers that compute one,
and training passes its consumed timestamps directly. That is the smallest shape covering all three,
and it removes the "one predicate" claim that was false.

### Delivered set, and what this does NOT judge

The delivered set is **the timestamps present for that column** — membership only.

**Nulls count as PRESENT.** Whether a slot's value is usable is `max_nan`'s job, already gated per
variable per frame by the FI adapter, which counts nulls and NaNs alike. An earlier version excluded
null-valued rows here, which both contradicted this rule and made null and NaN behave differently.

### Verdict

`SERVE` iff `expected ⊆ delivered`. Otherwise `CANNOT SERVE`, naming the input and the missing slots.

### VERDICT TABLE — checkable without reading code

`S` = 1 day and `T0` = 2026-01-10 unless stated. **Every PAST row's expected set ends at 01-09, not 01-10** — `T0` is the bucket in progress and is excluded by the rule above. Round-2 review (major): this table was published a bucket LATE, contradicting both the rule and the tests that implement it (the tests were right). Corrected 2026-09-07. "wrongly X" marks what a refuted mechanism did.

| # | model / series | data held | expected set | verdict | refuted mechanism |
|---|---|---|---|---|---|
| 1 | any, daily, L=4 | stamps 01-06 00:00, 01-07 12:00, 01-08 00:00, 01-09 12:00 | 01-06..01-09 | **SERVE** — all four buckets occupied | median gap 36h wrongly REJECTED |
| 2 | any, **hourly**, L=4 | daily rows 01-06..01-09 plus one extra at 01-08 01:00 | the 4 hourly buckets BEFORE T0 | **CANNOT SERVE** | minimum gap 1h wrongly SERVED |
| 3 | any, **hourly**, L=2 | daily rows up to T0 | the 2 hourly buckets BEFORE T0 | **CANNOT SERVE** | aggregate-then-validate wrongly SERVED (one row left, validator returns early) |
| 4 | any, daily, L=5 | 01-05..01-09 present except **01-05** (the FIRST expected bucket) | 01-05..01-09 | **CANNOT SERVE** | aggregate-then-validate wrongly SERVED (edge bucket invisible) |
| 5 | any, daily, L=5 | 01-05..01-09 present except 01-07 (interior) | 01-05..01-09 | **CANNOT SERVE** | — (all three caught this) |
| 6 | **`NwpRainfallRunoff`** past forcing | past forcing row at `T0` MISSING; future forcing complete to `H` | past forcing: **∅** (declares none — `_n_lags = 0`; its only past_known is the TARGET, lookback 1) | **SERVE** | validating forcing from `lookback_start` would wrongly REJECT |
| 11 | any, daily, `L=2`, `T = 2026-01-10 06:00Z` | complete buckets 01-08 and 01-09 | 01-08, 01-09 — **NOT 01-10**, which is still in progress | **SERVE** | an expected set including `T0` wrongly REFUSED |
| 12 | any, daily, `H=2`, `T = 2026-01-10 00:00Z` (exactly on a boundary) | buckets 01-10 and 01-11 | 01-10, 01-11 — starts AT `T0` because the issue instant is aligned | **SERVE** | always starting at `T0+S` wrongly demanded 01-12 |
| 13 | **training**, targets at 01-01 and 01-03 only | forcing present at exactly those two | {01-01, 01-03} — the consumed set, no anchor | **SERVE** | an anchor+horizon rule wrongly demanded 01-02 |
| 7 | **`SeasonalPrecipRunoffRegression`** temperature | temperature present for the 14 buckets BEFORE `T0`, ABSENT for days 15–45 | the 14 buckets before `T0` | **SERVE** | a class-wide 45-bucket set would wrongly REJECT |
| 8 | **`SeasonalPrecipRunoffRegression`** precipitation | precipitation missing on one day inside its 45 | the 45 buckets before `T0` | **CANNOT SERVE** — names precipitation | — |
| 9 | any, daily, L=1 | hourly data, complete for 01-09 | 1 daily bucket: 01-09 | **SERVE** — aggregated (precip SUM, temp MEAN) | — |
| 10 | any, daily, L=3 | no data at all for `V` | 3 buckets: 01-07..01-09 | **CANNOT SERVE** | — |

Rows 1–5 are the refuted mechanisms; **6 and 7 are the two a per-feature-class spec would still get
wrong**, which is why they are mandatory.

### Verification

`uv run pytest tests/unit/services/test_training_data.py -k MissingExpectedBuckets` — one test per row, each asserting the
verdict AND, on `CANNOT SERVE`, the series named and buckets reported. A test must fail against a
per-feature-class implementation (rows 6, 7), against each refuted mechanism (rows 1–4), and against
the boundary errors this review caught (rows 11–13). **Rows 1 and 9 must run through AGGREGATION**,
not be fed pre-bucketed data — an earlier version claimed to cover them and did not.

### Pre-change evidence

`hindcast_forecasts.time_step_seconds = 86400` for all 715,103 rows currently stored, with 1,134
consecutive one-day gaps — today's daily path must still produce exactly that after T1.

### T1 — SPLIT into T1a and T1b (owner decision, 2026-09-07)

The owner chose to ship these SEPARATELY. T1a changes what models are FED
(forecast values move); T1b changes whether they RUN AT ALL (some forecasts
stop being produced). Bundled, a surprising change after deployment has two
possible causes and no way to tell them apart. Split, each lands observable on
its own. Cost: one extra review round, accepted deliberately.

#### T1a — deliver past forcing at the declared step — **DONE** (2026-09-07)

All four assemblers resample `past_dynamic` to the model's declared `time_step`
using its own declared per-variable aggregation, over the SAME aligned
complete-bucket window `past_targets` has used since Plan 228:
`operational_inputs.py`, `track_assembly.py` (independent — fixing only the
first leaves this production route broken), `hindcast.py`, `training_data.py`.

Red-first and re-verified: each of the four tests was watched failing before
its fix, and each fix was then RE-REVERTED to confirm the test catches it. Two
of the four first failed for a SETUP reason (starved of data — indistinguishable
from a real red at a glance); both fixtures were corrected so the failure proves
the cadence defect and nothing else.

Independent evidence the replacement is complete: ruff reported the old naive
`lookback_start` as UNUSED in both `operational_inputs.py` and
`track_assembly.py` afterwards. Nothing else consumed it.

The three in-code comments and two docs calling the mismatch "legitimate" are
retracted — that claim is why this survived earlier review.

**What T1a does NOT do:** a model with holes in its history still runs. It now
runs on correctly-spaced data with holes in it. Refusing is T1b's job.

#### T1b — REDESIGNED BY THE OWNER 2026-09-08: LABEL, do not refuse

**The earlier T1b was a gate: `CANNOT SERVE` when an expected bucket is missing. The owner replaced
that design.** Recorded verbatim because it changes the outcome, not just the wording:

> "if the model asks for 210 days of rainfall and out of those we have 2 days of missing data, that
> should not be a problem and we can either ignore those or apply gap filling. if however, we're
> missing the latest 2 days for rainfall data, that might indeed be a problem for a reliable
> forecast. we should however, still produce a forecast but lable it degraded because of missing
> forecasts."

Two consequences:

1. **WHERE the gap sits decides severity.** 2 missing days out of 210, long ago, is not a problem.
   The same 2 days at the END is, because the model leans on recent conditions.
2. **NEVER refuse.** Always forecast; attach an honest label. A refusal loses information the
   forecast still carries.

**This uses machinery that already exists — no new component.** `services/input_quality.py`
already produces `FULL / PARTIAL / DEGRADED` with per-category `InputQualityFlag`s, already covering
observation staleness, NWP age and warm-up age. It has NO category for gaps in past forcing. T1b
adds that category and nothing else.

**Owner decisions, 2026-09-08:**
- *"Recent" = a fixed number of most-recent steps*, one rule for every model (chosen over scaling
  with lookback, and over a per-model declaration which no model makes today and which would
  require an FI contract change first).
- *Gap-filling is DEFERRED* — label first, measure how often gaps actually occur and where, then
  decide whether filling is worth it with evidence. Filling invents values; labelling is honest
  about what was there. Do not conflate them.

**The rule:**

| missing buckets among the expected past set | verdict |
|---|---|
| none | no flag — `FULL` unaffected |
| some, ALL older than the recent window | `PARTIAL`, naming the series and the count |
| any inside the recent window | `DEGRADED`, naming the series and which buckets |

The expected set is `expected_past_buckets` and the comparison is `missing_buckets` — both landed
in T1a, both already tested against the verdict table. T1b computes the set, splits it on the recent
window, and emits a flag. **No new predicate.**

**What T1b does NOT do:** refuse a forecast, fill a gap, change what a model receives, or judge
values (that is `max_nan`'s job, per T0).

#### T1b — folded from the independent Codex review, 2026-09-09

Seven findings, all verified against the code before folding. Two mattered:

- 🔴 **BLOCKER — the flags were computed only on the success path.** `past_forcing_flags` ran
  AFTER `predict`, so a model that refuses its own short window produced no forecast AND no
  forcing diagnosis. The seasonal model does exactly that: it correctly RETURNS `ModelFailure`
  (FI-compliant), the adapter re-raises it as `ModelOutputError`, and the runner returns
  `PREDICT_FAILED` before the flags were ever built. **Measured live on staging 2026-09-09: 136
  `short_forcing_window` events and 604 `predict_failed` in 30 hours** — the exact population this
  task exists to explain. The analysis now runs BEFORE `predict` and travels with the failure.
  ⚠️ **This records the reason; it does not make a refusing model forecast.** A model whose own
  contract rejects an incomplete window still fails. Filling that window is **Plan 261**.
- 🟠 **MAJOR — every series was judged on the collapsed maximum lookback.** `lookback_steps` is a
  MAX across declared variables; the seasonal model declares precipitation=45 and temperature=14,
  so a 30-day-old temperature hole — a bucket the model never reads — was reported as a gap. The
  FI adapter now preserves each variable's own declared lookback
  (`ModelDataRequirements.declared_lookbacks`, same tuple-of-pairs shape as
  `declared_aggregations`), and each series is judged on its own window. 🪤 Per name this is a MAX
  across branches, NOT a conflict check: one variable legitimately carries different lookbacks in
  different (product, time_step) branches, and an early revision that raised on that broke 14
  adapter tests.

Also folded: `forcing_recent_steps` is now bounded `ge=0` (a negative value put the cutoff in the
future and inverted the rule); three tests that passed against a wrong implementation were made
discriminating — each new test was verified RED against the specific mutant it targets; and
`FORCING` / `forcing_recent_steps` are documented in `docs/spec/types-and-protocols.md` and
`docs/spec/config-reference.toml`.

⚖️ **Unchanged by this review, on the owner's decision (2026-09-09):** `forcing_recent_steps`
ships at **2**. Past forcing measured **2.29 days** behind on staging, so DEGRADED fires on
essentially every forecast until Plan 261 lands. The owner was shown this and chose to ship.



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
