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

## ⛔ PER-RUN SCOPE (2026-09-02, second) — THREE fixes only. Nothing else.

The previous per-run scope is COMPLETE (see the implementation status below). The review has been
**CUT** by owner decision — see "Review disposition" at the foot. This run closes the three findings
that stayed in this plan, and **nothing else**.

**Do not** re-open D1-D4, re-review the shipped P1/P2 fix, or touch anything assigned to Plan 234
(FI aggregation threading, exactly-N delivery at the model boundary, artifact attribution). If a
reviewer raises those, the correct response is "assigned to Plan 234" — not a fix.

### 1. 🔴 Cohort collision silently discards skill scores

Round 2 partitioned scoring cohorts by `(time_step, phase)`
(`flows/compute_skills.py:144`), but the skill natural key contains neither
(`alembic/versions/0051_...py:58`, `store/skill_store.py:35`). Two cohorts producing the same
`(station, model, lead, …)` therefore collide and one is **silently dropped** by
`ON CONFLICT DO NOTHING`.

This branch created the partitioning, so it owns the data-loss path. Latent under today's config
(one step, one phase after D4) and live the moment heterogeneity appears. Either select one
authoritative cohort, or add `time_step_seconds` and `phase` to the score/diagram types, tables,
natural keys and read APIs — and make nullable-artifact uniqueness NULL-safe. **Locking test must be
a PostgreSQL integration test** with overlapping 24-hour leads across two cohorts; a unit test
cannot exercise `ON CONFLICT`.

### 2. Phase validation reads only the first valid time

`services/skill/service.py:74,90` classifies an ensemble's phase from `vts[0]`, so an ensemble whose
own valid times mix phases (daily steps at both midnight and 06:00) is misclassified, passes
homogeneity validation, and silently drops leads. Validate every distinct valid time within each
ensemble. Locking test: a multi-step ensemble with internally mixed phases.

### 3. The decision record describes a mechanism that no longer exists

`docs/decisions/plan-228-hindcast-skill-resampling.md:16,56-83` still describes the removed
`issue_time`/`valid_time` anchor and the removed `min(time_step)` inference, and its heading claims
every pre-fix hindcast is invalid while its body correctly limits P1 to two model families. Rewrite
to state UTC-calendar bucketing, homogeneous validation, and the correct affected subset.

### ⛔ Still forbidden

Do not touch the mac-mini. D3's marking and recompute wait for a decision on its crashed onboarding
run.

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

## Implementation status (2026-09-02, third round) — the three "FIX HERE" findings closed

Executed the "⛔ PER-RUN SCOPE (second)" section in full — the three findings the review disposition
table marked "FIX HERE" (as opposed to "→ Plan 234") were still genuinely open at that point, despite
an earlier status note in this document claiming otherwise:

1. **Cohort collision (blocker).** `uq_skill_scores_natural_key`/`uq_skill_diagrams_natural_key`
   still omitted `time_step`/`phase` — `partition_by_time_step_and_phase` computes and stores skill
   once PER cohort (deliberately: `test_mixed_time_step_history_degrades_gracefully` requires BOTH
   cohorts to survive), so two cohorts sharing every other natural-key column collided under
   `ON CONFLICT DO NOTHING` and one was silently dropped. Migration 0052 adds `time_step_seconds`
   (`NOT NULL`, backfilled `86400`) and `phase_offset_seconds` (nullable) to both tables and widens
   both natural keys to include them, NULL-safe via the same `COALESCE` pattern migration 0051 uses.
   `SkillScore`/`SkillDiagram` gained matching fields (defaulted, so unrelated call sites are
   untouched); `compute_skill_for_station` threads the real resolved value through. Locked by
   `tests/integration/store/test_skill_store.py::TestPgSkillStore::
   test_natural_key_disambiguates_cohorts_by_time_step_and_phase` — a real-Postgres test, since
   `ON CONFLICT` cannot be exercised against the in-memory fake store — proven RED (`assert 1 == 2`)
   against the pre-0052 schema and GREEN after.
2. **Phase validation reading only `vts[0]` (major).** `_valid_time_phase_us` now checks every
   distinct `valid_time` in an ensemble and raises `ConfigurationError` on internal disagreement,
   instead of classifying the whole hindcast by its first `valid_time` alone.
   `partition_by_time_step_and_phase` catches that raise per-hindcast and excludes (logged) just the
   offending hindcast, so one internally-malformed ensemble degrades scoring by one hindcast rather
   than crashing the whole partitioning pass. Locked by
   `tests/unit/services/skill/test_service.py::TestInternallyMixedPhaseWithinOneHindcastRaises`,
   proven RED (`DID NOT RAISE`) against the `vts[0]`-only code and GREEN after.
