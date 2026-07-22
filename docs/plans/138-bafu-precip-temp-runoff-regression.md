---
status: READY
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

A second `/plan` round (2026-07-22) escalated with 6 further majors; this revision folds them all (they
pin to existing repo conventions rather than inventing), and the `/plan` loop is **stopped here** — the
residual design is now well-specified against those conventions, and further automated rounds were churning:
- **Case-(b) failures split out of T3 into T2b** (pre-T4), and T2b now separates true **retrain-blockers**
  (code defects, training-window data gaps) from **operational/transient** causes (`no_active_artifact` is
  resolved by T4 itself; a transient NWP gap is not a training defect) — only the former gate T4.
- **Skill comparison is now DEFINED (DC-4)** — pinned to the repo's existing S.4 model-promotion convention
  (`architecture-context.md` §S.4): flood-range BSS/CRPS, like-for-like `skill_source` (with the documented
  fallback hierarchy), same `eval_period`, `min_skill_samples`/`min_skill_seasons` gates, and
  `evaluate_skill_gate`'s worst-across-scores reduction — so T3's promote/leave verdict is reproducible.
- **`config.toml` global default is now left UNTOUCHED in ALL cases** (previous "sync when both converge" was
  an unsound n=2→deployment-wide extrapolation) — the persisted per-station `model_assignments.priority` rows
  are the sole source of truth; no config/doc priority-touchpoint edits.
