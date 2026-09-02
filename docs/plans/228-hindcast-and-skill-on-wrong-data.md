---
status: READY
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

**READY — HIGH PRIORITY.** Owner confirmed 2026-09-01. Cleared for `/implement`.

All three decisions are settled (D1 = C, D2 = B, D3 = mark superseded), both requested
investigations are done (the FI contract finding under D1; training is clean, see Non-goals), and
an independent Codex pass verified all nine load-bearing factual claims with no blockers.

⛔ **Do not implement against the mac-mini until its onboarding and hindcasting test finishes**
(D3). The code fix can be built and merged meanwhile; only the marking and the recompute wait.

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

**The FI documentation says so directly**, which is stronger than the inference above: the pinned
version's `docs/input_requirement.md:3,109-115` assigns coarser-resolution aggregation to the
preprocessing/SAP3 side explicitly (confirmed by independent review, 2026-09-01).

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

### D4 — THE AUTHORITATIVE GRID (added 2026-09-02, after the first implementation went wrong)

**Every path aggregates onto UTC calendar buckets, and every model receives exactly N COMPLETE
buckets.** Nothing aligns to a forecast's own timestamp.

This decision exists because the first implementation did the opposite. It added an `anchor`
parameter to `resample_to_time_step` and phase-aligned buckets to `issue_time` / `valid_time`
(37 lines), so a 06:00 hindcast consumed rolling 06:00-06:00 means while training consumed UTC
calendar days — replacing one quantity mismatch with another. It aligned to `valid_time`, which is
precisely the value Plan 226 exists to correct.

**The repo already commits to calendar days**, which is why this is parity rather than a new
convention: NWP aggregation uses UTC calendar days (`services/operational_inputs.py:141`), the NWP
model deliberately validates the same previous calendar days at every 06/12/18Z cycle
(`models/nwp_regression.py:638`), and training buckets to midnight. The artifacts were trained on
calendar days; anchoring to "now" would only be right for artifacts trained on a rolling quantity,
and these were not.

**"Drop the incomplete trailing bucket" is NOT sufficient** *(independent review, 2026-09-02, which
falsified the first version of this rule)*. Operational assembly fetches exactly
`lookback_steps × time_step` ending at a non-midnight `issue_time`
(`services/operational_inputs.py:538`), so the window has a partial bucket at **both** ends.
Dropping only the trailing one leaves a corrupt leading day; dropping both leaves **six** complete
days for a seven-day model.

**The invariant is therefore: exactly N complete UTC-calendar buckets, which requires ALIGNING AND
EXTENDING the fetch bounds** — not filtering after the fact. It applies to every assembly path:
hindcast, both operational assemblers, and scoring.

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

### T5 — snap the training window's default end to a complete bucket

Training **already accepts** `period_start` and `period_end` (`flows/train_models.py:400-412`); no
new parameter is needed. The defect is the default: `period_start` falls back to five years ago at
**midnight** (aligned, correct), while `period_end` falls back to `clock()` — a raw instant, which
produces the same partial trailing bucket D4 forbids everywhere else.

Snap the default end to the last complete bucket boundary at or before now. An explicitly supplied
`period_end` is the caller's business and passes through unchanged.

**This does not invalidate existing artifacts.** A partial final bucket is one imperfect row at the
end of a multi-year training set, not a systematic mismatch — unlike P1, which corrupted every
hindcast. No retraining is required by this plan.

## Non-goals

- Anchoring the daily models' `valid_time` — **Plan 226**, which this plan blocks.
- Any change to model maths, coefficients, or artifacts.
- ~~Any change to the operational forecast path, which is correct today.~~ **RETRACTED
  2026-09-02 — this was false, and is now in scope (D4).** Both operational assemblers do resample,
  but neither aligns bounds nor checks completeness, so at a 06/12/18Z cycle the most recent bucket
  is a **partial day** presented as a full one. Measured: **37 rows where a full day is 144** at
  10-minute cadence. Nothing stops it reaching the model — the cadence validator compares bucket
  *labels* not sample counts (`services/training_data.py:119`), the short-lookback check only warns
  (`services/operational_inputs.py:592`), and FI's `max_nan` counts nulls rather than source samples
  (`adapters/forecast_interface.py:909`). The partial mean is finite, so `tail(7)` reads it as the
  latest full day on **3 of 4 daily production cycles**. Same defect class as P1, smaller magnitude,
  live.
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

