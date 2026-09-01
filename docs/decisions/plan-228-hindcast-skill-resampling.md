# Decision Record — hindcast and skill scoring ran on the wrong data (Plan 228)

**Date**: 2026-09-01
**Status**: Fix implemented and merged; **existing scores not yet marked/recomputed**
(gated on the mac-mini's in-flight onboarding/hindcasting test run — see D3 below)
**Owners**: Bea (orchestrator)
**Cross-reference**: `docs/plans/228-hindcast-and-skill-on-wrong-data.md` (source plan),
`docs/touchpoint-maps.md` § Operational inputs / Training-hindcast-skill (routing
detail), Plan 226 (blocked on this plan — anchoring `valid_time` is a separate,
later fix)

## ⚠️ Every skill score and hindcast forecast predating this fix is invalid

**Every row in `skill_scores` created before 2026-09-01 was computed on
mismatched data and does not mean what it says.** Two independent defects (P1,
P2 below) both corrupted the comparison; P2 affects 100% of rows, P1 an
additional subset built on `linear_regression_daily` / `nwp_regression`
hindcasts. Do not trust a pre-fix skill number for model promotion, ranking, or
API display. Per D3 (below), these rows are pending being marked
`superseded` and recomputed — this has **not yet happened** as of this
record; check `skill_scores.freshness` / a future superseded-marker column
before relying on any row's age.

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

- **P1** (`services/hindcast.py::_assemble_hindcast_inputs`): `past_targets` is
  now resampled to the model's declared `time_step` via the SAME
  `resample_to_time_step` (`services/training_data.py`) the operational path
  already uses, before it reaches `StationInputData` — anchored to `issue_time`
  (review fixer round, below) so a non-midnight `issue_time` still gets a full
  `[issue_time - time_step, issue_time)` window, not a partial boundary bucket.
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
  — MEAN for discharge/water_level, SUM for precipitation, etc.), anchored to
  a `valid_time` drawn from the hindcasts (review fixer round, below). The
  `time_step` itself is inferred from the hindcast data
  (`_infer_forecast_time_step`): the lead-to-lead gap inside one hindcast's own
  ensemble, falling back to the issue-to-issue gap only for a horizon-1 model.
  When neither signal is available (a single hindcast with a single lead
  step), `compute_skill_for_station` returns no scores at all
  (`compute_skill_for_station.time_step_unknown_no_scores`) — see the fixer
  round below for why the original instantaneous-join fallback was removed.

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
  reintroduced P2.** When `_infer_forecast_time_step` returns `None`,
  `compute_skill_for_station` used to fall back to the exact pre-fix raw
  instantaneous join. It now returns no scores at all — a missing score is
  preferable to a wrong one — logged as
  `compute_skill_for_station.time_step_unknown_no_scores`. Locked by
  `TestUnknownTimeStepProducesNoScores` (test_service.py).

**Disputed, not implemented**: the review additionally suggested a test
proving `validate_time_step_cadence` *tolerates* a realistically-gappy
lookback window (e.g. one missing daily bucket out of seven), to avoid
"starving stations of forecasts in production." That is the opposite of the
adjacent MAJOR finding above, which requires the validator to *reject* an
isolated missing bucket — tolerating it would silently reintroduce the same
positional-misread bug the strict per-gap check exists to catch. The
strict, reject-on-any-gap behavior was kept; see the fixer's `disputedFindings`
for the full reasoning. Whether a single missing operational reading should
degrade a station gracefully (skip cleanly) rather than raise is a genuine,
separate operational-policy question or the existing per-station
`except Exception` wrapping in `run_forecast_cycle.py` — not one this fixer
round settles.
