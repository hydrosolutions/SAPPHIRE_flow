# Decision Record — hindcast and skill scoring ran on the wrong data (Plan 228)

**Date**: 2026-09-01 (updated 2026-09-02 — see § D4 and "Per-run scope round (third)")
**Status**: Fix implemented and committed on `feat/plan-228-hindcast-skill-resampling`,
**held at PR — not yet merged to `main`**; two independent-review fixer rounds
complete, PLUS a per-run scope correction (§ D4 below) that removed a wrong
`anchor`-based fix from fixer round 1 and replaced it with aligned/extended
fetch bounds, PLUS a third per-run-scope round (bottom of this record) closing
the three findings a subsequent review left open (cohort-collision natural
key, internally-mixed-phase validation, this record's own staleness);
**existing scores not yet marked/recomputed**
(gated on the mac-mini's in-flight onboarding/hindcasting test run — see D3 below)
**Owners**: Bea (orchestrator)
**Cross-reference**: `docs/plans/228-hindcast-and-skill-on-wrong-data.md` (source plan),
`docs/touchpoint-maps.md` § Operational inputs / Training-hindcast-skill (routing
detail), Plan 226 (blocked on this plan — anchoring `valid_time` is a separate,
later fix)

## ⚠️ Every skill score predating this fix is invalid; only two model families' hindcasts are

**Every row in `skill_scores` created before 2026-09-01 was computed on
mismatched data and does not mean what it says.** Two independent defects (P1,
P2 below) both corrupted the comparison; P2 affects 100% of `skill_scores` rows
(every score was computed by comparing a step-mean forecast to a raw
instantaneous observation), P1 an additional subset built on
`linear_regression_daily` / `nwp_regression` hindcasts — `NwpRainfallRunoff`
(weather-only, `_n_lags = 0`), `ClimatologyFallback` (does not read
`past_targets`), and `PersistenceFallback` (a different, smaller error of the
same family — see the plan's "What is wrong" § P1) are NOT P1-affected. Do not
trust a pre-fix skill number for model promotion, ranking, or API display. Per
D3 (below), these rows are pending being marked `superseded` and recomputed —
this has **not yet happened** as of this record; check `skill_scores.freshness`
/ a future superseded-marker column before relying on any row's age.

## The two defects

### P1 — two models hindcast on ~70 minutes of history while declaring seven days

`LinearRegressionDaily._extract_discharge()` and `NwpRegression._initial_lags()`
both take the last `lookback_steps` **rows** of `past_targets`, trusting that a
row IS one `time_step` (the ForecastInterface contract: `PastKnownVariable.lookback`
is a count of steps of the declared `time_step`, and aggregating raw data to
that step is the PROVIDER's job — `input/requirement.py`,
`docs/model_interface.md` in the pinned `forecastinterface` package). The
operational path already aggregates before delivery
(`operational_inputs.py::assemble_station_operational_inputs`,
`track_assembly.py`); the hindcast assembler
(`hindcast.py::_assemble_hindcast_inputs`) did not — it handed the model raw
~10-minute-cadence rows straight through. Measured live: 7 rows spanned ~70
minutes against a declared 10,080-minute (7-day) window — a 144x shortfall.

### P2 — skill scores compared a daily-mean forecast against a single instantaneous reading

`compute_skill_for_station` (`services/skill/service.py::_build_strata`) looked
a forecast's `valid_time` up in a dict keyed by RAW observation timestamps.
Whichever single reading happened to land exactly on `valid_time` decided the
score, regardless of the day's true mean. Measured live (discharge, 14 days,
n=134 station-days): median 6.4%, mean 12.4%, p95 48.6%, max 78.8% error —
present in every skill score regardless of forecast quality.

## Fix

**This section describes the FINAL, currently-implemented mechanism. Two
earlier fixer rounds (below, "Fixer round" / "Fixer round 2") built a
phase-aligned `anchor` mechanism first; § D4 explains why that was wrong and
replaces it with what is described here — UTC-calendar bucketing, never
anchoring to `issue_time` or `valid_time`.**

- **P1** (`services/hindcast.py::_assemble_hindcast_inputs`): `past_targets` is
  now resampled to the model's declared `time_step` via the SAME
  `resample_to_time_step` (`services/training_data.py`) the operational path
  already uses, before it reaches `StationInputData`. The fetch bounds feeding
  that resample are UTC-calendar-ALIGNED-AND-EXTENDED
  (`aligned_lookback_bounds`, § D4) so every model receives exactly
  `lookback_steps` COMPLETE calendar buckets — never a naive window with a
  partial bucket at either end, and never phase-aligned to a forecast's own
  `issue_time`.
- **Enforcement** (`services/training_data.py::validate_time_step_cadence`, new):
  a hard backstop, called after every `past_targets` resample in
  `hindcast.py`, `operational_inputs.py`, AND `track_assembly.py` — raises
  `ConfigurationError` if the delivered cadence ever again drifts from the
  declared `time_step`. Deliberately scoped to `past_targets` only:
  `past_dynamic` legitimately carries a finer, unresampled cadence than the
  model's `time_step` in the operational path (e.g. hourly reanalysis feeding
  a daily model) — a blanket check on it would misfire. **Not** wired into
  `training_data.py`'s own `assemble_station_training_data`, deliberately: a
  real multi-year historical training window legitimately contains gaps a
  hard fail would need to reject rather than train around.
- **P2** (`services/skill/service.py`): observations are now resampled to the
  forecast's own `time_step` before the join
  (`_resample_observations_to_forecast_step`), using the SAME aggregation
  method the model trains against (`training_data.py::aggregation_method_for`
  — MEAN for discharge/water_level, SUM for precipitation, etc.), bucketed
  onto the same UTC-calendar grid every assembly path now uses — never
  anchored to a `valid_time` drawn from the hindcasts. A forecast whose
  `valid_time` does not itself fall on a calendar boundary (a non-midnight
  daily cycle) legitimately produces NO score until `valid_time` itself is
  fixed (Plan 226's territory), rather than this function shifting the grid
  to meet it. The `(time_step, phase)` a skill computation covers is
  validated homogeneous across every hindcast — and, per-hindcast, across
  every one of that hindcast's OWN `valid_time`s — by
  `validate_homogeneous_time_step_and_phase`, which RAISES on any mismatch
  rather than silently coercing to `min(time_step)` plus one arbitrarily
  chosen phase (the earlier `_infer_forecast_time_step` behavior this
  replaced). The resolved `(time_step, phase)` is persisted on every
  `SkillScore`/`SkillDiagram` (`time_step_seconds`/`phase_offset_seconds`,
  migration 0052) and is part of both tables' natural keys, so two distinct
  cohorts that would otherwise collide on every other natural-key column
  never collide under `ON CONFLICT DO NOTHING`.

No model math, coefficients, or artifacts changed. Training was investigated
and found clean: `build_station_training_data` already resamples
(`services/training_data.py:285-287`) — the defect was confined to the
hindcast/skill read path.

## D3 — what happens to the 106,910 pre-fix scores

**Decision (owner, 2026-09-01): mark them superseded, not delete, once the
mac-mini's in-flight onboarding/hindcasting test run finishes.** That run is a
deliberate pipeline exercise; its *numbers* are already known-invalid (this
fix postdates it) but its *existence* should stay auditable. Sequence: let the
mini's run finish → mark every pre-fix score superseded → recompute
everywhere. **As of this record, the mini run has not been confirmed finished
and the marking/recompute has NOT been executed** — this is an explicit
follow-on, not part of this fix's PR.

## Verified effect (test-harness demonstration, not a live recompute)

Both fixes were locked with a red-first test proven to fail against the
pre-fix code (stash-and-restore proof, not just written-then-trusted):

- P1: `tests/unit/services/test_hindcast.py::TestHindcastResamplesPastTargetsToDeclaredTimeStep`
  — pre-fix, `tail(7)` of `past_targets` spanned **1:00:00** against a declared
  7-day window; post-fix it spans the full window.
- P2: `tests/unit/services/skill/test_service.py::TestDailyMeanComparedAgainstDailyMeanObservation`
  — pre-fix, MAE comparing a daily-mean forecast to the resampled daily
  observation was **40.0** (the deliberately offset boundary reading);
  post-fix it is <1.0 (the forecast IS the true daily mean).

This is the "recomputed skill score for one station, compared against its
pre-fix value" the plan's exit gate asks for — done via a controlled synthetic
scenario, not against the live mac-mini (D3 explicitly forbids touching that
system until its in-flight run finishes).

## Fixer round (multi-model review response, 2026-09-01)

An independent Codex pass plus a Claude design review over the committed
diff raised six findings (1 blocker, 3 major, 2 minor). All six are
resolved:

- **BLOCKER — recompute was silently a no-op, and not only for Plan 228.**
  `store_skill_scores`/`store_skill_diagrams` insert with `ON CONFLICT DO
  NOTHING`, so a corrected recompute must land on a natural key the old
  (now-stale) row does not already occupy. Two things had to be true for
  that, and only one was: `_COMPUTATION_VERSION` (`services/skill/service.py`)
  was still `1`; bumping it to `2` was necessary but, on its own, not
  sufficient — **the LIVE `uq_skill_scores_natural_key`/
  `uq_skill_diagrams_natural_key` indexes have not actually included
  `computation_version` since migration 0016** ("Add parameter column...",
  2026-03-27), which dropped and recreated both indexes to add `parameter`
  and silently dropped `computation_version` in the process.
  `db/metadata.py`'s Python `sa.Index` objects were never updated to match
  and have been WRONG about the live schema ever since — a pure
  metadata/migration drift, unrelated to Plan 228, that made ANY versioned
  recompute of an existing stratum a silent no-op since 0016, not merely the
  one this plan needed. Migration 0051 restores `computation_version` to
  both live indexes (matching what `db/metadata.py` already, if incorrectly,
  declared); `_COMPUTATION_VERSION` is now `2`. Locked by
  `tests/integration/store/test_skill_store.py::TestPgSkillStore::test_recompute_after_mark_stale_supersedes_the_corrupted_score`
  — proven RED (stash 0051 + the version bump, keep the test): the corrected
  row collides with the stale row and `fetch_latest_scores` returns the
  stale 40.0, not the corrected near-zero score.
- **MAJOR — aggregation windows were not phase-aligned to the forecast grid.**
  `resample_to_time_step` (`services/training_data.py`) truncates to
  UTC-midnight boundaries regardless of what instant a caller actually cares
  about. For `services/skill/service.py`, this meant a non-midnight
  `valid_time` never exact-matched any observation bucket key at all —
  `_build_strata` silently found NO observation for ANY forecast, not merely
  a wrong one (every earlier test in this module happened to use
  midnight-aligned `hindcast_step`s, so this never showed up). For
  `hindcast.py`, it meant a non-midnight `issue_time` got a spurious PARTIAL
  bucket at the boundary nearest `issue_time` instead of a full
  `[issue_time - time_step, issue_time)` window. `resample_to_time_step`
  gained an `anchor` parameter (a fixed phase-shift applied before grouping,
  removed after) — hindcast anchors to `issue_time`; skill anchors to a
  `valid_time` drawn from the hindcasts themselves
  (`_forecast_valid_time_anchor`). Locked by
  `TestObservationBucketsAlignToForecastValidTimePhase` (test_service.py) and
  `TestHindcastPastTargetsBucketsAlignToIssueTime` (test_hindcast.py), both
  using a 06:00 UTC issue/valid time — both proven RED against the
  pre-anchor code.
- **MAJOR — `hindcast_forecasts` never persisted the ensemble's own
  `time_step`.** It was reconstructed on read by inferring from the gap
  between stored `valid_time`s, defaulting to a hardcoded `timedelta(hours=1)`
  whenever a hindcast has a single lead step — silently wrong for a
  horizon-1 DAILY model. Migration 0050 adds `time_step_seconds`
  (`hindcast_forecasts`, `NOT NULL`, backfilled `86400`); `store_hindcast`
  writes it from `ensemble.time_step`; `_reconstruct_ensemble`
  (`store/hindcast_store.py`) reads it directly instead of inferring. Locked
  by `TestStoreHindcastPersistsAuthoritativeTimeStep` (integration), proven
  RED against the pre-migration code (round-trips as 1 hour, not 1 day).
- **MAJOR — the cadence validator only checked the median gap.** An isolated
  missing bucket (e.g. days 1,2,3,5,6 — one 2-day gap among three 1-day gaps)
  left the median unaffected and passed silently — exactly the shape a model
  reading `.tail(n)`/`values[-n:]` positionally misreads as consecutive
  steps. `validate_time_step_cadence` now checks **every** adjacent gap.
  Direct unit tests were also added (`TestValidateTimeStepCadence`,
  `test_training_data.py`) — there had been none at all, despite the
  function gating the live operational forecast cycle. One test's isolated-
  missing-bucket case is proven RED against the pre-fix (median-only) check.
- **MINOR — the docs overclaimed which rows are invalid, and which
  assembler is covered by the cadence backstop.** `v0-scope.md` and
  `touchpoint-maps.md` now say every `skill_scores` row (P2, universal) is
  invalid, while only the `hindcast_forecasts` rows from
  `linear_regression_daily`/`nwp_regression` (the P1-affected models) are —
  not every `hindcast_forecasts` row. `touchpoint-maps.md` now names the
  three assemblers `validate_time_step_cadence` actually covers
  (`hindcast.py`, `operational_inputs.py`, `track_assembly.py`) and states
  explicitly that `training_data.py` resamples but does not additionally
  validate, and why (real historical gaps are expected, not a bug to reject).
- **MINOR — the single-hindcast/single-lead-step fallback silently
  reintroduced P2.** When `_infer_forecast_time_step` returned `None`,
  `compute_skill_for_station` used to fall back to the exact pre-fix raw
  instantaneous join. **Superseded by the round-2 fixer notes below**: rather
  than keep the gap-inference/`None`-fallback shape and patch its exit, the
  function was replaced outright with a direct read of
  `ForecastEnsemble.time_step` (see the P2 fix bullet above), which cannot
  fail to resolve once `hindcasts` is non-empty — there is no `None` branch,
  no fallback join, and no `time_step_unknown_no_scores` log event left to
  hit. `TestUnknownTimeStepProducesNoScores` was removed along with the
  branch it locked (obsolete, not silently deleted: see round 2 below).

**Disputed, not implemented** (round 1): the review additionally suggested a
test proving `validate_time_step_cadence` *tolerates* a realistically-gappy
lookback window (e.g. one missing daily bucket out of seven), to avoid
"starving stations of forecasts in production." That is the opposite of the
adjacent MAJOR finding above, which requires the validator to *reject* an
isolated missing bucket — tolerating it would silently reintroduce the same
positional-misread bug the strict per-gap check exists to catch. The strict,
reject-on-any-gap behavior was kept. **Round 1 left open** whether a single
missing operational reading should degrade a station gracefully (skip
cleanly) rather than raise; **round 2 (below) closes that question**: it does
already degrade gracefully, at all three call sites, as of the same commit
that introduced this paragraph — the paragraph above was simply never updated
to say so.

## Fixer round 2 (multi-model review response, 2026-09-01, second pass)

A second independent Codex pass plus a Claude design review ran over commit
`d5d188d0` ("recovered fixer-round work from a stalled agent") and raised two
majors. Both findings targeted an intermediate state of the code
(`8f87eb68`) that `d5d188d0` had already superseded by the time the review
ran; neither required a further code change. Both are recorded in the
fixer's `disputedFindings` with the diff evidence, and are summarized here so
this record does not go stale a second time:

- **"`_infer_forecast_time_step` re-derives via gap-inference and can return
  `None`, dropping scores for a freshly-onboarded station/model."** This
  described `8f87eb68`. `d5d188d0` had already replaced the function with
  `min(hc.ensemble.time_step for hc in hindcasts)` — reading the mandatory,
  authoritative field every model sets at construction and migration 0050
  persists losslessly — and removed the `None` branch, its log event, and
  `TestUnknownTimeStepProducesNoScores` entirely. Verified: `grep -n
  "_infer_forecast_time_step"` in `services/skill/service.py` at HEAD shows
  only the `min(...)` implementation; the test class no longer exists in
  `tests/unit/services/skill/test_service.py`.
- **"A cadence mismatch in `past_targets` raises `ConfigurationError` into
  the operational path's generic `except Exception`, counting the station as
  FAILED — a new, unmeasured failure mode on a path the plan's non-goals say
  must stay unchanged."** This described `8f87eb68`, where
  `operational_inputs.py`, `track_assembly.py`, and `hindcast.py` all called
  `validate_time_step_cadence` unguarded. `d5d188d0` wrapped all three call
  sites in `try/except ConfigurationError`, degrading to the SAME
  insufficient-data handling already used for `no_observations`/`no_nwp`
  (`operational_inputs.py` and `track_assembly.py` return a clean sentinel —
  `None` / `UnavailableTrackContext` — that the caller's non-failure branch
  consumes; `hindcast.py` returns `None`, recorded as one
  `HindcastStepResult(success=False, error="insufficient data")`, the same
  per-step outcome an isolated forcing/observation gap already produces).
  Confirmed by reading `run_forecast_cycle.py`'s caller: the
  `inputs_result is None` branch (`forecast_cycle.station_skipped_no_nwp`)
  does **not** increment `stations_failed` — only the `except Exception`
  branch around the *call itself* does, and a caught-and-returned `None`
  never reaches it. All three graceful-degradation paths already carry
  locking tests (`TestOperationalInputsCadenceMismatchSkipsGracefully`,
  `TestHindcastCadenceMismatchSkipsStepGracefully`, and the
  `track_assembly.cadence_mismatch_skip` case in `test_track_assembly.py`),
  added in the same `d5d188d0` commit.

**What genuinely remains open (escalated, not resolved by this fixer round):**
the strict per-gap check is still a **new** condition on the operational and
hindcast paths — before Plan 228, neither path validated cadence at all,
so an isolated missing bucket simply flowed through positionally-shifted
rather than triggering a skip. The skip is clean (no crash, no station
double-counted as failed), but its real-world incidence is **unmeasured**:
the only fixture data available in this dev environment
(`tests/fixtures/reference/bafu_observations.parquet`) is a fully complete,
zero-gap 2-year synthetic series and cannot stand in for real BAFU/
SwissMetNet sensor-and-comms gap statistics (a real, non-hypothetical
condition — the mac-mini has already logged BAFU-obs DEGRADED alerts caused
by LINDAS rate-limiting, a live source of real observation gaps, not a
hypothetical one). **Recommendation for whoever merges this PR**: watch
`operational_inputs.cadence_mismatch_skip`,
`hindcast.skip.cadence_mismatch`, and `track_assembly.cadence_mismatch_skip`
on the mac-mini for the first several cycles after this deploys, and confirm
with the owner that a clean per-cycle skip (rather than tolerating an
isolated gap) is the intended behavior for the live forecast path — the
graceful-degradation mechanics are in place either way, so reversing the
*policy* later (tolerate one gap, e.g. by relaxing to "reject only when a gap
exceeds N × time_step") would not require re-touching the call sites, only
`validate_time_step_cadence` itself.

## D4 — the authoritative grid, and the per-run scope correction (2026-09-02)

**Fixer round 2's `anchor` mechanism was itself wrong and has been removed.**
It phase-aligned `resample_to_time_step`'s buckets to a caller-supplied
instant (`issue_time` in hindcast, a `valid_time` drawn from the hindcasts in
skill scoring). That made hindcast consume rolling `[issue_time - N *
time_step, issue_time)` windows while training consumes UTC calendar days —
a NEW quantity mismatch replacing the one this plan exists to fix — and it
anchored skill scoring's observation buckets to `valid_time`, which is
precisely the value **Plan 226** exists to correct (anchoring a daily model's
`valid_time` to the calendar is out of THIS plan's scope by design).

**D4 replaces it**: every assembly path aggregates onto UTC-calendar buckets
(whole multiples of `time_step` since the Unix epoch) — `resample_to_
time_step` no longer takes an `anchor` parameter at all, full stop. What
changed instead is the FETCH BOUNDS feeding it: `aligned_lookback_bounds`
(`services/training_data.py`, new) computes `[start, end)` such that `end` is
the UTC-calendar `time_step` boundary at or before the given instant (never
after — this is what preserves NO-FUTURE-LEAKAGE) and `start` is exactly
`lookback_steps` buckets earlier. This is not equivalent to "fetch the naive
window, then drop the incomplete trailing bucket" — an independent review
falsified that simpler rule (2026-09-02): the naive window has a partial
bucket at BOTH ends whenever the instant isn't itself a boundary, so dropping
only the trailing one leaves a corrupt LEADING day, and dropping both starves
an N-day model to N-1 days. `aligned_lookback_bounds` avoids both failure
modes by aligning `end` first and deriving `start` from that aligned `end`.

Applied to every `past_targets` fetch: `hindcast.py::_assemble_hindcast_
inputs` (+ the runner-level prefetch bounds in `run_station_hindcast`/
`run_group_hindcast`), `operational_inputs.py::assemble_station_operational_
inputs`, and `track_assembly.py::assemble_assignment_inputs`. Locked by
`TestHindcastPastTargetsAreCompleteUtcCalendarBuckets` (test_hindcast.py),
`TestOperationalInputsPastTargetsAreCompleteUtcCalendarBuckets`
(test_operational_inputs.py), and
`test_partial_trailing_day_excluded_at_a_non_midnight_cycle`
(test_track_assembly.py) — each seeds a distinct sentinel value into the
partial trailing window at a non-midnight cycle/issue_time and asserts it
never reaches `past_targets`. This also resolves the item this plan's
Non-goals section had RETRACTED (2026-09-02) as a false claim: the
operational path's `past_targets` resample did not previously align or
extend its fetch bounds, so a 06/12/18Z cycle presented a partial day
(measured: 37 rows where a full day is 144) as if it were complete.

Skill scoring's observation resample (`_resample_observations_to_forecast_
step`) also lost its `anchor` parameter — it buckets onto the same
UTC-calendar grid unconditionally. A forecast whose `valid_time` is not
itself calendar-aligned (a non-midnight daily cycle, Plan 226's territory)
will legitimately produce NO score until `valid_time` itself is fixed, rather
than this function silently shifting the grid to paper over the mismatch.
`TestObservationBucketsAlignToForecastValidTimePhase` (which locked the
retracted anchor behavior) was removed along with the mechanism it tested.

### ALSO FIX — three findings D4 does not cover, folded into the same round

1. **Hindcast validation examined the whole delivered frame, not what the
   model consumes.** Hindcast runners default to `lookback_steps=720` (a
   generous prefetch buffer), but `_assemble_hindcast_inputs` validated
   cadence over that entire 720-bucket window even though a model reads only
   its own declared `lookback_steps` (e.g. 7) via `.tail(n)`. A single stale
   bucket anywhere in the other 713 buckets suppressed the step regardless.
   `_assemble_hindcast_inputs` now accepts `declared_lookback_steps` (the
   model's `data_requirements.lookback_steps`, threaded from both
   `run_station_hindcast` and `run_group_hindcast`) and validates only
   `obs_df.tail(declared_lookback_steps)`. Locked by
   `TestHindcastValidatesOnlyTheDeclaredLookbackWindow`
   (test_hindcast.py) — a gap 20 days back with a 30-day fetch window and a
   7-day declared lookback no longer suppresses the step.
2. **`compute_skills_task` derived observation-fetch bounds from
   `hindcast_step` (the issue time), not `valid_time`.** For a SINGLE
   hindcast, `min(hindcast_step) == max(hindcast_step)` — an empty
   `[start, end)` range that fetched no observations at all, silently
   producing zero scores for exactly the freshly-onboarded-station case this
   plan's own P2 fix was supposed to repair. `observation_fetch_bounds`
   (`services/skill/service.py`, new) derives `[min(valid_time),
   max(valid_time) + time_step)` from the ensembles themselves instead, and
   is now used by `compute_skills_task`, `compute_combined_skills_task`
   (flattened across every combined model), and onboarding's
   `_make_skill_fn`/`_compute_skill` callback (`services/onboarding.py`,
   which previously used the training period's own bounds — narrower than a
   multi-step horizon's trailing `valid_time` in the same way). Locked by
   `TestComputeSkillsTask::test_single_hindcast_still_fetches_observations_
   and_scores` (test_compute_skills.py).
3. **Mixed `time_step`/phase across hindcasts was silently coerced.**
   `_infer_forecast_time_step`'s `min(hc.ensemble.time_step for hc in
   hindcasts)` picked the smallest step across a MIXED set with no signal
   that anything was wrong, and nothing checked that every hindcast's
   `valid_time` shared the same phase within that step. Replaced with
   `validate_homogeneous_time_step_and_phase` (`services/skill/service.py`)
   — raises `ConfigurationError` on either mismatch; a caller with
   genuinely mixed models/cycles must partition upstream. Locked by
   `TestMixedTimeStepOrPhaseRaises` (test_service.py, two tests: mixed
   `time_step`, and mixed phase within one `time_step`).

### T5 — training's default `period_end` snapped to a partial bucket

`train_models_flow`'s `period_end` already accepted an explicit override; the
DEFAULT (used only when the caller omits it) fell back to a raw `clock()`
instant while `period_start`'s default fell back to an aligned midnight — the
same partial-trailing-bucket defect D4 forbids everywhere else, just in the
training window rather than a lookback. Fixed by snapping the default to
`floor_to_time_step(clock(), time_step)`. Does NOT invalidate existing
artifacts (a partial final row in a multi-year training set is a minor,
one-row edge — not the systematic corruption P1 was). Locked by
`TestTrainModelsDefaultPeriodEnd` (test_train_models.py), which spies on
`determine_training_scope`'s `period_end` kwarg with a 06:30 UTC clock and
asserts it resolves to that day's midnight.

### Status update

All of the above landed in the SAME per-run scope round that removed the
`anchor` mechanism (2026-09-02). `uv run pytest tests/unit` — zero failures
(see the implementer's own report for the exact count). Still outstanding,
unchanged from before this round: **D3's marking of pre-fix scores as
superseded, and the live recompute, remain gated on the mac-mini's onboarding
run finishing** — nothing in this round touched that system.

### Fixer round (independent Codex review, 2026-09-02) — ALSO-FIX-#3's own callers didn't partition

ALSO FIX #3 above says "a caller with genuinely mixed models/cycles must
partition upstream", but `compute_skills_task` and `compute_combined_skills_
task` (`flows/compute_skills.py`) — the ONLY two production callers of
`observation_fetch_bounds` at the top of the flow — never did that
partitioning themselves. Both fetch a station/model's entire unpartitioned
hindcast history (no `hindcast_run_id` filter, 1970-2100 bounds) and handed
it straight to `observation_fetch_bounds` / `compute_skill_for_station`,
either of which now hard-raises `ConfigurationError` on the very mismatch
this section introduced enforcement for. Unlike `hindcast.py`/
`operational_inputs.py`/`track_assembly.py`, which catch this exact
exception and degrade one step/cycle gracefully, these two flow-level tasks
had no try/except and no upstream partitioning — a single differently
configured hindcast run (a retraining, or Plan 226's planned per-cycle
anchoring, which this plan blocks) would raise uncaught on every subsequent
run and permanently halt skill scoring for that station/model.

**Fix:** `partition_by_time_step_and_phase` (new, `services/skill/
service.py`) splits a hindcast list into its homogeneous `(time_step,
phase)` cohorts. Both tasks now partition BEFORE calling
`observation_fetch_bounds`/the compute helpers and compute+store skill once
per cohort. `compute_combined_skills_task` partitions each model's
hindcasts independently, then only combines cohorts where >= 2 models are
present — the sharper case, since it used to union across every combined
model before validating. A mismatch now degrades to "fewer cohorts scored
this run" rather than "none, forever, uncaught". Locked by
`TestComputeSkillsTask::test_mixed_time_step_history_degrades_gracefully`
and `TestComputeCombinedSkillsTask::test_mixed_time_step_across_models_
degrades_gracefully` (test_compute_skills.py), both proven RED against the
pre-fix code (uncaught `ConfigurationError: ... mixed time_step ...`) and
GREEN after.

## Per-run scope round (2026-09-02, third) — the three findings the review cut left open

An independent review of the branch escalated after two rounds (2 blockers, 3
majors, 1 minor); the owner cut the review rather than run a fourth round,
folding the findings into three dispositions: three items assigned to Plan
234 (broader FI-conformance work), and three that stayed in this plan because
this branch created the defect. This round closes those three.

1. **Cohort collision silently discarded skill scores (blocker).**
   `partition_by_time_step_and_phase` (the fixer round immediately above)
   computes and stores skill once PER `(time_step, phase)` cohort, on
   purpose — `test_mixed_time_step_history_degrades_gracefully` requires
   BOTH cohorts' scores to survive. But `uq_skill_scores_natural_key`/
   `uq_skill_diagrams_natural_key` (migration 0051) contained neither
   `time_step` nor `phase`. Two cohorts producing a score identical on
   every OTHER natural-key column (e.g. a daily cohort's 24h lead and an
   hourly cohort's 24h lead) collided under `ON CONFLICT DO NOTHING`, and
   one was silently dropped — latent under today's single-cadence config,
   live the moment heterogeneity appears. **Fix:** migration 0052 adds
   `time_step_seconds` (`NOT NULL`, backfilled to 86400) and
   `phase_offset_seconds` (nullable) to both tables and widens both natural
   keys to include them, NULL-safe via the same `COALESCE` pattern
   migration 0051 already uses for `season`/`flow_regime`/`forcing_type`.
   `SkillScore`/`SkillDiagram` (`types/skill.py`) gained the same two
   fields (defaulted to `86400`/`None` so unrelated call sites need no
   changes); `compute_skill_for_station` now threads the REAL
   `(time_step, phase)` `validate_homogeneous_time_step_and_phase` already
   resolves down into every constructed score/diagram. Locked by
   `tests/integration/store/test_skill_store.py::TestPgSkillStore::
   test_natural_key_disambiguates_cohorts_by_time_step_and_phase` — a real
   Postgres test, since `ON CONFLICT` cannot be exercised against an
   in-memory fake store — proven RED against the pre-0052 schema
   (`assert 1 == 2`, the second cohort's row silently missing) and GREEN
   after.
2. **Phase validation read only the first `valid_time` (major).**
   `_valid_time_phase_us` classified an ENTIRE hindcast's phase from
   `vts[0]` alone. A single multi-step hindcast whose own `valid_time`s
   mix phase (e.g. daily leads at both midnight and 06:00 within one
   ensemble) was silently misclassified by whichever phase happened to be
   first, passed `validate_homogeneous_time_step_and_phase`'s homogeneity
   check (which only ever saw the one phase value that function returned
   per hindcast), and then had its off-phase leads silently vanish
   downstream — they never land on a UTC-calendar bucket, so
   `_resample_observations_to_forecast_step`'s exact-key lookup never
   matches them. **Fix:** `_valid_time_phase_us` now computes the phase of
   EVERY distinct `valid_time` in the ensemble and raises
   `ConfigurationError` on internal disagreement, rather than reading only
   the first. `partition_by_time_step_and_phase` catches that raise
   per-hindcast and excludes (with a `structlog` warning) just the
   offending hindcast, so one internally-malformed ensemble degrades
   scoring by one hindcast rather than taking down the whole partitioning
   pass — the same graceful-degradation principle as the fixer round
   above. Locked by `tests/unit/services/skill/test_service.py::
   TestInternallyMixedPhaseWithinOneHindcastRaises`, proven RED (`DID NOT
   RAISE`) against the `vts[0]`-only code and GREEN after.
3. **This decision record described a mechanism that no longer exists
   (minor).** The "## Fix" section above previously described `past_targets`
   and observation resampling as anchored to `issue_time`/`valid_time` — the
   fixer-round-1/2 mechanism § D4 explains was wrong and removed. It now
   describes the actual, final UTC-calendar-bucketing behavior and links to
   § D4 for the rationale, and the "every hindcast forecast" heading above
   now correctly scopes P1 to the two affected model families rather than
   implying every hindcast is invalid.
