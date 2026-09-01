---
status: DRAFT
created: 2026-09-01
plan: 228
title: Every skill score in the database is computed on the wrong comparison, and two models hindcast on 70 minutes of history
scope: Two defects in how hindcast and skill scoring consume observations. Fix both, then recompute. No change to any model's maths, no change to the operational forecast path, no anchoring work (that is Plan 226).
depends_on: []
blocks: [226]
source: 2026-09-01 — measured on the live mini during Plan 226's T-M task
---

# Plan 228 — hindcast and skill are running on the wrong data

## Status

**DRAFT — HIGH PRIORITY.** Awaiting owner READY. **All three decisions are settled** (owner,
2026-09-01) and both investigations they asked for are done — see D1's FI finding and the training
result below.

Both defects are **live on the mac-mini right now**. Skill scoring ran 1.5 hours before this plan
was written. Nothing here is hypothetical or projected — every number below was measured.

## What is wrong

### P1 — two models hindcast on ~70 minutes of history while declaring seven days

`LinearRegressionDaily._extract_discharge()` takes `sorted_df.tail(7)` — the last seven **rows**
(`models/linear_regression_daily.py:42-49`, error text: "need 7 rows").
`NwpRegression._initial_lags()` does the same: `values[-self._n_lags:]`
(`models/nwp_regression.py:500-505`).

Both are correct in the **operational** path, because `assemble_station_operational_inputs`
resamples observations to the model's `time_step` first
(`services/operational_inputs.py:551`), so seven rows *are* seven days.

The **hindcast** path does not resample. It passes the raw observation frame straight through
(`services/hindcast.py:201`, `past_targets=obs_df`).

**Measured:** observation cadence is **604 s (~10 min)**, uniform across stations. Seven rows is
therefore **70 minutes** against a declared 10 080-minute window — a **144× shortfall**. Neither
model notices: `_initial_lags`'s only guard is a *count* check (`nwp_regression.py:418`), which
seven raw rows pass.

`NwpRainfallRunoff` is weather-only (`_n_lags = 0`) and is not affected by P1.
`ClimatologyFallback` does not read `past_targets` at all. `PersistenceFallback` takes
`past_targets[param][-1]` (`models/persistence_fallback.py:88`) — it persists the latest
instantaneous reading rather than the last daily mean, a smaller error of the same family.

### P2 — skill scores compare a daily mean against a single instantaneous reading

`_build_strata` looks each forecast `valid_time` up in an **unresampled** observation lookup
(`services/skill/service.py:92-95, 338-342`). The forecast is a daily mean; the observation is
whatever was measured at that instant.

**Measured** (discharge, 14 days, n = 134 station-days): median **6.4 %**, mean 12.4 %,
p95 **48.6 %**, max **78.8 %**. That error is pure quantity mismatch and is present in every skill
score regardless of how good the forecast was.

## Impact — measured on the mini, 2026-09-01

| | Count |
|---|---|
| Skill scores in the database | **106 910** |
| …affected by P2 (all of them) | **106 910** |
| …also built on P1-corrupted hindcasts (`linear_regression_daily`, `nwp_regression`) | **35 377** |

Skill scoring is **actively running**: `nwp_rainfall_runoff` scored at `2026-09-01 09:18`,
`linear_regression_daily` at `2026-08-31 23:17`. Every new score inherits both defects.

**Consequence: no skill number currently in this system means what it says.** Anything that reads
them — model promotion (`min_skill_samples` / `min_skill_seasons`), the API's skill surfaces, model
ranking — is drawing on corrupted input.

## Decisions the owner must make

### D1 — where does the daily-averaging fix go? (owner-settled: **C**, and it is CONFORMANCE)

**🔴 The ForecastInterface contract already requires C. We are violating it.** The owner's intuition
was that "this is what we claim with the ForecastInterface"; checked against the pinned package
(`forecastinterface` v0.1.19, `.venv/.../forecast_interface/`), it is exactly right:

- `InputRequirement.dynamic` is `dict[datetime.timedelta, SpatialInputSpec]`
  (`input/requirement.py:41`) — **the time step is the KEY**. A model declaring
  `{timedelta(days=1): ...}` is declaring that its inputs arrive daily.
- `PastKnownVariable.lookback` is an `int` (`input/variable.py:14-15`) — **a count of STEPS**, not a
  duration. "7" means seven of the declared step, and the model is entitled to read it that way.
- `PastKnownVariable.aggregation: AggregationMethod | None` (`input/variable.py:18`) — the model
  declares **how** its data should be aggregated to that step. That field is meaningless unless the
  **provider** performs the aggregation.

