---
status: DRAFT
created: 2026-08-31
plan: 226
title: The daily models label calendar-day quantities with wall-clock instants — and two paths they depend on are already broken
scope: Anchor the daily models' valid_times to the calendar day they actually predict, and fix the two pre-existing defects that make anchoring meaningless without them (hindcast lookback by rows, skill's unresampled observation join). Restores the combined forecast that Plan 222 takes dark.
depends_on: [222]
blocks: []
source: 2026-08-31 — split out of Plan 222 after two review rounds showed the anchoring half rests on pre-existing brokenness
---

# Plan 226 — anchoring, and the two defects underneath it

## Status

**DRAFT** — not reviewed. Split from Plan 222 (owner, 2026-08-31) after round 2 of that plan's
independent review found the anchoring half entangled with defects it never set out to fix.

**This plan has NOT been through a usable review round of its own.** Plan 222's rounds established
the problem; they did not review this solution.

**A `plan` workflow round was attempted 2026-09-01 and its output was DISCARDED.** It stalled after
three rounds at 5 blockers + 2 majors — but every one of those findings was about material the loop
had itself added. It grew the document from 186 lines to 526, inventing a `computation_version`
cutover, an Alembic migration, a ForecastInterface conversion prerequisite, a "P3" section and a
skill-store version-selection rewrite — none of which this plan proposes. The Proportionality guard
above was already in the document and was ignored. The working copy was reverted; nothing was
committed.

**For the next reviewer:** the guard above is not decoration. This plan is 186 lines and three
concerns (anchoring, P1, P2) with six deliberately-open questions. If a round returns findings about
schema migrations, version cutovers or FI conversion, it has expanded the plan rather than reviewed
it, and its output should be discarded rather than folded.

## ⛔ Proportionality — BINDING on this plan AND on its review

**Read this before reviewing.** Plan 222, this plan's sibling, went through four adversarial review
rounds that each ADDED, growing a fix for one reported defect from five tasks to eight. A fifth
round — a proportionality review — cut it back to three and returned **TOO BIG BY 5 TASKS**. That
round-trip cost more than the fix. Do not repeat it here.

**A finding that GROWS this plan is a worse finding than one that shrinks it.**

**This is a MEASURE-FIRST plan and it is deliberately incomplete.** The six design questions below
are open ON PURPOSE. T-M measures before anything is designed, because answering first is precisely
what cost Plan 222 two rounds: its D1 was settled on a confident argument that a later round proved
false against the code.

**In scope for findings:** a stated fact is false; a citation does not say what the plan claims;
T-M would not actually measure what it claims to measure; the scope boundary against Plan 222 is
wrong.

**Explicitly OUT of scope — do not propose, and reject if proposed:**

- **Answering the six open design questions.** Recommending an answer, narrowing the options, or
  declaring one "obvious" is over-reach until T-M reports. Saying *"question 3 is missing a case"*
  is useful; saying *"question 3 should be resolved as X"* is not.
- New tasks, phases, abstractions, registries or plug-in points.
- Exit gates beyond those already fixed. They are deliberately unwritten pending T-M.
- Anything Plan 222 owns or deferred: pooling semantics, the member-id collision, per-parameter
  `source_model_ids`, the alert-path union, BMA. All have their own homes.
- Backfill, recomputation, or migration of stored forecasts or hindcasts.
- Performance work.

**"No findings" is a complete and valuable review.** A round that reports the plan checks out is a
success, not a wasted round. Do not manufacture findings to justify the pass.

**If a reviewer believes a genuinely blocking problem sits outside these bounds, say so in one
sentence and stop there** — do not design the fix into this plan.

## Why this is a separate plan

Plan 222 originally paired anchoring with intersection pooling, because the intersection is empty
under today's grids and the invariant alone takes the combined forecast dark. Two rounds showed
that pairing was wrong: intersection is a small, local, well-tested change, while anchoring turned
out to sit on top of two live defects. Plan 222 ships the invariant and accepts the outage; this
plan earns the product back.

## The mislabelling

`LinearRegressionDaily` trains and predicts on midnight-bucketed daily discharge
(`services/training_data.py:106-110` feeding `services/operational_inputs.py:551`). Its step-*k*
output is the *k*-th daily mean after the last observed day — a calendar-day quantity. It labels
that `issue_time + (k+1) · time_step` (`models/linear_regression_daily.py:164-166`), and a
scheduled cycle's `issue_time` is the raw wall clock (`flows/run_forecast_cycle.py:684-690`), so
the published timestamp was `2026-08-31T18:00:01.851153Z` for a quantity describing a calendar day.