## ⛔ PER-RUN SCOPE (2026-09-02) — this run REVERSES a wrong turn. Read before implementing.

The branch already holds three commits. **Most of that work is correct and must be kept. One part
is wrong and must be removed.** Do not rebuild from scratch; do not revert the branch.

### REMOVE — the `anchor` mechanism (~37 lines) and the tests that lock it

A previous fixer round added an `anchor` parameter to `resample_to_time_step` and passed
`anchor=issue_time` / the forecast's `valid_time`, phase-aligning buckets to the forecast timestamp.
**D4 forbids this.** It made hindcast consume rolling 06:00-06:00 means while training consumes UTC
calendar days, and it aligned to `valid_time` — the value Plan 226 exists to correct. Its
acceptance tests lock the wrong semantics and must go with it. Delete the parameter, its call sites
and those tests.

### IMPLEMENT — D4, on every assembly path

Exactly **N complete UTC-calendar buckets**, with **aligned and extended fetch bounds**. Filtering
after the fact is not sufficient: the current window has a partial bucket at BOTH ends, so dropping
the trailing one leaves a corrupt leading day and dropping both starves a 7-day model to 6 days.
Applies to `services/hindcast.py`, `services/operational_inputs.py`, `services/track_assembly.py`
and the scoring path in `services/skill/service.py`.

### ALSO FIX — three findings from the last round that D4 does not cover

1. Hindcast validation examines the whole delivered frame while runners default to
   `lookback_steps=720`, so one old gap can suppress hindcasts for up to 720 days. Derive the
   lookback from the model's declared `lookback_steps` and trim to the consumed window before
   validating.
2. `compute_skills_task` fetches observations over `[min(hindcast_step), max(hindcast_step))`, which
   for a single hindcast is an empty range — so production callers fetch no observations at all.
   Derive the bounds from the ensemble valid times and their step; fix the single, combined and
   onboarding callers.
3. Mixed time steps or phases across hindcasts are silently coerced to `min(step)` plus the first
   forecast's phase. Require and validate one `(time_step, phase)` per computation, or partition by
   it.

### KEEP — do not undo

The `resample_to_time_step` call in the hindcast path; `validate_time_step_cadence`; migrations 0050
(hindcast `time_step` persistence) and 0051 (skill natural key + `computation_version`); the T3
scoring work apart from its anchoring; and all of T4's documentation.

### ADD — T5

Snap training's default `period_end` to the last complete bucket boundary
(`flows/train_models.py:400-412`). The parameter already exists; only the default is wrong.

### ⛔ STILL FORBIDDEN

Do not touch the mac-mini. D3's marking and the recompute wait for its onboarding and hindcasting
test to finish. Build the code; do not run it against that system.

## Implementation status (2026-09-01) — T1-T3 COMPLETE + committed; T4 PARTIAL

**T1/T2 (P1)**: `services/hindcast.py::_assemble_hindcast_inputs` now resamples
`past_targets` to the model's declared `time_step` via the SAME
`resample_to_time_step` (`services/training_data.py`) the operational path
already used (D1 = A). Enforcement (D1 = C) added as
`validate_time_step_cadence` (new, `services/training_data.py`), a hard
backstop called after every `past_targets` resample in `hindcast.py`,
`operational_inputs.py`, AND `track_assembly.py` — scoped to `past_targets`
only, since `past_dynamic` legitimately carries a different cadence than
`time_step` in the operational path. Locking test
(`tests/unit/services/test_hindcast.py::TestHindcastResamplesPastTargetsToDeclaredTimeStep`)
proven RED against the stashed pre-fix code (span 1:00:00 vs. declared 7
days), GREEN after.

**T3 (P2)**: `services/skill/service.py::compute_skill_for_station` now
infers the forecast's own `time_step` from the hindcast data
(`_infer_forecast_time_step`) and resamples observations to it before the
`_build_strata` join (`_resample_observations_to_forecast_step`, reusing
`resample_to_time_step` + a new `aggregation_method_for` helper so the
aggregation matches what the model trains against — D2 = B). Locking test
(`tests/unit/services/skill/test_service.py::TestDailyMeanComparedAgainstDailyMeanObservation`)
proven RED against the stashed pre-fix code (MAE 40.0 vs. the deliberately
offset boundary reading), GREEN after (MAE < 1.0).

