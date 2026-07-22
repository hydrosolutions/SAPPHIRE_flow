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
and live mac-mini state. Went through a `/plan` adversarial round (2026-07-22) that escalated with real
model-assignment-mechanics corrections; this revision folds them:
- **Dropped the suppress/reactivate deploy dance** — it was both broken (`create_station_assignment` skips
  reactivating an INACTIVE assignment) and redundant (the in-code feature-count guard already makes a stale
  artifact a clean `ModelFailure` the cycle falls through). Only the code guard remains.
- **Priority is a persisted `UPDATE model_assignments`, not a `config.toml` edit** — config priority is read
  only at onboarding; the cycle sorts on the *stored* `assignment.priority`.
- **Priority is now SKILL-GATED (owner decision 2026-07-21):** promote seasonal above `nwp_regression` only
  where the retrained (temp-augmented) model's hindcast skill actually beats it per-station; otherwise
  `nwp_regression` legitimately winning is the correct, non-bug outcome. So T3 runs AFTER T4's skill gate.
- T1 enumerates ALL per-model failure modes; doc touchpoints + a citation fixed.
For a confirming `/plan` round before READY.

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
2. **It stores ZERO forecasts.** Live: no `seasonal_precip_runoff_regression` row exists in the `forecasts`
   table. **Important correction (reviewer finding):** the cycle does **not** short-circuit on first success.
   `run_all_station_forecasts` (`src/sapphire_flow/services/run_station_forecast.py:334-360`) iterates **every**
   active assignment in priority order and collects **every** successful result into `results`; the PRIMARY
   combination strategy then designates only the **first** success as `primary_model_id` (`:357-358`) and it is
   the primary that is persisted to `forecasts`. So a seasonal model that **ran and succeeded** but was not
   primary produces **0 rows in `forecasts` yet no failure** — indistinguishable, from the DB alone, from a
   model that **failed predict** (returned `ModelFailure`, captured only in-memory as `failed_models` `:360`
   and logged at `run_station_forecast.predict_failed` `:207`). Disambiguating these two cases is exactly what
   T1 must do. The observed 2026-07-22 00:00 fall-through to `linear_regression_daily`@30 as the stored primary
   is consistent with **either** (nwp_regression@10 AND seasonal@12 both failed → linear became primary) **or**
   (they succeeded but the stored-primary selection differs from what we expected) — the DB row alone cannot
   tell us. Root cause is **unconfirmed** and is Task T1.

This plan closes both: add past-temperature, make it actually run, retrain, deploy.

## Objective

A BAFU discharge regression that consumes past+future precipitation, past+future temperature, and past
runoff, **running correctly and producing skill-competitive forecasts** on both operational BAFU stations —
stored as the primary where (and only where) it out-scores `nwp_regression`, and never silently failing when
it doesn't. (If a station's T1 case is a real failure, that failure is fixed regardless.)

## Non-goals

