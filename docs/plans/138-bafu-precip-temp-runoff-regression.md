---
status: DRAFT
created: 2026-07-22
plan: 138
title: BAFU precip+temp+past-runoff regression — add past-temperature, unshadow, retrain
scope: Extend the existing seasonal_precip_runoff_regression to consume PAST temperature (it already has past precip, future precip+temp, and 7 autoregressive discharge lags), diagnose+fix why it emits zero forecasts, retrain on the 2 BAFU stations, and deploy to the mac-mini. Swiss.
depends_on: []
blocks: []
supersedes: []
---

# Plan 138 — BAFU precip + temp + past-runoff regression (extend + unshadow)

## Status

**DRAFT.** Grounded in a 3-agent investigation (2026-07-22) of the model framework, feature delivery,
and live mac-mini state. Owner chose "extend + unshadow the existing model" over a new sibling model.
For a `/plan` adversarial round before READY.

## Context — the model is ~90% built already

The user wants a regression consuming **past + future precipitation**, **past + future temperature**, and
**past runoff** on the BAFU data. The merged **`SeasonalPrecipRunoffRegression`**
(`src/sapphire_flow/models/nwp_regression.py:628`, entry point `seasonal_precip_runoff_regression`, PR #119)
**already delivers most of that** — verified live on the mac-mini:

- **past precipitation** — `_extra_past_known` declares `past_known reanalysis/precipitation` with a 45-day
  lookback, routed to the `past_dynamic` frame → `fetch_reanalysis` (RhiresD/RprelimD hybrid);
  `_antecedent_precip_sums` (`nwp_regression.py:610`) turns it into an antecedent-precip feature.
- **future precipitation + future temperature** — base `_NwpRegressionBase` declares
  `future_known nwp/{precipitation,temperature}` (`nwp_regression.py:218-233`), served from ICON-CH2-EPS at
  inference and observed-era5/meteoswiss at training (the leakage-safe convention).
- **past runoff** — `past_known obs/discharge` with `lookback=7`, delivered from the `past_targets` frame
  (observations) and used as 7 autoregressive lags (`nwp_regression.py:271`, `_initial_lags` `:470`).
- **season** — day-of-year sin/cos (`_season_features`, `:561`).
- **Trained + assigned on both BAFU stations** (2009 Porte_du_Scex, 2091 Rheinfelden-Messstation): active
  artifacts trained 2026-07-21, assigned at priority 12.

**Two real gaps:**

1. **No PAST temperature feature.** No model anywhere declares `past_known reanalysis/temperature` (verified
   by grep across `src/sapphire_flow/models/`). This is the one genuinely-missing channel the user asked for.
2. **It emits ZERO forecasts.** Live: `seasonal_precip_runoff_regression` has produced **0 forecasts**. It
   sits at priority 12 in the first-success PRIMARY chain behind `nwp_regression`@10 — but on the 2026-07-22
   00:00 cycle the chain fell through past both of them to `linear_regression_daily`@30, so seasonal did
   **not** merely get shadowed that cycle — it appears to have **failed or not emitted** even when
   `nwp_regression` failed. Root cause is **unconfirmed** and is Task T1.

This plan closes both: add past-temperature, make it actually run, retrain, deploy.

## Objective

A BAFU discharge regression that consumes past+future precipitation, past+future temperature, and past
runoff, **producing real forecasts** on both operational BAFU stations on the mac-mini.

## Non-goals

- **Not** a new model class — extend `SeasonalPrecipRunoffRegression` in place (owner decision).
- **Not** removing the season/antecedent terms (they help; the user's "past+future precip+temp + past
  runoff" is a superset the extended model satisfies).
- **Not** any snow/SWE work (that is Plan 139, Nepal).
- **Not** changing the FI contract, the forecast-cycle engine, or the other models.

## Design decisions (locked before `/plan`)

### DC-1 — past-temperature as an antecedent MEAN, mirroring antecedent precip

Add `past_known reanalysis/temperature` (lookback = a configurable window, default **14 days** — the
melt/accumulation-relevant horizon, shorter than the 45-day precip window) and derive a single **antecedent
mean-temperature** feature per target time, mirroring `_antecedent_precip_sums`. Temperature is a **state**
(not a flux) → **MEAN** aggregation, never SUM. The feature vector becomes
`[precip, temp, antecedent_precip, antecedent_temp, season_sin, season_cos, *discharge_lags]` — extended
identically in `train`/`predict` (the base builds both from the same order, `nwp_regression.py:270-272,
431-432`). *(Alternative considered: N discrete temperature lags. Rejected as higher-dimensional for no
clear gain over the antecedent mean, and asymmetric with the precip treatment.)*

### DC-2 — reanalysis temperature is already delivered for the Swiss stations

`fetch_reanalysis(parameters=[…, "temperature"])` already serves meteoswiss `tabsd`/reanalysis temperature —
`historical_forcing` holds temperature for both BAFU stations 1981→2026 (verified live). So the new
`past_known reanalysis/temperature` channel needs **no new adapter/ingestion work**; declaring it is enough
(the adapter routes an unmapped-name past_known var to `past_dynamic` → `fetch_reanalysis`,
`forecast_interface.py:1007-1026`). Confirm the reanalysis fetch key list includes `temperature` for the
model's `past_dynamic_features`.

### DC-3 — the artifact schema changes ⇒ full retrain via onboard-model

Adding a feature changes the coefficient vector, so **existing artifacts are invalid**. Retrain through the
**`onboard-model`** flow (not bare `train-models`): it re-runs the compatibility check
(`services/model_onboarding.py:130` verifies the new `past_dynamic_features` incl. `temperature` are
available), smoke test, train, hindcast, skill gate, promote, and (re)assignment — the correct path when a
model's requirements change.

## Tasks

### T1 — diagnose why `seasonal_precip_runoff_regression` emits 0 forecasts (BLOCKER — do first)

- **Scope:** on the mac-mini, read the `forecast-cycle` run logs + the model's `ModelFailure` cause for
  recent cycles (query `forecasts`/failure records and the worker logs for `seasonal_precip_runoff_regression`).
  Determine which of: **(a) pure priority shadowing** (nwp_regression@10 always succeeds, so seasonal never
  runs), or **(b) an actual failure** (a `_ShortForcingWindowError` → `ModelFailure` from the 45-day
  antecedent-window validation `_validate_continuous_window` `:568`, or a NaN-gate rejection, or missing
  reanalysis coverage). The 00:00 fall-through to linear@30 strongly implies (b) for that cycle.
- **Files:** read-only diagnosis (mac-mini logs + DB); findings recorded in this plan.
- **Verification:** the root cause is stated with evidence (log line / failure cause) before T3 designs the
  fix.

### T2 — add past-temperature to `SeasonalPrecipRunoffRegression` (DC-1)

- **Scope:** extend the model: `_extra_past_known` adds `past_known reanalysis/temperature`
  (`PastKnownVariable(lookback=_TEMP_LOOKBACK_DAYS, unit=Unit.DEG_C, max_nan=0)`); add
  `_antecedent_temp_means` (mirror `_antecedent_precip_sums` with MEAN); extend `_extra_train_features` +
  `_extra_predict_features` to emit the antecedent-temp column in the locked order (DC-1); extend
  `_train_warmup_steps` to `max(n_lags, precip_window, temp_window)`; extend `_validate_continuous_window`
  to also gate the temperature window (short/stale ⇒ `_ShortForcingWindowError` ⇒ `ModelFailure`, never
  raise — CLAUDE.md FI rule).
- **Files:** `src/sapphire_flow/models/nwp_regression.py`; `tests/unit/models/test_nwp_regression.py` (new
  tests, red-first): the requirement now declares `past_known reanalysis/temperature`; train builds the
  antecedent-temp column; predict emits it and returns `ModelFailure(INPUT_DATA)` on a short/stale temp
  window; the feature-vector order is `[…, antecedent_precip, antecedent_temp, season…, lags]`.
- **Verification:** `uv run pytest tests/unit/models/test_nwp_regression.py`; prove soundness (break the
  temp-window guard → the short-window test goes RED).

### T3 — make it run: fix the gating from T1

- **Scope:** apply the T1 root-cause fix. If **(a) shadowing**: adjust the `[model_priorities]` entry in
  `config.toml` (+ the mac-mini overlay if it overrides priorities) so `seasonal_precip_runoff_regression`
  fires — either give it a priority `< 10` (make it PRIMARY ahead of nwp_regression) or configure it in a
  distinct output slot; owner-visible choice recorded here. If **(b) failure**: fix the failure (e.g.
  relax/repair the antecedent-window validation, or ensure the reanalysis backfill covers the required
  window) so a normal cycle emits a forecast.
- **Files:** `config.toml` (`[model_priorities]`) and/or the failure-fix in `nwp_regression.py`;
  `tests/unit/config/…` or model tests as appropriate.
- **Verification:** a forecast-cycle (unit/integration) shows `seasonal_precip_runoff_regression` emitting a
  forecast for a healthy station-cycle.

### T4 — retrain both BAFU stations via onboard-model (DC-3)

- **Scope:** re-run the model through `onboard-model` (model_id=`seasonal_precip_runoff_regression`) for
  stations 2009 + 2091 so the compat check re-validates the new `temperature` requirement, and fresh
  artifacts (with the antecedent-temp coefficient) are trained + skill-gated + promoted, superseding the
  2026-07-21 artifacts.
- **Files:** no code — an operational run of `flows/onboard_model.py` on the mac-mini (post-deploy, T5).
- **Verification:** new active `model_artifacts` for both stations dated post-deploy; the skill gate passed.

### T5 — deploy to mac-mini + verify live forecasts

- **Scope:** version-bump + build; deploy with the **correct overlay stack**
  `docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d --build` (never a bare
  `docker compose up` — that drops the overlay; see `reference_macmini_ssh_access`), export the recap build
  secret first; re-run `register_deployments` only if specs changed; run T4's onboard-model; confirm the
  next `forecast-cycle` emits a `seasonal_precip_runoff_regression` forecast for both stations.
- **Files:** deploy actions (no repo change beyond the version bump).
- **Verification:** live — `forecasts` table shows a `seasonal_precip_runoff_regression` row for 2009 + 2091
  from a post-deploy cycle; the existing feeds (other models, collectors, NWP) are unaffected.

## Dependency graph

```json
{
  "phases": [
    { "id": "diagnose", "tasks": ["T1"], "parallel": false, "depends_on": [] },
    { "id": "model", "tasks": ["T2"], "parallel": false, "depends_on": [] },
    { "id": "run-fix", "tasks": ["T3"], "parallel": false, "depends_on": ["diagnose", "model"],
      "note": "T3 needs T1's root cause and T2's model in place." },
    { "id": "retrain-deploy", "tasks": ["T4", "T5"], "parallel": false, "depends_on": ["run-fix"],
      "note": "T5 deploys; T4 onboard-model runs on the deployed host." }
  ]
}
```

## Exit gates

```bash
uv run ruff format --check src/ tests/ && uv run ruff check src/ tests/
uv run pyright src/
uv run pytest
```

## References

- `src/sapphire_flow/models/nwp_regression.py` (`SeasonalPrecipRunoffRegression` `:628`, base `:139`,
  `_extra_past_known` `:667`, `_antecedent_precip_sums` `:610`, `_season_features` `:561`,
  `_validate_continuous_window` `:568`, `_ShortForcingWindowError` `:105`, feature order `:270-272`).
- `adapters/forecast_interface.py` (past_known→past_dynamic/past_targets routing `:499-511,1007-1026`; NaN
  gate `:622`).
- `services/training_data.py` (`assemble_station_training_data` `:141`; aggregation fallback `:29-40`).
- `flows/train_models.py`, `flows/onboard_model.py`, `services/model_onboarding.py:130`.
- Live mac-mini state (2026-07-22): stations 2009/2091 onboarded, `seasonal_precip_runoff_regression`
  assigned @12 with 0 forecasts; `historical_forcing` has temperature 1981→2026.
- memory `reference_macmini_ssh_access` (deploy MUST use the `-f docker-compose.macmini.yml` overlay).