**T4**: the docs half is done — `docs/decisions/plan-228-hindcast-skill-resampling.md`
(new), `docs/v0-scope.md`, and `docs/touchpoint-maps.md` all now state that
every `skill_scores`/`hindcast_forecasts` row predating 2026-09-01 is invalid.
**The live half (mark pre-fix scores superseded + recompute) was
DELIBERATELY NOT executed**, per this plan's own "⛔ Do not implement against
the mac-mini until its onboarding and hindcasting test finishes" — that
system was not confirmed to have reached that state during this
implementation session, and it is an operational action outside a code PR
regardless. This remains an open follow-on for whoever confirms the mini's
run has finished.

**Exit gates**: the "recomputed skill score for one station, compared against
its pre-fix value" gate was satisfied via the T3 locking test's
stash-and-restore proof (a controlled, reproducible before/after comparison)
rather than a live mac-mini recompute, for the same D3 reason. `uv run pytest
tests/unit` — see the implementer's own report for the exact run and result.

## Implementation status (2026-09-02) — PER-RUN SCOPE round COMPLETE

Executed the "⛔ PER-RUN SCOPE" section above in full, on top of the branch's
existing T1-T4 work (kept, not rebuilt):

- **REMOVED** the `anchor` mechanism: `resample_to_time_step` no longer takes
  an `anchor` parameter; `_anchor_phase_shift` (training_data.py),
  `_forecast_valid_time_anchor` (skill/service.py), and the two tests that
  locked the retracted phase-aligned behavior
  (`TestHindcastPastTargetsBucketsAlignToIssueTime`,
  `TestObservationBucketsAlignToForecastValidTimePhase`) are gone.
- **IMPLEMENTED D4**: `aligned_lookback_bounds` + `floor_to_time_step` (new,
  `services/training_data.py`) give every `past_targets` fetch (`hindcast.py`,
  `operational_inputs.py`, `track_assembly.py`) aligned-and-extended bounds —
  exactly `lookback_steps` COMPLETE UTC-calendar buckets, never a naive
  window with a partial bucket at either end. Skill scoring's observation
  resample lost its `anchor` too — UTC-calendar buckets only, everywhere.
- **ALSO FIXED** all three findings: (1) hindcast cadence validation now
  scopes to the model's own `declared_lookback_steps`, trimmed from the tail,
  not the runner's much larger fetch window; (2) `observation_fetch_bounds`
  (new) derives observation-fetch bounds from the ensembles' own
  `valid_time`s for `compute_skills_task`, `compute_combined_skills_task`,
  AND onboarding's skill callback — a single hindcast no longer collapses to
  an empty fetch range; (3) `validate_homogeneous_time_step_and_phase`
  replaces the silent `min()`-coercion with a hard raise on mixed
  `time_step` or phase.
- **T5 ADDED**: `train_models_flow`'s default `period_end` (when omitted)
  now snaps to `floor_to_time_step(clock(), time_step)` instead of a raw
  `clock()` instant.
- **KEPT** everything the scope said to keep: the hindcast `resample_to_
  time_step` call, `validate_time_step_cadence`, migrations 0050/0051, and
  T3's scoring work apart from its anchoring.
- Full detail, rationale, and locking tests: `docs/decisions/plan-228-hindcast-skill-resampling.md`
  § D4.
- **Still forbidden and still not done**: the mac-mini was not touched; D3's
  marking-superseded + recompute remain gated on its onboarding/hindcasting
  run finishing.

**Scope note (review, minor):** commit `95cb5116` (`tests/conftest.py`
per-checkout `PREFECT_HOME`) is outside this plan's stated scope of "two
hindcast/skill-scoring defects". It is included deliberately, not
incidentally: this round's own test runs against a shared `~/.prefect`
SQLite database were measured hanging (17 GB DB, 23 live Prefect
processes, `/implement` runs dying as "agent stalled" — see the comment in
`tests/conftest.py`), and the fix was needed to get this plan's own gates
to run reliably. Recorded here per the project's task-jag discipline rather
than split into a separate PR, since the branch is already committed and
hold-at-PR.