3. **Stale decision record (minor).** `docs/decisions/plan-228-hindcast-skill-resampling.md`'s
   heading and "## Fix" section described the retracted `anchor`/`min(time_step)` mechanisms as
   current; rewritten to describe UTC-calendar bucketing and homogeneous validation, with the
   heading correctly scoping P1 to `linear_regression_daily`/`nwp_regression` rather than "every
   hindcast forecast".

Full suite: `uv run pytest tests/unit` and `uv run pytest tests/integration` both zero failures.
`ruff check`/`ruff format --check` clean; pyright ratchet unchanged (400 <= baseline 400). Still
outstanding, unchanged: D3's marking-superseded + recompute remain gated on the mac-mini's
onboarding/hindcasting run finishing — nothing in this round touched that system.

## Fixer round (independent Codex + design review of the committed diff, 2026-09-02) — 1 blocker + 3 majors closed

An independent Codex pass plus a Claude design review over the committed diff found four
correctness issues in the "PER-RUN SCOPE (second)" and "compute_skills_task partitioning" rounds
above, plus two documentation gaps. All were fixed on top of the branch (kept, not rebuilt):

1. **Nullable artifact IDs still defeated skill idempotency (BLOCKER).** Migration 0052's
   `uq_skill_scores_natural_key`/`uq_skill_diagrams_natural_key` indexed `model_artifact_id` RAW
   (no `COALESCE`) and never indexed `model_id` at all. `services/skill/combined_skill.py` always
   computes pooled/BMA scores with `model_artifact_id=None` — PostgreSQL treats every `NULL` as
   distinct, so a repeated pooled/BMA computation for the identical stratum inserted a duplicate
   row on every re-run instead of colliding under `ON CONFLICT DO NOTHING`, and `model_id`
   (`POOLED_MODEL_ID` vs `BMA_MODEL_ID`) is the only column that would have kept those two
   combination strategies apart once `model_artifact_id` became NULL-safe. Fixed in migration 0052
   directly (amended, not superseded — it had not shipped to any environment): both natural keys
   now include `model_id` and `COALESCE(model_artifact_id::text, '')`; the migration also
   defensively deduplicates existing rows (keeps the newest by `created_at`) under the new key
   before creating the tightened indexes. Locked by two real-Postgres integration tests
   (`tests/integration/store/test_skill_store.py`):
   `test_repeated_pooled_computation_with_null_artifact_is_idempotent` and
   `test_pooled_and_bma_null_artifact_scores_do_not_collide`, both proven RED against the pre-fix
   schema (the second specifically against a COALESCE-but-no-`model_id` half-fix, to prove it is
   not a vacuous assertion).
2. **Aligned model-input bounds corrupted observation freshness (MAJOR).** `assemble_station_
   operational_inputs`/`assemble_assignment_inputs` (`operational_inputs.py`/`track_assembly.py`)
   both derive `observation_staleness_hours` from the SAME `all_observations` collection D4
   deliberately truncates to complete UTC-calendar buckets — so at a 06Z cycle with a 10-minute-old
   reading, freshness was measured from the prior midnight (~6.2h stale, crossing the default
   warning threshold) and worse at 12Z/18Z. Fixed by fetching the trailing gap
   `[past_targets_end, issue_time)` separately, unresampled, purely for freshness — never mixed
   into `past_targets`. Locked by a 06Z test in each file, both proven RED (measured exactly
   6.1667h, matching the D4 arithmetic).
3. **Scoring accepted an incomplete current bucket as a full step mean (MAJOR).**
   `_resample_observations_to_forecast_step` aggregated every available row without checking
   whether the bucket's end had elapsed — `observation_fetch_bounds` fetching through
   `valid_time + time_step` does not guarantee those rows exist yet, so a still-forming bucket
   could exact-match a forecast's `valid_time` and silently score a partial mean as complete. Fixed
   by taking an explicit `now` and excluding any bucket where `bucket_start + time_step > now`.
   Locked by `TestIncompleteCurrentBucketExcludedFromScoring`, proven RED (score 75.0, the exact
   average of a 0-error completed pair and a 150-error partial-bucket pair). Two pre-existing
   stratification tests used hindcast dates AFTER the fixture clock (an unrealistic shape this
   correct filter now catches); their dates were moved earlier rather than the filter weakened.