- **Not** a new model class — extend `SeasonalPrecipRunoffRegression` in place (owner decision).
- **Not** removing the season/antecedent terms (they help; the user's "past+future precip+temp + past
  runoff" is a superset the extended model satisfies).
- **Not** any snow/SWE work (that is Plan 139, Nepal).
- **Not** changing the FI contract, the forecast-cycle engine, or the other models.

## Design decisions (locked before `/plan`)

### DC-1 — past-temperature as an antecedent MEAN, mirroring antecedent precip

Add `past_known reanalysis/temperature` (lookback = a **class-level `_TEMP_LOOKBACK_DAYS` constant**, mirroring
the existing precip `_PRECIP_LOOKBACK_DAYS` so a subclass can override it, default **14 days** — the
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
`past_known reanalysis/temperature` channel needs **no new adapter/ingestion work**; declaring it in
`past_dynamic_features` is enough — that set drives the reanalysis fetch in **both** training
(`services/training_data.py:177-179`: `past_features` `:177` ∪ `future_features` `:178` → `required_features`
`:179`) and operational assembly
(`services/operational_inputs.py:410`, the `past_dynamic` branch that calls `fetch_reanalysis_bindings`).
*(Reviewer correction: the earlier citation `forecast_interface.py:1007-1026` is only where the FI adapter
builds per-variable input **series from already-assembled frames**, and `:499-511` is requirement
**projection** — neither is where the reanalysis **fetch** is routed. The fetch is driven by the two service
call-sites above.)* Confirm the reanalysis fetch key list includes `temperature` for the model's
`past_dynamic_features`.

### DC-3 — the artifact schema changes ⇒ full retrain via onboard-model; a code guard covers the deploy→retrain window

Adding a feature lengthens the coefficient vector by one, so **the 2026-07-21 active artifacts are
shape-incompatible** with the extended code: `predict` does `features @ coefficients`
(`nwp_regression.py:434`) where `features` gains the antecedent-temp column but the old `coefficients` do not.
Today the only pre-matmul guard is a **lag-count** check (`len(lags) != artifact.n_lags`,
`nwp_regression.py:411`) — there is **no total-feature-count guard**, so an old artifact against new code would
either raise a raw NumPy shape error or (if lengths coincidentally align) silently mis-weight.

**Single mitigation — an in-`predict` feature-count guard (in T2).** Add an explicit artifact-shape check
**before** the matmul (`nwp_regression.py:434`): if `coefficients.shape[0] != expected_feature_count` (base
precip+temp + antecedent columns + season + `n_lags`), return `ModelFailure(INPUT_DATA, "artifact
feature-count mismatch: got … expected …")` — never a raw shape crash, per the FI rule. This is the *general*
fix (protects against any future artifact/code shape drift), and it is **sufficient on its own** for the
deploy→retrain window: between the container swap (T5) and the retrain completing (T4), every scheduled cycle
that reaches the stale seasonal artifact gets a clean `ModelFailure`, and the PRIMARY first-success chain
(`run_station_forecast.py:334-360`) simply falls through to the next model — exactly as it does today (the
observed stored primary is `linear_regression_daily`). No station goes dark (`_record_station_dark` fires only
when *no* model succeeds, `run_forecast_cycle.py:611-630`). *(Reviewer correction: an earlier draft also
suppressed+reactivated the seasonal assignment across the deploy. That was **cut** — it is broken
(`create_station_assignment`, `model_onboarding.py:869-879`, returns an existing INACTIVE assignment WITHOUT
reactivating it, so onboard-model can't undo the suppression) and redundant with this guard; it bought only
quieter logs for a short self-closing window at the cost of an unspecified per-station DB toggle.)*

Retrain through the **`onboard-model`** flow (not bare `train-models`): it re-runs the compatibility check
(`services/model_onboarding.py:130` verifies the new `past_dynamic_features` incl. `temperature` are
available), smoke test, train, hindcast, **skill gate**, promote, and (re)assignment — the correct path when a
model's requirements change, and the source of the per-station skill numbers T3 uses.

## Tasks

### T1 — diagnose why `seasonal_precip_runoff_regression` stores 0 forecasts (do first)

*(T1 gates T2's case-(b) fold and T3's skill-gated priority decision. T2's core — the past-temp feature +
artifact guard — does not depend on T1's outcome, so it can be built in parallel; only a case-(b) code/data
remedy waits for the diagnosis.)*

- **Scope:** on the mac-mini, diagnose the missing rows **per station (both 2009 Porte-du-Scex AND 2091
  Rheinfelden — different catchments, plausibly different reanalysis density/history)**. **There is no
  persisted per-model failure store to query** (reviewer correction): FI `ModelFailure` is converted to
  `ModelOutputError` in the adapter (`adapters/forecast_interface.py:369`) and per-model failures survive only
  in-memory as `failed_models` (`run_station_forecast.py:360`). Evidence sources are therefore:
  1. **Worker logs — ALL per-model failure events, not just `predict_failed`** (reviewer correction: a
     missing `predict_failed` line does **not** prove success; `_run_single_model` can fail via several
     distinct events). Enumerate every failure signal for `seasonal_precip_runoff_regression`:
     `model_not_found`, `nwp.insufficient_coverage`, `run_station_forecast.no_active_artifact`,
     `run_station_forecast.predict_failed` (`:207`), and `run_station_forecast.qc_failed`
     (`run_station_forecast.py:114,141,155,207,247`). Presence of ANY ⇒ **case (b) failure** (record which).
  2. **`pipeline_health` station-dark records** — `_record_station_dark` / `forecast_cycle.station_dark`
     (`flows/run_forecast_cycle.py:614,629`) persists when a station emits no storable primary.
  3. **`forecasts` table + a reproduced run** — classify **case (a)** only on **positive evidence of
     success**: seasonal appears in the reproduced cycle's `MultiModelForecastResult.results`
     (`run_station_forecast.py:334-360`) as a succeeding-but-non-primary model. Do NOT infer case (a) merely
     from "no `predict_failed` line" — reproduce the cycle (or inspect the in-memory results) to confirm it
     actually ran and succeeded.
- **Distinguish, per station, exactly two cases:** **(a) not-stored-because-non-primary** — seasonal is
  present in `results` as a success but a higher-priority model became primary (positive success evidence, no
  failure event of ANY of the five kinds above). **(b) failed** — ANY of the five failure events fired (e.g.
  `_ShortForcingWindowError` → `ModelFailure` from the antecedent-window validation `_validate_continuous_window`
  `:568`, a NaN-gate rejection, missing reanalysis coverage, no active artifact, or QC rejection). Record the
  case + evidence **for each of 2009 and 2091 separately** — they may diverge (T3 handles divergence).
- **Files:** read-only diagnosis (mac-mini worker logs + `forecasts` + `pipeline_health`); findings recorded
  in this plan.
- **Verification:** the root cause (case a or b) is stated **with evidence (specific log line / failure cause /
  DB row) for BOTH stations** before T3 designs the fix.

### T2 — add past-temperature to `SeasonalPrecipRunoffRegression` (DC-1)

- **Scope:** extend the model: `_extra_past_known` adds `past_known reanalysis/temperature`
  (`PastKnownVariable(lookback=_TEMP_LOOKBACK_DAYS, unit=Unit.DEG_C, max_nan=0)`); add
  `_antecedent_temp_means` (mirror `_antecedent_precip_sums` with MEAN); extend `_extra_train_features` +
  `_extra_predict_features` to emit the antecedent-temp column in the locked order (DC-1); extend
  `_train_warmup_steps` to `max(n_lags, precip_window, temp_window)`; extend `_validate_continuous_window`
  to also gate the temperature window — and **parameterize its hardcoded `"antecedent-precip"` label**
  (`nwp_regression.py:575,605`, the docstring + `_ShortForcingWindowError` message) to a `feature_label`
  argument so temperature diagnostics don't lie (short/stale ⇒ `_ShortForcingWindowError` ⇒ `ModelFailure`,
  never raise — CLAUDE.md FI rule). Also add the DC-3 **artifact feature-count guard** before the matmul
  (`nwp_regression.py:434`): a coefficient/feature-length mismatch returns `ModelFailure(INPUT_DATA)`, not a
  raw shape crash.
- **Files:** `src/sapphire_flow/models/nwp_regression.py`; **`tests/unit/models/test_seasonal_precip_runoff_regression.py`**
  — this is the dedicated file that owns the seasonal model's requirements/routing/warmup/stale-window/FI-failure
  tests (NOT `test_nwp_regression.py`). Its fixtures currently feed only **reanalysis precipitation**
  (`_train_series` returns `ts, discharge, precip, temp, reanalysis_precip`, ~line 424; the routing test at
  ~line 102 asserts only `precipitation` in `past_dynamic_features`) — they **must be extended to also supply
  reanalysis temperature** and assert `temperature` routes into `past_dynamic_features`. New red-first tests:
  the requirement now declares `past_known reanalysis/temperature`; train builds the antecedent-temp column;
  predict emits it and returns `ModelFailure(INPUT_DATA)` on a short/stale temp window; predict returns
  `ModelFailure(INPUT_DATA)` on a coefficient/feature-count mismatch (artifact guard); the feature-vector order
  is `[…, antecedent_precip, antecedent_temp, season…, lags]`.
- **Docs:** update the **Plan 129 model description in `docs/architecture-context.md:148`** to note the added
  `past_known reanalysis/temperature` channel + antecedent-temp mean feature (per the "every code change
  updates affected docs" rule).
- **Verification:** `uv run pytest tests/unit/models/test_seasonal_precip_runoff_regression.py`; prove
  soundness (break the temp-window guard → the short-window test goes RED; break the feature-count guard →
  the mismatch test goes RED).

### T5 — deploy the extended code to mac-mini (runs BEFORE T4)

- **Scope:** version-bump + build; deploy with the **correct overlay stack**
  `docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d --build` (never a bare
  `docker compose up` — that drops the overlay; see `reference_macmini_ssh_access`), exporting the recap build
  secret first; re-run `register_deployments` only if specs changed. **No assignment suppression** — the DC-3
  in-code feature-count guard makes the stale-artifact window a clean fall-through (a few `ModelFailure` log
  lines until T4 retrains, no station dark).
- **Files:** deploy actions (no repo change beyond the version bump).
- **Verification:** the extended container is up on the correct overlay; existing feeds (other models,
  collectors, NWP) are unaffected; the forecast cycle still stores *a* primary (the fall-through model) with no
  station-dark record while the seasonal artifact is stale.

### T4 — retrain both BAFU stations via onboard-model, producing skill (runs AFTER T5)

- **Scope:** on the deployed host, re-run `onboard-model`
  (model_id=`seasonal_precip_runoff_regression`) for stations 2009 + 2091 so the compat check re-validates the
  new `temperature` requirement, and fresh artifacts (with the antecedent-temp coefficient) are trained,
  **hindcast-scored**, skill-gated, and promoted, superseding the 2026-07-21 artifacts. **Capture the
  per-station hindcast skill for the retrained seasonal model AND the incumbent `nwp_regression`** — this is
  the input to T3's skill-gated decision.
- **Files:** no code — an operational run of `flows/onboard_model.py` on the mac-mini (post-deploy).
- **Verification:** new active `model_artifacts` for **both** 2009 + 2091 dated post-deploy with the skill gate
  passed; the per-station skill numbers (seasonal-retrained vs nwp_regression) are recorded for T3.

### T3 — resolve gating: fix failures (case b) + SKILL-GATED priority (case a) — runs AFTER T4

- **Goal:** each healthy BAFU station stores the **best** succeeding model as primary. "Best" is decided by
  **skill**, not by forcing a specific model's row to exist (reviewer correction: seasonal not being the
  stored primary is a *bug only if it failed*; if `nwp_regression` succeeds and scores better, its winning is
  correct-by-design per the Plan 089 trust hierarchy). We do **not** add a parallel/multi-output path — the
  Non-goal rules that out; the only combination strategies are
  `ModelCombinationStrategy.PRIMARY/POOLED/BMA/CONSENSUS` (`types/enums.py:95-99`), none a per-model "output
  slot".
- **Case (b) FAILURE — fix it (a real bug), BEFORE T4 retrain can succeed.** The remedy depends on the T1
  cause: a **code** cause (e.g. the antecedent/temp-window validation over-rejecting) folds into **T2**; a
  **per-station data** cause (e.g. a reanalysis-coverage gap specific to only 2009's or 2091's basin) is a
  **targeted reanalysis backfill for that basin** run before T4. Record which station needs which; a code fix
  applies to both, a data gap does not.
- **Case (a) NON-PRIMARY — SKILL-GATED promotion (owner decision 2026-07-21).** For a station where T1 shows
  seasonal *succeeds* but `nwp_regression`@10 is primary, promote seasonal **only if** its retrained (T4)
  hindcast skill **beats `nwp_regression`** for that station; otherwise **leave the ranking as-is** and treat
  `nwp_regression` winning as the correct outcome. Where promotion IS warranted, change the **stored** order
  via a **persisted assignment operation**, NOT a `config.toml` edit alone (reviewer correction: config
  priority is applied only at onboarding; the cycle sorts on the stored `model_assignments.priority`,
  `run_station_forecast.py:327,335`). Mechanism: an explicit `UPDATE model_assignments SET priority=<n<10>`
  for the affected station(s) (the established practice, per Plan 089 history) — **per-station**, so 2009 and
  2091 can diverge. For consistency of future re-onboards, **also** lower the `[model_priorities]` default in
  `config.toml:59-63` (+ the mac-mini overlay if it overrides priorities) to match.
- **Files:** `nwp_regression.py` (only if case (b) is a code fix — folds into T2); a persisted
  `UPDATE model_assignments` (SQL/store) for any promoted station; `config.toml` `[model_priorities]` +
  **doc touchpoints (reviewer major): `docs/spec/config-reference.toml:116` and the priority sentence in
  `docs/architecture-context.md:152`** — updated whenever the seasonal priority changes.
- **Verification:** for each station where seasonal was promoted, a post-change `forecast-cycle` stores a
  `seasonal_precip_runoff_regression` **primary** row; for a station left un-promoted (nwp_regression better),
  the recorded skill comparison justifies leaving it — **not** treated as an unresolved defect.

## Dependency graph

```json
{
  "phases": [
    { "id": "diagnose", "tasks": ["T1"], "parallel": false, "depends_on": [],
      "note": "Per-station: case (a) non-primary-success vs case (b) failure, with positive evidence." },
    { "id": "model", "tasks": ["T2"], "parallel": false, "depends_on": ["diagnose"],
      "note": "Past-temp feature + artifact feature-count guard; ALSO folds any case-(b) CODE fix T1 found. Any case-(b) per-station DATA backfill also lands here (before deploy/retrain)." },
    { "id": "deploy", "tasks": ["T5"], "parallel": false, "depends_on": ["model"],
      "note": "Deploy the extended code. No suppression — the DC-3 code guard covers the stale-artifact window." },
    { "id": "retrain", "tasks": ["T4"], "parallel": false, "depends_on": ["deploy"],
      "note": "onboard-model on the deployed host, AFTER T5; produces the per-station skill numbers T3 needs." },
    { "id": "priority", "tasks": ["T3"], "parallel": false, "depends_on": ["diagnose", "retrain"],
      "note": "Case-(a) SKILL-GATED priority decision, per station, AFTER T4's skill gate. T3 last because the promote/leave decision depends on the retrained model's hindcast skill." }
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
- `adapters/forecast_interface.py` (requirement **projection** `:499-511`; per-variable input-series build
  from assembled frames `:1007-1026`; `ModelFailure`→`ModelOutputError` conversion `:369`; NaN gate `:622`).
  *Note: `:499-511`/`:1007-1026` are NOT where reanalysis fetch is routed — see the two service call-sites
  below.*
- `services/training_data.py` (reanalysis fetch driven by `past ∪ future` features `:177-179`;
  `assemble_station_training_data` `:141`; aggregation fallback `:29-40`).
- `services/operational_inputs.py` (operational `past_dynamic` reanalysis fetch `:410`).
- `services/run_station_forecast.py` (every-assignment loop + PRIMARY first-success storage `:334-360`;
  `predict_failed` log `:207`) and `flows/run_forecast_cycle.py` (`station_dark` / `pipeline_health` records
  `:614,629`).
- `config.toml` (`[model_priorities]` first-success chain, seasonal @12 `:59-63`); `types/enums.py`
  (`ModelCombinationStrategy` `:95-99` — no per-model "output slot").
- `flows/train_models.py`, `flows/onboard_model.py`, `services/model_onboarding.py:130`.
- Live mac-mini state (2026-07-22): stations 2009/2091 onboarded, `seasonal_precip_runoff_regression`
  assigned @12 with 0 forecasts; `historical_forcing` has temperature 1981→2026.
- memory `reference_macmini_ssh_access` (deploy MUST use the `-f docker-compose.macmini.yml` overlay).
