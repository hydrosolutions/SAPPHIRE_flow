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

**This plan has NOT been through a review round of its own.** Plan 222's rounds established the
problem; they did not review this solution, which did not exist in this shape.

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