So the contract says: *deliver `lookback` steps of size `time_step`, aggregated by `aggregation`.*
Our hindcast path delivers raw 10-minute rows against a declared `timedelta(days=1)`, and the model
does precisely what the contract entitles it to do. **This is not a model bug and not a new
requirement — it is SAP3 failing to meet a contract it already claims**, which
`CLAUDE.md` § ForecastInterface Adherence classifies as "our code violates the FI → fix our code".

**Therefore C is not the expensive option; it is the correct one.** A is the mechanism (the provider
aggregates, exactly as `services/operational_inputs.py:551` already does); C is the enforcement that
makes a silent violation impossible. Implement both: aggregate at the boundary, and validate that
what was delivered matches what was declared.

| | Option | Cost | Risk |
|---|---|---|---|
| **A** | **Resample in the hindcast input assembly** — `services/hindcast.py` buckets `obs_df` to the model's `time_step` before building `StationInputData`, exactly as the operational path already does | Smallest: one function, one place | Any *other* caller that assembles model inputs stays free to feed raw rows; the models keep trusting whoever feeds them |
| **B** | **Each model resamples its own lookback** before taking its tail | 2-3 models, via a shared helper | Models become correct regardless of caller; but it changes model internals and must be proven a no-op in the operational path |
| **C** | **Validate at the boundary** — the model declares a `time_step`, and input assembly fails loudly when the delivered cadence does not match it | Largest | Makes silent recurrence impossible anywhere, but needs a single validation point both paths pass through |

**Settled: C, implemented as A + enforcement.** B (models resampling their own inputs) is now
explicitly **rejected**: under the FI contract, aggregation is the provider's responsibility, so
pushing it into models would move us *further* from conformance, not closer.

### D2 — how is the scoring comparison fixed? (owner-settled: **B**)

| | Option |
|---|---|
| **A** | Resample the observation lookup to **daily means** before the join |
| **B** | Resample to the **forecast's own `time_step`**, whatever it is — the same fix, done once, correct for sub-daily models too |

**Settled: B.** A is B special-cased to today's only cadence.

To be explicit about what B means, because it is the whole point: **the observed runoff must be
aggregated to the forecast's step before comparison.** An instantaneous reading cannot be compared
against a daily-average forecast. The aggregation method must match the one the model trains
against (`_V0_AGGREGATION_FALLBACK`, `services/training_data.py:69-73`) — mean for discharge — or
the fix substitutes one mismatch for another.

### D3 — what happens to the 106 910 existing scores?

They cannot be repaired in place; they must be recomputed after D1 and D2 land.

**Settled (owner, 2026-09-01): MARK them superseded, and only once the running process finishes.**

⛔ **Do not interfere with the mac-mini.** A station-onboarding run and its hindcasting are in
flight and are **a deliberate test**. They will keep producing scores that carry both defects; that
is accepted. The test's value is in exercising the pipeline, not in the numbers it emits.

Sequence: let onboarding and hindcasting finish → mark every score predating the fix as superseded
→ land D1 and D2 → recompute all hindcasts and scores. Marking is chosen over deletion so the test
run's *existence* stays auditable while its *numbers* stop being trusted.

## Tasks

### T1 — a measurement that fails, before any fix

Prove P1 as a test, not as an argument: build the hindcast inputs for one station at a 10-minute
cadence and assert that what the model receives spans the declared lookback window. This must
**fail** against current code, reporting ~70 minutes against 7 days.

### T2 — fix P1, per D1

### T3 — fix P2, per D2. A locking test must compare a daily-mean forecast against a daily-mean observation and fail against the current instantaneous join.

### T4 — execute D3, and state in `docs/` that every score predating this plan is invalid.

## Non-goals

- Anchoring the daily models' `valid_time` — **Plan 226**, which this plan blocks.
- Any change to model maths, coefficients, or artifacts.
- Any change to the operational forecast path, which is correct today.
- Retraining. **Investigated at the owner's request (2026-09-01): the training path is CLEAN.**
  `build_station_training_data` resamples before use — `past_targets_df = resample_to_time_step(...)`
  (`services/training_data.py:285-287`) — exactly as the operational path does. **Model artifacts are
  therefore NOT corrupted, and no retraining is required by this plan.** The defect is confined to
  the hindcast path: training resamples, operational resamples, hindcast does not.

## Exit gates

- T1's measurement demonstrated failing before the fix and passing after.
- `uv run pytest tests/unit` — zero failures.
- A recomputed skill score for one station, compared against its pre-fix value, with the difference
  recorded. If the difference is negligible the premise is wrong and this plan should stop.