`PersistenceFallback` (`models/persistence_fallback.py:92`) and `ClimatologyFallback`
(`models/climatology_fallback.py:145`) use the same construction.

The NWP models are already correct: they adopt the delivered forcing timestamps
(`models/nwp_regression.py:472`), which are UTC-midnight buckets
(`services/operational_inputs.py:147`). They are the grid everything else must move onto, and this
plan does not touch them.

## 🔴 The two defects underneath — measure before designing

**Neither was introduced here. Both are live today, and anchoring is meaningless without them.**

### P1 — the hindcast lookback takes ROWS, not daily buckets

`_extract_discharge()` returns `sorted_df.tail(_LOOKBACK)` — the last **7 rows**, with an error
message that says "need 7 rows" (`models/linear_regression_daily.py:42-49`). The operational path
resamples `past_targets` to daily (`services/operational_inputs.py:551`) so 7 rows are 7 days. The
hindcast path passes **raw, unresampled observations** (`services/hindcast.py:201`).

At a 10-minute observation cadence a hindcast therefore runs on roughly **seventy minutes** of
history while declaring a seven-day autoregressive window. Every hindcast, and every skill score
derived from one, for this model.

**Relabelling the output cannot fix this**, which is why Plan 222's D1(a′) failed review.

### P2 — skill joins daily forecasts to instantaneous observations

`_build_strata` looks each forecast `valid_time` up in an **unresampled** observation lookup
(`services/skill/service.py:338`, `services/skill/service.py:92-95`). A daily-mean forecast is
therefore scored against the single instantaneous reading at that timestamp, not against the daily
mean it was trained to predict. Aligning timestamps does not make the comparison commensurable.

### T-M — measure both before writing any fix (read-only)

Quantify, against real data: the observation cadence per station; what `_extract_discharge()`
actually consumes in a hindcast today; the magnitude of the skill error P2 introduces; and the
distribution of `last_bucket` relative to `issue_date`.

**No writes, no deploy, no interruption of a running flow.** This plan does not proceed to design
until P1 and P2 are sized. The repo has a recorded habit of being wrong about operational numbers
by reasoning rather than measuring, and two review rounds have now made that concrete here.

## T-M RESULTS — measured 2026-09-01 against the live mini (read-only)

Queries only, no writes, no flow interrupted; the station-onboarding backfill was running throughout
and the DB showed one active query (ours) at the time.

| # | Measurement | Result |
|---|---|---|
| M1 | Observation cadence (`discharge`, 14 d) | **604 s ≈ 10 min**, uniform across every station sampled |
| M2 | What `_extract_discharge()` consumes in a hindcast | 7 rows × 10 min = **70 minutes**, against a declared 7-day (10 080 min) window — a **144× shortfall**. P1 CONFIRMED on real data |
| M3 | Daily mean vs the instantaneous 00:00 reading | median **6.4 %**, mean 12.4 %, p95 **48.6 %**, max 78.8 % (n = 134 station-days). P2 CONFIRMED and material |
| M4 | Observation staleness at issue | **0.00 h on every cycle**; `bucket_lag_days = 0` throughout |

**Scale, which differs from the original report.** 148 stations now carry all six model assignments,
but only **37** produce any forecast, only **2** produce `linear_regression_daily` / `nwp_regression`
/ `_pooled`, and 36 produce `nwp_rainfall_runoff`. Most stations therefore have a single combinable
model producing and no combination to make. Plan 222's "dark for 2009 and 2091" remains exact.

**Production issue times carry the sub-second wall clock**, live: `06:00:02.768568`,
`18:00:02.599972`, `18:00:01.851153`. The raw `clock()` read this plan describes is not theoretical.

### What the measurements bear on — WITHOUT closing the questions

- **Q3 (anchor to last observed day vs issue day) is empirically MOOT in this deployment.** At zero
  staleness the last observation's daily bucket *is* midnight of the issue day, so (a) and (b) give
  the identical timestamp on every cycle measured. The choice is therefore about failure behaviour
  under staleness that does not currently occur — not about today's output. It remains the human's,
  and (a) still degrades correctly where (b) silently mislabels.
- **Anchoring should restore the combined forecast in full.** Under (a) with zero staleness,
  `linreg` step 1 = midnight(issue day) + 1 d. The NWP path drops the issue-day bucket as backdated
  at a non-midnight issue (`services/operational_inputs.py:225-236`), so its first future bucket is
  the same day. The grids coincide and the intersection is the whole 5-day horizon. **Arithmetic
  from the measurements, not a live test** — it needs proving against a real cycle before it is
  relied on.
