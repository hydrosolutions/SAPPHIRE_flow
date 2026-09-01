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

**DRAFT — HIGH PRIORITY.** Awaiting owner READY and two decisions (D1, D2).

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

### D1 — where does the daily-averaging fix go? ⚠️ OPEN

| | Option | Cost | Risk |
|---|---|---|---|
| **A** | **Resample in the hindcast input assembly** — `services/hindcast.py` buckets `obs_df` to the model's `time_step` before building `StationInputData`, exactly as the operational path already does | Smallest: one function, one place | Any *other* caller that assembles model inputs stays free to feed raw rows; the models keep trusting whoever feeds them |
| **B** | **Each model resamples its own lookback** before taking its tail | 2-3 models, via a shared helper | Models become correct regardless of caller; but it changes model internals and must be proven a no-op in the operational path |
| **C** | **Validate at the boundary** — the model declares a `time_step`, and input assembly fails loudly when the delivered cadence does not match it | Largest | Makes silent recurrence impossible anywhere, but needs a single validation point both paths pass through |

**Recommended: A plus the guard from C** — fix the assembly so scores become correct now, and add a
cheap assertion that the delivered lookback actually spans the declared window, so this cannot
silently recur. B is the better long-term shape and is a larger behaviour change; it can follow.

### D2 — how is the scoring comparison fixed? ⚠️ OPEN (owner has said "fix", not which)

| | Option |
|---|---|
| **A** | Resample the observation lookup to **daily means** before the join |
| **B** | Resample to the **forecast's own `time_step`**, whatever it is — the same fix, done once, correct for sub-daily models too |

**Recommended: B.** A is B special-cased to today's only cadence.

### D3 — what happens to the 106 910 existing scores?

They cannot be repaired in place; they must be recomputed after D1 and D2 land. The owner decides
whether to **delete** them, **mark them superseded**, or **recompute and overwrite**. Leaving them
readable and unmarked is the one option this plan rejects: a wrong skill number that looks
authoritative is worse than an absent one.

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
- Retraining. Training uses its own assembly path; whether it shares this defect is a question for
  T1's measurement, not an assumption for this plan.

## Exit gates

- T1's measurement demonstrated failing before the fix and passing after.
- `uv run pytest tests/unit` — zero failures.
- A recomputed skill score for one station, compared against its pre-fix value, with the difference
  recorded. If the difference is negligible the premise is wrong and this plan should stop.