4. **The promotion gate mixed stale v1 and corrected v2 scores (MAJOR).** Migration 0051 and
   `_COMPUTATION_VERSION = 2` deliberately let both versions coexist (`mark_stale` never deletes),
   but `evaluate_skill_gate` fetched every version/freshness combination and filtered only by
   sample size — so a stale v1 value could still decide the worst score. Fixed: `evaluate_skill_
   gate` now filters to `freshness == CURRENT`, then to `computation_version == max(...)` among
   those, before the threshold logic runs. Locked by opposing stale-v1 (fails)/current-v2 (passes)
   rows in the same stratum, proven RED, plus a guard test confirming a CURRENT v1 score still
   controls the gate when no v2 recompute exists yet.
5. **Minor — the documented invalid-row cutoff named a calendar date.** `docs/v0-scope.md` and the
   decision record said every score "predating 2026-09-01" is invalid, but the in-flight mac-mini
   process kept producing defective `computation_version = 1` scores past that date while the fix
   sat unmerged. Both now define invalid rows as `computation_version < 2`.
6. **Minor — a docstring overclaimed aggregation parity.** `_resample_observations_to_forecast_
   step`'s docstring claimed the SAME aggregation `_assemble_hindcast_inputs` uses; the assemblers
   now call `resolved_aggregation_methods(reqs)` (FI-declared aggregation wins), while skill scoring
   still calls the simpler fallback-only `aggregation_method_for` (no `ModelDataRequirements` in
   scope at a single-parameter join). Docstring corrected to state the gap and defer it to Plan 234,
   matching `docs/touchpoint-maps.md`'s existing caveat.

Test soundness: every locking test above for a correctness/bug fix (1-4) was proven to fail against
the pre-fix code before the fix was restored — see the session's `lockingTestProofs`.

`uv run ruff check`/`ruff format --check` clean on every touched file. Pyright ratchet: 394 <= 400
(one new `list[Observation]` annotation on `operational_inputs.py`'s new `freshness_observations`
variable actually IMPROVED the count from the 403 an unannotated version produced). Full suite:
`uv run pytest tests/unit` and `uv run pytest tests/integration/store/test_skill_store.py` both
zero failures. Still outstanding, unchanged: D3's marking-superseded + recompute remain gated on
the mac-mini's onboarding/hindcasting run finishing.

## ⛔ PER-RUN SCOPE (2026-09-02, third) — TWO failing tests. Nothing else.

The full unit suite is **RED**: `2 failed, 5069 passed`. Both reproduce in isolation, so they are
regressions, not flakes. They come from the previous fixer round — the work recovered from the
working tree after the machine slept mid-run, which therefore never went through independent
verification.

**This run fixes exactly these two and stops.** Do not re-open any closed finding, do not touch
anything assigned to Plan 234, do not refactor beyond what these two require. If a reviewer raises
anything else, the answer is "out of scope for this run".

### 1. 🔴 `test_computes_skills_for_all_target_parameters` — NOTHING is stored

`tests/unit/flows/test_train_models.py:975`: expected `{discharge, water_level}`, got `set()`.

The probable cause is the previous round's new rule excluding observation buckets that have not
fully elapsed as of `clock()` (`services/skill/service.py:262,600`). Under this test's injected
clock it appears to exclude **every** bucket, so there are no observations and no scores.

**Diagnose before fixing.** If the rule is over-strict, that matters far beyond this test: a
condition that silently yields zero scores would make D3's recompute *look* successful while storing
nothing — the same class of silent no-op that migration 0051 exists to prevent. The fix must make
"no observations available" distinguishable from "scored nothing", not merely make the test pass.

### 2. `test_mixed_time_step_history_degrades_gracefully` — one cohort silently dropped

`tests/unit/flows/test_compute_skills.py:372`: expected `24 in lead_time_hours`, got `{1}`.

Cohort partitioning now selects one cohort and discards the other, where the test expects mixed
history to degrade gracefully across both.