- **Q4 (all steps backdated) does not arise at current staleness**, and Q6 (multi-parameter
  fallbacks) is untouched by these measurements.
- **P1 and P2 are confirmed as live defects, and P2 is now sized.** A skill score computed on this
  path carries a median 6.4 % and tail ~49 % mismatch that is pure quantity error.

## Open design questions — deliberately NOT answered yet

Answering these before T-M reports would repeat the mistake that cost Plan 222 two rounds.

1. **Does P1 get fixed by resampling in the hindcast path, or by making the model resample its own
   lookback?** The first makes hindcast match operational; the second makes the model
   self-sufficient and independent of who assembles its inputs. They differ in blast radius.
2. **Does P2 get fixed by resampling the observation lookup, or is the daily-vs-instantaneous
   comparison acceptable and merely undocumented?** A daily mean and a midnight instantaneous
   reading are not the same quantity, but the size of the resulting error is an empirical question
   that T-M answers.
3. **What exactly does step *k* predict** — the *k*-th day after the last observed day, or after the
   issue day? Plan 222 settled this as "after the last observed day" (its D1(a)) and that survives,
   but its *construction* did not. The anchor must be computed by truncation, not by reading a
   `past_targets` row (`services/training_data.py:66-87`: the resampler no-ops below two rows, so a
   lone 07:20 observation stays at 07:20).
4. **What happens when every step is backdated?** At five or more stale days a
   drop-steps-at-or-before-`issued_at` rule removes the whole forecast;
   `ForecastEnsemble.from_members` rejects an empty frame (`types/ensemble.py:58`) and an empty
   ensemble dict would be recorded as a **successful** primary model
   (`services/run_station_forecast.py:640`). The required outcome is undecided.
5. **Is the boundary `<` or `<=`?** The NWP path treats a valid_time *equal* to `issue_time` at
   midnight as genuinely future and deliberately keeps it (`services/operational_inputs.py:225-236`).
   Any drop rule here must agree with that convention rather than contradict it.
6. **How is "the last observation" defined for a multi-parameter fallback?** `PersistenceFallback`
   can emit several parameters (`models/persistence_fallback.py:39`) whose final non-null
   timestamps differ (`services/operational_inputs.py:411`). One frame-level anchor would date a
   stale parameter from another parameter's fresher reading.

## Carried forward from Plan 222's review rounds

Established facts, so they are not rediscovered:

- **`ClimatologyFallback`'s values change with its timestamps** — it selects quantiles by the
  day-of-year of the proposed `valid_time` (`models/climatology_fallback.py:143-148`), so a change
  of *date* changes the values. A change of *time within the same date* does not. Round 2 corrected
  round 1 on this.
- **`PersistenceFallback`'s values also change**, in the opposite way to what round 1 assumed:
  spread scales with the step index (`models/persistence_fallback.py:91`), so dropping or retiming
  steps changes the quantiles at the retained timestamps.
- **`observation_staleness_hours` explains nothing on a combined row** — it is hard-coded `None`
  (`services/forecast_combination.py:254`). A shortened horizon has no published explanation.
- **A test asserting only "all timestamps are midnight" locks nothing.** It passes for issue-day
  anchoring, for last-bucket anchoring, and for an off-by-one. Round 2 rejected exactly this.
- **Two of Plan 222's proposed T1 tests were green against current code**, because current code
  ignores `past_targets` entirely and every current timestamp already follows issuance. Any test
  here must be demonstrated red first.

## Non-goals

- Pooling semantics. Plan 222 owns them and lands first.
- The pooled member-id collision — Plan 222 T3b.
- Changing `NwpRegression` / `NwpRainfallRunoff` timestamps.
- Snapping the scheduled cycle's issue time. Plan 222 round 1 cut this: rounding admits the
  issue-day NWP bucket and shifts the NWP grid by a day
  (`services/operational_inputs.py:225-236`). Do not reintroduce it.
- Backfilling or recomputing stored forecasts or hindcasts.

## Exit gates

To be written once T-M reports and the design questions are answered. The gates below are fixed
regardless of the design chosen:

- Every locking test demonstrated **failing against the pre-change code**. Two of Plan 222's did
  not, and that is the specific failure this gate exists to prevent.
- A test that distinguishes the chosen anchoring from *every* plausible alternative, including
  issue-day anchoring and an off-by-one — not merely "the timestamps are midnight".
- `uv run pytest tests/unit` — zero failures. The bar is zero.
- An independent review round before READY, on this plan's own terms. Plan 222's rounds do not
  transfer.