- **T1** adds station-level pre-model skip paths (`station_skipped_no_nwp`, no-assignments, registry-load,
  input-assembly) + the reproduction caveat (PRIMARY-mode cycles don't surface non-primary successes).
- **T5** follows the repo standard upgrade sequence (`cicd.md:132`: token, stop workers, `run --rm --build
  init`, `up -d`) in overlay form, not a bare `up -d --build`.
**READY** (owner flip 2026-07-22). The remaining open items are genuinely data-dependent (T1's per-station
case) or operational, not unresolved design.

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

Add `past_known reanalysis/temperature` (lookback mirrors the existing precip pattern exactly: a **module
constant `_TEMP_LOOKBACK_DAYS = 14`** alongside `_PRECIP_LOOKBACK_DAYS` (module-level, `nwp_regression.py:101`),
plus an **overridable class attribute `_temp_lookback_days = _TEMP_LOOKBACK_DAYS`** mirroring
`_precip_lookback_days` (`nwp_regression.py:659`); the model reads `self._temp_lookback_days`. Default **14 days**
— the melt/accumulation-relevant horizon, shorter than the 45-day precip window) and derive a single **antecedent
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
(`flows/onboard_model.py:271` passes the deployment's available past/future features into
`services/model_onboarding.py:217`, whose `missing_past = req.past_dynamic_features - available_past_features`
diff at `:221` verifies the new `temperature` past-dynamic feature is available), smoke test, train, hindcast,
**skill gate**, promote, and (re)assignment — the correct path when a
model's requirements change, and the source of the per-station skill numbers T3 uses.

### DC-4 — "beats `nwp_regression`" is defined by the existing S.4 promotion convention (not ad-hoc)

The skill-gated decision (T3) needs a *pinned* comparison, because `SkillScore` is multi-dimensional (metric,
lead_time_hours, season, flow_regime, skill_source, eval_period — `types/skill.py:20-40`); "beats" is
undefined without a policy. **Do not invent one — reuse the repo's documented model-promotion convention**
(`docs/architecture-context.md` §S.4 "model promotion skill priority", `:1156,1164-1168`):

- **Primary metric(s):** flood-range **BSS / CRPS** (the S.4-designated promotion metrics), evaluated for the
  `discharge` target. A model that wins the primary metric wins; if the primary metrics disagree, S.4's
  ordering is the tie-break (do NOT let a secondary metric like NSE override).
- **`skill_source` matching:** compare **like-for-like** — both models scored from the **same `skill_source`**,
  falling back down the S.4 hierarchy `hindcast_nwp_archive > operational > hindcast_reanalysis >
  transfer_validation` only if the preferred source is unavailable **for both**. If the incumbent
  `nwp_regression`'s stored score is from a different source or a stale/different-length `eval_period` than
  seasonal's fresh T4 run, **re-run `nwp_regression`'s hindcast/skill over the same period** so the comparison
  is valid (T4 scope).
- **Same `eval_period`** for both; enforce the **`min_skill_samples` / `min_skill_seasons`** gates from S.4 —
  if either model lacks enough samples, the comparison is inconclusive → **leave the ranking as-is** (do not
  promote on thin evidence).
- **Reduction:** reuse the existing **worst-across-valid-scores per-metric** pattern from
  `evaluate_skill_gate` (`services/model_onboarding.py:810-838`) applied to **both** models, rather than a new
  ad-hoc scalar.

This makes T3's promote/leave verdict reproducible (two operators reach the same answer) and consistent with
how the system already decides model promotion. *(If S.4 turns out to be aspirational/not-yet-implemented for a
head-to-head A-vs-B comparison, that gap is surfaced in T4 as the first thing to confirm — see T4.)*

## Tasks

### T1 — diagnose why `seasonal_precip_runoff_regression` stores 0 forecasts (do first)

*(T1 gates the case-(b) remedy (phase `model-case-b`) and T3's skill-gated priority decision. It does **not**
gate T2's core — the past-temp feature + artifact guard (phase `model-core`) — which is built in parallel with
T1. The dependency graph below reflects this split exactly: `model-core` has `depends_on: []`; only
`model-case-b` has `depends_on: ["diagnose"]`.)*

- **Scope:** on the mac-mini, diagnose the missing rows **per station (both 2009 Porte-du-Scex AND 2091
  Rheinfelden — different catchments, plausibly different reanalysis density/history)**. **There is no
  persisted per-model failure store to query** (reviewer correction): FI `ModelFailure` is converted to
  `ModelOutputError` in the adapter (`adapters/forecast_interface.py:369`) and per-model failures survive only
  in-memory as `failed_models` (`run_station_forecast.py:360`). Evidence sources are therefore:
  1. **Worker logs — ALL per-model failure events, not just `predict_failed`** (reviewer correction: a
     missing `predict_failed` line does **not** prove success; `_run_single_model` can fail via several
     distinct events). Enumerate every failure signal for `seasonal_precip_runoff_regression`:
     `run_station_forecast.model_not_found` (`:117`), `nwp.insufficient_coverage` (`:143`),
     `run_station_forecast.no_active_artifact` (`:157`), `run_station_forecast.predict_failed` (`:207`), and
     `run_station_forecast.qc_failed` (`:249`) — all in `run_station_forecast.py`. Presence of ANY ⇒ **case (b)
     failure** (record which).
  2. **Station-level pre-model skip/failure paths (reviewer major) — the model may never be reached.** Before
     any per-model loop, a station can be skipped or aborted: no active assignments, a model-registry load
     failure, an input-assembly failure/skip, or an NWP fetch abort / runtime-unavailable →
     `forecast_cycle.station_skipped_no_nwp` and related events (`flows/run_forecast_cycle.py:1801,1868,1875`).
     If seasonal never ran because its *station* was skipped, that is a **third** situation (neither "ran and
     lost" nor "ran and failed") — inspect these station-level events first.
  3. **`pipeline_health` station-dark records** — `_record_station_dark` / `forecast_cycle.station_dark`
     (`flows/run_forecast_cycle.py:614,629`) persists when a station emits no storable primary.
  4. **`forecasts` table + a reproduced run** — classify **case (a)** only on **positive evidence of
     success**: seasonal appears in `run_all_station_forecasts`'s `MultiModelForecastResult.results`
     (`run_station_forecast.py:334-360`) as a succeeding-but-non-primary model. **Reproduction caveat
     (reviewer):** a normal PRIMARY-mode `forecast-cycle` run does **not** surface non-primary successes (only
     the primary is stored); to see seasonal's non-primary success you must call/instrument
     `run_all_station_forecasts` directly (or log its `results`/`failed_models`) — do NOT infer case (a) from
     "no `predict_failed` line" in an ordinary cycle.
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

### T2 — add past-temperature to `SeasonalPrecipRunoffRegression` (DC-1) — CORE, parallel with T1

- **Scope:** extend the model: `_extra_past_known` adds `past_known reanalysis/temperature`
  (`PastKnownVariable(lookback=self._temp_lookback_days, unit=Unit.DEG_C, max_nan=0)`, with module constant
  `_TEMP_LOOKBACK_DAYS = 14` + class attr `_temp_lookback_days = _TEMP_LOOKBACK_DAYS`, mirroring
  `_PRECIP_LOOKBACK_DAYS`/`_precip_lookback_days`); add
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

### T2b — case-(b) remediation (runs AFTER T1, folds before deploy)

*(Split out of T3 (reviewer major): a case-(b) **failure** is a real bug that must be fixed **before T4's
retrain can succeed** — onboard-model trains → hindcasts → skill-gates → promotes **before** assignment
(`flows/onboard_model.py:751` assemble, `:840` hindcast, `:902` skill gate, `:959` promote), so a station that
fails to produce usable inputs cannot be skill-gated at all. This remediation is therefore pre-T4, not a
post-T4 priority concern. T3 is left as post-T4 skill-based priority **only**.)*

- **Scope — only *retrain-blocking* causes gate T4; operational/transient causes do NOT (reviewer major).**
  Split the T1 case-(b) signals:
  - **Retrain-blockers (fix here, before T4):** a **code** cause (antecedent/temp-window validation
    over-rejecting, a NaN-gate/shape defect) → a `nwp_regression.py` change that **lands in the same file/tests
    as T2's core** (`model-case-b` phase depends on `diagnose`; merges with the T2 diff; applies to **both**
    stations); or a **training-data gap** (a reanalysis-coverage hole *within the training/hindcast window* for
    a specific basin) → a **targeted reanalysis backfill for that basin** run before T4 (per-station — record
    which). These genuinely block `onboard-model` from producing a usable, skill-gated artifact.
  - **NOT retrain-blockers (do not gate T4):** `run_station_forecast.no_active_artifact` is **resolved by T4
    itself** (T4 promotes a fresh artifact) — it needs no separate remedy. `nwp.insufficient_coverage` and the
    station-level `station_skipped_no_nwp` are **operational/transient forecast-time** states (a given cycle
    lacked NWP), not training defects — treat them as operational evidence, and only escalate to a
    training-data fix **if** the shortfall reproduces inside the hindcast/training window. Do not hold T4 for a
    transient operational NWP gap.
- **Files:** `src/sapphire_flow/models/nwp_regression.py` + the T2 test file (code cause only); an operational
  reanalysis backfill (training-data-gap cause only — no repo change). If T1 finds **no** retrain-blocking
  case (b) (both stations are case (a) non-primary, or only transient/operational causes), this task is a
  no-op.
- **Verification:** the specific T1 failure signal no longer fires in a reproduced cycle for the affected
  station; for a code fix, a red-first test locks the over-rejection it corrected.

### T5 — deploy the extended code to mac-mini (runs BEFORE T4)

- **Scope:** follow the **repo standard upgrade sequence** (`docs/standards/cicd.md:132`), in the **overlay
  form** — every compose command carries `-f docker-compose.yml -f docker-compose.macmini.yml` (a bare
  `docker compose` drops the overlay and dark-fails the stack; see `reference_macmini_ssh_access`). Concretely:
  version-bump; `export RECAP_DG_CLIENT_TOKEN=$(cat secrets/recap_dg_client_token)`; stop the workers; run the
  **`init`** service to (re)build the image + apply migrations + re-register deployments
  (`docker compose -f … -f docker-compose.macmini.yml run --rm --build init`, `docker-compose.yml:261`); then
  `docker compose -f … -f docker-compose.macmini.yml up -d`. **No assignment suppression** — the DC-3 in-code
  feature-count guard makes the stale-artifact window a clean fall-through (a few `ModelFailure` log lines
  until T4 retrains, no station dark).
- **Files:** deploy actions (no repo change beyond the version bump).
- **Verification:** the extended container is up on the correct overlay; existing feeds (other models,
  collectors, NWP) are unaffected; the forecast cycle still stores *a* primary (the fall-through model) with no
  station-dark record while the seasonal artifact is stale.

### T4 — retrain both BAFU stations via onboard-model, producing skill (runs AFTER T5)

- **Scope:** on the deployed host, re-run `onboard-model`
  (model_id=`seasonal_precip_runoff_regression`) for stations 2009 + 2091 so the compat check re-validates the
  new `temperature` requirement, and fresh artifacts (with the antecedent-temp coefficient) are trained,
  **hindcast-scored**, skill-gated, and promoted, superseding the 2026-07-21 artifacts. **Capture the
  per-station skill for BOTH models on a DC-4-comparable basis** — same `skill_source`, same `eval_period`,
  flood-range BSS/CRPS, `min_skill_samples`/`min_skill_seasons` met. **First confirm DC-4 is executable:** if a
  comparable stored `nwp_regression` score does not exist for that station/period/source (e.g. its latest
  score is `operational` while seasonal's fresh run is `hindcast_reanalysis`, or a different-length window),
  **re-run `nwp_regression`'s hindcast/skill over the same period** so the head-to-head is valid; if S.4's
  head-to-head comparator turns out not to exist yet as tooling, surface that as the gating finding for T3
  (a small comparator built on `evaluate_skill_gate`'s reduction, not a new metric framework).
- **Files:** no code for the retrain itself — an operational run of `flows/onboard_model.py` (post-deploy);
  possibly a small skill-comparison helper if DC-4's head-to-head is not already available.
- **Verification:** new active `model_artifacts` for **both** 2009 + 2091 dated post-deploy with the skill gate
  passed; the DC-4-comparable per-station scores (seasonal-retrained vs nwp_regression, same source/period)
  are recorded for T3.

### T3 — SKILL-GATED priority for case-(a) stations — runs AFTER T4 (skill-priority ONLY)

*(Scope narrowed (reviewer major): case-(b) **failures** are handled entirely in **T2b** before T4. T3 is
purely the post-T4, skill-based promote/leave decision for case-(a) non-primary stations.)*

- **Goal:** each healthy BAFU station stores the **best** succeeding model as primary. "Best" is decided by
  **skill**, not by forcing a specific model's row to exist (reviewer correction: seasonal not being the
  stored primary is a *bug only if it failed*; if `nwp_regression` succeeds and scores better, its winning is
  correct-by-design per the Plan 089 trust hierarchy). We do **not** add a parallel/multi-output path — the
  Non-goal rules that out; the only combination strategies are
  `ModelCombinationStrategy.PRIMARY/POOLED/BMA/CONSENSUS` (`types/enums.py:95-99`), none a per-model "output
  slot".
- **Case (a) NON-PRIMARY — SKILL-GATED promotion (owner decision 2026-07-21).** For a station where T1 shows
  seasonal *succeeds* but `nwp_regression`@10 is primary, promote seasonal **only if** its retrained (T4)
  hindcast skill **beats `nwp_regression`** for that station **as defined by DC-4** (flood-range BSS/CRPS,
  like-for-like `skill_source`, same `eval_period`, min-sample gates, worst-across-valid-scores reduction);
  otherwise **leave the ranking as-is** and treat `nwp_regression` winning as the correct outcome. Where
  promotion IS warranted, change the **stored** order via a **persisted assignment operation**, NOT a
  `config.toml` edit (reviewer correction: config priority is applied only at onboarding; the cycle sorts on
  the stored `model_assignments.priority`, `run_station_forecast.py:327,335`). Mechanism: an explicit
  `UPDATE model_assignments SET priority=<n<10>` for the affected station(s) (the established practice, per
  Plan 089 history) — **per-station**, so 2009 and 2091 can diverge.
- **`config.toml` `[model_priorities]` global default is left UNTOUCHED in all cases (reviewer major).** It is
  keyed by **model_id only** — one deployment-wide value (`config.toml:60-61`,
  `seasonal_precip_runoff_regression = 12`) that `onboard_model` applies to *any* station onboarded with this
  model when `assignment_priority` is omitted (`services/model_onboarding.py:1042-1045`), including the 169
  CAMELS-CH basins in `[onboarding].basin_ids` (`config.toml:161-176`). A 2-station, catchment-specific skill
  verdict must **not** be extrapolated into that global default — even when both BAFU stations happen to agree
  (n=2 is not cross-catchment evidence, and the plan's own premise is that skill competitiveness is
  catchment-dependent). **Rule: the persisted per-station `model_assignments.priority` rows are the SOLE source
  of truth for the promote/leave outcome; `config.toml` stays at 12 whether the two stations converge or
  diverge.** No `config.toml`/`config-reference.toml`/`architecture-context.md:152`/`conventions.md:451`
  priority-touchpoint edits are needed (they would only apply to a global-default change, which this plan does
  not make). *(Residual gap, documented: a future re-onboard of a promoted station with `assignment_priority`
  omitted would pull the global 12 and silently revert its per-station override — Plan 089,
  `docs/plans/archive/089-*.md:88`. Mitigation: such a re-onboard must pass an explicit `assignment_priority`,
  or be treated as a fresh skill-gate re-evaluation. Flagged so the operator does not reintroduce the loss.)*
- **Files:** a persisted `UPDATE model_assignments SET priority=…` (SQL/store) for any promoted station —
  **and nothing in `config.toml` or its doc mirrors** (per the rule above).
- **Verification:** for each station where seasonal was promoted, a post-change `forecast-cycle` stores a
  `seasonal_precip_runoff_regression` **primary** row; for a station left un-promoted (nwp_regression better),
  the recorded DC-4 skill comparison justifies leaving it — **not** treated as an unresolved defect;
  `config.toml` remains at 12 and the per-station `model_assignments.priority` rows carry the outcome.

## Dependency graph

```json
{
  "phases": [
    { "id": "diagnose", "tasks": ["T1"], "parallel": false, "depends_on": [],
      "note": "Per-station: case (a) non-primary-success vs case (b) failure, with positive evidence. depends_on:[] — runs concurrently with model-core (both have no upstream)." },
    { "id": "model-core", "tasks": ["T2"], "parallel": false, "depends_on": [],
      "note": "Past-temp feature + artifact feature-count guard. depends_on:[] — independent of T1's outcome, so it runs concurrently with diagnose." },
    { "id": "model-case-b", "tasks": ["T2b"], "parallel": false, "depends_on": ["diagnose", "model-core"],
      "note": "Case-(b) remediation: a CODE fix merges into the model-core diff (same file/tests); a per-station DATA backfill is an operational step. No-op if T1 finds no case (b). Waits on diagnosis." },
    { "id": "deploy", "tasks": ["T5"], "parallel": false, "depends_on": ["model-core", "model-case-b"],
      "note": "Deploy the extended code. No suppression — the DC-3 code guard covers the stale-artifact window." },
    { "id": "retrain", "tasks": ["T4"], "parallel": false, "depends_on": ["deploy"],
      "note": "onboard-model on the deployed host, AFTER T5; produces the per-station skill numbers T3 needs." },
    { "id": "priority", "tasks": ["T3"], "parallel": false, "depends_on": ["diagnose", "retrain"],
      "note": "Case-(a) SKILL-GATED priority decision, per station, AFTER T4's skill gate. Skill-priority ONLY (case-(b) is model-case-b). Depends on diagnose (which stations are case a) and retrain (the skill numbers)." }
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
- `flows/train_models.py`, `flows/onboard_model.py` (compat-check call passing available features `:271`),
  `services/model_onboarding.py` (`onboard_model_flow`-path compat check `:217`, missing-feature diff `:221`;
  `assignment_priority` resolves to a single model-wide default via `config.assignment_priority_for_model` when
  omitted `:1042-1045`).
- Live mac-mini state (2026-07-22): stations 2009/2091 onboarded, `seasonal_precip_runoff_regression`
  assigned @12 with 0 forecasts; `historical_forcing` has temperature 1981→2026.
- memory `reference_macmini_ssh_access` (deploy MUST use the `-f docker-compose.macmini.yml` overlay).
