---
status: DRAFT
created: 2026-09-04
plan: 239
title: Two silent-data gates — hindcast forcing cadence, and skill sample size
scope: Add a cadence backstop to the hindcast FORCING path (Plan 228 fixed only observations), and make skill scores state what they actually rest on (minimum-sample gate + covered-vs-requested eval window). Two small guards against silently-wrong numbers. No new subsystem, no change to how skill is computed.
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

## Open questions the owner owns

- **Q1:** the minimum-sample threshold, and what happens at it — record a flag/`freshness`-style
  marker on the row (recommended), or emit a pipeline-health WARNING, or both?
- **Q2:** should `eval_period_*` become the COVERED window, or should a separate covered-window pair
  be added alongside the requested one? Overwriting is smaller; adding preserves "what was asked".
  **Recommendation: overwrite**, because the requested window is already implicit in the run's params
  and the stored value's only real consumer question is "what does this score rest on?"
- **Q3:** does D1 also apply to the TRAINING path, or is `services/training_data.py` already covered?
  It owns `resample_to_time_step`, but this plan has not verified its call sites.

## Phases

### T1 — forcing cadence backstop (D1)
`services/hindcast.py`: route `forcing_df` through `resample_to_time_step` +
`validate_time_step_cadence`, mirroring the `obs_df` block Plan 228 added. Red-first test: hourly
forcing into a daily model must FAIL LOUDLY, not silently deliver 24 rows as one step.

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

1. Hourly forcing into a daily model fails loudly in a test (T1's red-first case), and the existing
   daily path is unchanged — `time_step_seconds = 86400` still holds for a re-run.
2. A low-n score is distinguishable from a well-supported one without inspecting the table.
3. A scoring window containing a data hole reports its covered span.
4. Unit suite green.

## Deferred (explicitly not this plan)

Backfilling the **2021–2025 discharge hole** (a data-acquisition question, not a code one, and the
single biggest limit on any Swiss skill number today); recompute identity and mark-and-replace
(**Plan 235** owns it); the operational forcing bridge (**Plan 134**, which explicitly scopes
hindcast out); changing any metric definition; suppressing low-n scores.