## Fixer round (review) — compute_skills_task / compute_combined_skills_task partitioning

An independent Codex pass over the diff found a major gap the D4/ALSO-FIX-#3
work above did not close: `compute_skills_task` and `compute_combined_skills_task`
(`flows/compute_skills.py`) fetch a station/model's ENTIRE unpartitioned
hindcast history (no `hindcast_run_id` filter, 1970-2100 bounds) and handed
it straight to `observation_fetch_bounds` / `compute_skill_for_station`,
both of which hard-raise `ConfigurationError` on any mixed `time_step` or
`valid_time` phase within that history (exactly the enforcement ALSO-FIX-#3
added). Unlike `hindcast.py`/`operational_inputs.py`/`track_assembly.py`,
which catch this exact exception and degrade one step/cycle gracefully,
these two flow-level tasks had no try/except and no upstream partitioning —
a single differently configured hindcast run (a retraining, or Plan 226's
planned per-cycle anchoring) would raise uncaught and halt skill scoring
for that station/model on every subsequent run, since the run always
refetches the same mixed history.

**Fix:** added `partition_by_time_step_and_phase` (new,
`services/skill/service.py`) and used it in both tasks to split the fetched
hindcasts into homogeneous `(time_step, phase)` cohorts BEFORE calling
`observation_fetch_bounds`/the compute helpers, computing and storing skill
once per cohort. `compute_combined_skills_task` partitions each model's
hindcasts independently, then only combines cohorts where >= 2 models are
present — the sharper case the review flagged, since it unions across
models before validating. A mismatch now degrades to "fewer cohorts scored
this run" instead of "none, forever, uncaught". Locking tests:
`tests/unit/flows/test_compute_skills.py::TestComputeSkillsTask::test_mixed_time_step_history_degrades_gracefully`
and
`::TestComputeCombinedSkillsTask::test_mixed_time_step_across_models_degrades_gracefully`,
both proven RED against the pre-fix code (uncaught
`ConfigurationError: ... mixed time_step ...`) and GREEN after.

## Review disposition (2026-09-02) — review CUT here, findings folded

The implement loop escalated after 2 rounds with 2 blockers + 3 majors + 1 minor. **The owner cut
the review at this point** rather than run a fourth round: P1 and P2 — the defects this plan exists
to fix — are fixed and locked, the full suite runs green (5063 passed, 0 failed), and the remaining
findings are either narrower than they read or belong elsewhere. Every finding below was verified
against the code before disposition.

| Finding | Severity | Disposition |
|---|---|---|
| FI-declared aggregation bypassed/flattened on every path | blocker | **→ Plan 234.** Broad FI-conformance work, not this plan's subject. **Not an FI issue** — the FI docs already specify the method, the default and that SAP3 owes it |
| Cohort partitioning writes scores whose natural key omits `time_step`/`phase`, so cohorts collide under `ON CONFLICT DO NOTHING` | blocker | **FIX HERE.** This branch introduced the partitioning, so it owns the data-loss path it created. Latent today (one step, one phase after D4), live the moment heterogeneity appears |
| Declared lookback validated but not delivered (`validation_window` trims; `past_targets=obs_df` does not) | major | **→ Plan 234.** Today's models self-slice with `tail(N)`, so P1 is genuinely fixed; the invariant is simply not enforced where the contract places it |
| Hindcasts attributed to the wrong artifact when the run id is omitted | major | **→ Plan 234** |
| Phase validation reads only `vts[0]`, so an internally mixed-phase ensemble passes | major | **FIX HERE.** A hole in code this branch added |
| Decision record contradicts the implemented behaviour (still describes the removed anchor) | minor | **FIX HERE.** The record must not describe a mechanism that was deleted |

### What ships from this plan

P1 and P2 fixed and locked by 8 acceptance tests, each proven red-first by stash-and-restore. D4's
UTC-calendar bucketing with aligned-and-extended bounds. `PREFECT_HOME` scoped per checkout.

**And one find worth more than its plan:** migration 0051. `computation_version` had been silently
dropped from the live skill natural key at migration 0016 while `db/metadata.py` still declared it —
so **D3's recompute would have silently discarded every corrected row** via `ON CONFLICT DO
NOTHING`. Found and fixed here, proven by an integration test.