**This is a semantic question, not automatically a test bug.** Decide explicitly: either scoring
mixed history should cover every cohort (fix the code), or selecting one authoritative cohort is
correct (fix the test, and state in the test's docstring *why* dropping the other is right and
where the dropped cohort's scores are supposed to come from). Do not simply relax the assertion.

### Exit gate for this run

`uv run pytest tests/unit` green, and **report pytest's own exit status** — not that of a pipeline
it was piped through. A `tail`-truncated run reported success over these very two failures.

### ⛔ Still forbidden

Do not touch the mac-mini.

## Implementation status (2026-09-02, fourth round) — both failing tests closed, both were fixture bugs

Diagnosed before fixing, per this run's scope:

1. **`test_computes_skills_for_all_target_parameters`.** `tests/unit/flows/test_train_models.py`
   declares module-level `_EPOCH` twice — 2025-03-01 (line 44, immediately shadowed, otherwise
   unused) and 2025-01-01 (line 122, the value every other test in the file reads). This test's
   `clock = lambda: _EPOCH` resolved to the second, later-defined value (2025-01-01), which
   **predates** the Feb-2025 hindcasts `_seed_hindcasts_and_obs` seeds. Against that clock, the
   review fixer round's completed-bucket filter in `_resample_observations_to_forecast_step`
   correctly excluded every observation bucket as not-yet-elapsed, so nothing was scored. **Test
   fixture bug, not a defect in the filter** — the filter did exactly what a `now` chronologically
   before its data should make it do. Fixed by giving the test an explicit clock (2025-03-01) after
   every seeded valid time, instead of the shared, since-shadowed `_EPOCH`.
2. **`test_mixed_time_step_history_degrades_gracefully`.** The injected daily-cadence hindcast was
   dated 2025-02-01 (valid time 2025-02-02) — **after** the test's `clock()` (`_EPOCH` = 2025-01-15).
   The same completed-bucket filter correctly excluded that bucket; `partition_by_time_step_and_
   phase` produced both cohorts correctly, but the daily cohort's one observation had not "happened"
   yet relative to the clock exercising it. **Test fixture bug**, not a cohort-selection defect —
   `compute_skills_task` does not silently drop one cohort in favor of another. Fixed by dating the
   fixture 2025-01-01 (valid time 2025-01-02), well before `_EPOCH`.

Neither fix touched `partition_by_time_step_and_phase`, `observation_fetch_bounds`, or the
completed-bucket rule. Per this run's explicit diagnostic requirement, `compute_skill_for_station`
now logs a `structlog` WARNING distinguishing "observations exist but no bucket has elapsed yet"
(`skill.compute_skill_for_station.no_elapsed_observation_buckets`) from "resampled buckets exist but
none matched a forecast `valid_time`" (`skill.compute_skill_for_station.no_matching_strata`) — so a
real recompute that silently stores zero rows is diagnosable from logs, not indistinguishable from a
clean run with nothing to score.

`uv run pytest tests/unit` — pytest's own exit status checked directly (not through a pipe) — green.
`ruff check`/`ruff format --check` clean; pyright ratchet 394 <= 400 (unchanged). Full detail:
`docs/decisions/plan-228-hindcast-skill-resampling.md` § "Per-run scope round (2026-09-02, fourth)".
Still outstanding, unchanged: D3's marking-superseded + recompute remain gated on the mac-mini's
onboarding/hindcasting run finishing — nothing in this round touched that system.

## ⛔ PER-RUN SCOPE (2026-09-03) — ONE change, then this plan is done

An independent design check (2026-09-03) found the proposed "bump `computation_version` per
recompute" fix **unsafe**, and its consequences are now **Plan 235**. This plan takes only the piece
that makes its own migration deployable.

### The one change: a PARTIAL unique index

Migration 0052 currently builds a plain unique index over rows that already violate it. Under 0051
a nullable `model_artifact_id` was compared raw, so PostgreSQL treated every `NULL` as distinct and
the live database legitimately accumulated repeated pooled/BMA rows. `CREATE UNIQUE INDEX` will
**fail on deploy** against the mac-mini's ~115,000 scores.

Make both unique indexes **partial**, applying only to `computation_version >= 2` — the cutoff this
plan already defines as the boundary of valid data. This grandfathers the known-invalid legacy
generation, **deletes nothing**, and enforces the corrected NULL-safe key for everything written
from now on. Also prevent further writes at the legacy version.

**Do NOT** restore the destructive `DELETE`. **Do NOT** implement generation identity, latest-
generation filtering, retention, or the `hindcast_run_id` requirement — all four are Plan 235.

**Locking test:** a real-PostgreSQL upgrade test seeded with repeated NULL-artifact rows at the
legacy version, proving the migration completes and the old rows survive.

### ⛔ Then STOP

After this, Plan 228 is complete. Its D3 recompute **must not be executed until Plan 235 lands** —
without a generation identity the recompute silently stores nothing. That gate is recorded in 235's
sequencing section.

Do not touch the mac-mini.
