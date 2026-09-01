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
  already uses, before it reaches `StationInputData`.
- **Enforcement** (`services/training_data.py::validate_time_step_cadence`, new):
  a hard backstop, called after every `past_targets` resample in
  `hindcast.py`, `operational_inputs.py`, AND `track_assembly.py` — raises
  `ConfigurationError` if the delivered cadence ever again drifts from the
  declared `time_step`. Deliberately scoped to `past_targets` only:
  `past_dynamic` legitimately carries a finer, unresampled cadence than the
  model's `time_step` in the operational path (e.g. hourly reanalysis feeding
  a daily model) — a blanket check on it would misfire.
- **P2** (`services/skill/service.py`): observations are now resampled to the
  forecast's own `time_step` before the join
  (`_resample_observations_to_forecast_step`), using the SAME aggregation
  method the model trains against (`training_data.py::aggregation_method_for`
  — MEAN for discharge/water_level, SUM for precipitation, etc.). The
  `time_step` itself is inferred from the hindcast data
  (`_infer_forecast_time_step`): the lead-to-lead gap inside one hindcast's own
  ensemble, falling back to the issue-to-issue gap only for a horizon-1 model.
  When neither signal is available (a single hindcast with a single lead
  step), the old instantaneous lookup is the deliberate fallback, logged as
  `compute_skill_for_station.time_step_unknown`.

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
