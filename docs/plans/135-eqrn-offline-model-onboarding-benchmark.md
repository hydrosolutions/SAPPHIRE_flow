# Plan 135 — EQRN Offline Model-Onboarding Benchmark

**Status**: DRAFT

## Objective

Decide whether an EQRN-style extreme-tail flood-risk model is worth adding as a
SAPPHIRE Flow model alternative by benchmarking it offline against the existing
model set, while deliberately exercising the existing Flow 13 model-onboarding
path as the integration harness once a minimal contract-conforming candidate
exists.

## Owner Decisions

1. **Use model onboarding as the integration benchmark, not just a standalone
   notebook benchmark.** Flow 13 already covers the lifecycle we care about:
   register, compatibility validation, smoke test, train, hindcast, skill gate,
   promote, and assign (`docs/v0-scope.md:24`, `docs/v0-scope.md:409`,
   `docs/architecture-context.md:1356`).
2. **Do not add the R packages as production dependencies.** The first benchmark
   may inspect EQRN/ExtremeConformal behavior externally, but this repo should
   not add R/torch/GPL runtime dependencies to `pyproject.toml`. A production
   candidate must be Python-native or ForecastInterface-compatible.
3. **Implement experiment models in a separate package.** The EQRN candidate
   should live outside this repo and expose a ForecastInterface model entry
   point. SAP3 then consumes it through the existing FI adapter. This keeps the
   service repo clean and gives us the same pattern for other model experiments
   such as TiREX.
4. **Use ForecastInterface from the start.** Do not build a native
   `StationForecastModel` shim first unless the FI contract itself blocks the
   experiment. The experiment package can still be lightweight, but its public
   boundary should be FI so onboarding tests the intended multi-team contract.
5. **Use all eligible BAFU discharge stations.** The experiment scope is not a
   curated 3-10 station sample. It should include every currently available BAFU
   station with sufficient discharge observations and required historical forcing.
   Stations that fail eligibility checks are reported explicitly.
6. **Do not make direct exceedance probability a SAPPHIRE-side workaround.** The
   ForecastInterface contract change is tracked upstream in
   `hydrosolutions/ForecastInterface#4`. Until that contract exists, SAP3 stores
   and verifies physical forecast variables as members or quantiles
   (`src/sapphire_flow/types/ensemble.py:17`, `src/sapphire_flow/types/ensemble.py:76`).
7. **Treat the all-station benchmark as an isolated experiment run, not a dry
   run.** Flow 13 writes training artifacts, hindcast rows, active artifacts, and
   assignments. The benchmark must use a disposable experiment database,
   experiment artifact location, and experiment model identifiers unless a
   follow-up plan adds no-promote/no-assign controls.
8. **Evaluate both method skill and integration fitness.** A model that improves
   high-flow Brier/reliability metrics but cannot pass model onboarding is not
   ready for SAP3. A model that onboards cleanly but does not improve alert skill
   remains a research result, not a promoted alternative.

## Deliberation

The user question is whether this should simply be model onboarding. Mostly yes,
but with one guardrail: Flow 13 should test a model candidate, not be the first
place where we discover whether the method can be represented as a forecast
model at all.

The repo already defines the lifecycle we want to exercise. `onboard_model_flow`
adapts FI models, registers the model class, validates per-unit compatibility,
runs smoke tests, trains, hindcasts, computes skill, evaluates the skill gate,
promotes an artifact, and creates assignments (`src/sapphire_flow/flows/onboard_model.py:1`;
`docs/architecture-context.md:1365`). That is exactly the operational question:
"can this model behave like a SAP3 model?"

However, EQRN-style methods naturally emphasize high quantiles and direct
threshold exceedance probabilities. Current SAP3/FI integration consumes
deterministic, member, or quantile forecasts and derives exceedance probability
from them for alerts (`src/sapphire_flow/services/alert_strategy.py:33`). A
probability-only model would skip too much of the forecast contract: no physical
forecast distribution for plots, QC, CRPS/CRPSS, arbitrary future thresholds, or
multi-model combination. Therefore the experiment should first define a minimal
candidate that emits a SAP3-compatible quantile grid for discharge, with optional
research-side direct exceedance probabilities recorded only in experiment output.

## Non-Goals

- No production model registration in this repo.
- No change to the ForecastInterface package or SAP3 adapter.
- No database schema change.
- No dependency addition to `pyproject.toml` for R, torch, EQRN, ExtremeCI, or
  ExtremeConformal.
- No operational assignment of an EQRN model to stations outside the scoped
  experiment.
- No implementation of TiREX in this plan; TiREX is only a design pressure for
  making the separate-package pattern reusable.

## Phase 1 — Benchmark Protocol

**Scope**: Define the experiment contract before implementation.

Tasks:

- Build or identify a disposable experiment database/configuration that already
  contains the candidate BAFU station inventory, historical discharge
  observations, and historical forcing needed for the requested split and horizon.
  The current reference fixtures are not sufficient for the all-BAFU experiment.
- Query all populated BAFU discharge stations in that experiment store and compute
  eligibility from actual observation and forcing coverage.
- Include every eligible BAFU discharge station in the benchmark. Report excluded
  stations with the reason: insufficient observations, insufficient flood events,
  missing forcing, incompatible target metadata, or unsupported time step.
- Use daily lead times 1-5, but label lead day 1 as the paper-faithful comparison.
- Use blocked or rolling time splits only; no random time-series cross-validation.
- Fix the threshold set before model fitting: configured danger thresholds where
  available, plus empirical Q90/Q95/Q99 fallback thresholds for research-only
  comparison.
- Define success criteria before training:
  - SAP3-native metrics currently produced by Flow 13 can gate onboarding.
  - Quantile-threshold Brier/reliability and CRPS-from-quantiles require either a
    pre-run SAP3 skill-service fix with tests or a separate experiment evaluator.
  - Until that scoring gap is closed, do not use current SAP3 quantile skill as
    the decision gate for threshold probability skill, because it treats quantile
    values as pseudo-members instead of interpolating threshold exceedance
    probabilities from the quantile CDF.
  - High-flow skill must improve without unacceptable POD/FAR/CSI regression, and
    the model must have no contract/onboarding failures.

Verification:

```bash
uv run pytest tests/unit/services/skill -q
```

## Phase 2 — Baseline Model Runs

**Scope**: Establish comparable baseline hindcast/skill outputs for existing
models.

Tasks:

- Run the scoped benchmark over:
  - `linear_regression_daily`
  - `nwp_regression`
  - `nwp_rainfall_runoff`
  - `persistence_fallback`
  - `climatology_fallback`
- Prefer existing training, hindcast, and skill services over a notebook-only
  runner so the baselines match SAP3 behavior.
- Capture per-station, per-model, per-lead, per-threshold metrics.

Verification:

```bash
uv run pytest tests/unit/services/test_model_registry.py tests/unit/services/skill -q
```

## Phase 3 — Minimal EQRN Candidate Shape

**Scope**: Specify the smallest separate-package FI model candidate that can
enter Flow 13 without committing to production adoption.

Tasks:

- Implement only after this DRAFT becomes READY.
- Candidate lives in a separate Python package with its own tests and a
  `sapphire_flow.models` entry point for experiment installation.
- Candidate entry-point class must declare SAP3 discovery metadata required for
  external model IDs: `model_tier` and `alert_eligibility`. Do not rely on SAP3's
  built-in model-ID maps for experiment models.
- Candidate must be a ForecastInterface model that `adapt_if_fi` can expose as a
  SAP3 model.
- Candidate must target the exact FI version pinned by SAP3 for this experiment
  unless a separate compatibility update is planned first.
- Candidate must pass an explicit FI contract checklist:
  - target variable is `discharge`;
  - target unit maps to SAP3 `M3_PER_S`;
  - forcing requirements use units supported by the current FI adapter;
  - spatial requirement is one representation the adapter supports;
  - temporal requirement is a 24-hour time step with future-known horizon;
  - target `TargetSpec.representations` includes `QUANTILES`;
  - output is deterministic, trajectories, or quantiles only;
  - anticipated insufficiency or degraded inputs return `ModelFailure`, not an
    exception.
- Candidate must emit the physical target variable, initially `discharge`, as a
  quantile grid accepted by `ForecastEnsemble.from_quantiles`.
- Candidate should include at least seven quantile levels with lower and upper
  tail coverage, plus upper-tail levels useful for flood thresholds, for
  example `0.98`, `0.99`, and `0.995`.
- Direct exceedance probabilities, if produced by the method, are experiment
  artifacts only until ForecastInterface supports them.
- Start with station-scoped daily modeling unless the implementation evidence
  shows a group-scoped FI artifact is simpler and more faithful.

Verification:

```bash
uv run pytest tests/unit/services/test_model_registry.py tests/unit/services/test_model_onboarding.py -q
```

## Phase 4 — Flow 13 Isolated Experiment Benchmark

**Scope**: Exercise model onboarding on the scoped station set without making the
candidate an operational default.

Tasks:

- Register the candidate model class through the existing entry-point discovery
  mechanism from the separate experiment package.
- Run Flow 13 on all eligible BAFU discharge stations with explicit scope in the
  disposable experiment database/configuration only.
- Require compatibility, smoke test, training, hindcast, skill-gate, promotion,
  and assignment outcomes to be recorded.
- Use a non-empty `skill_gate_thresholds` configuration for the experiment so
  Flow 13 is not merely a pass-through promotion.
- For the Flow 13 integration gate, use exact currently supported metric keys
  whose current quantile handling is acceptable as an onboarding sanity gate, for
  example `mae`, `nse`, or `kge`. Do not gate on `brier_score`, `reliability`, or
  `crps` for quantile threshold skill unless the SAP3 skill-service quantile
  scoring fix has been implemented and verified first.
- Decide method skill from the dedicated experiment evaluator when using
  quantile-threshold Brier/reliability or CRPS-from-quantiles without a SAP3 skill
  fix.
- Keep promotion and assignment scoped to the experiment environment; do not
  point the run at staging or production stores.
- Run a one-station preflight before the full all-eligible-BAFU run to catch
  package/import/FI-shape failures cheaply. This is a preflight, not a change to
  the final station scope.
- Measure runtime during preflight, estimate full-run duration, and define a
  chunk/restart strategy before launching the full run. The current Flow 13
  per-unit station loop is sequential; only skill computation is mapped inside a
  unit.
- Define cleanup for experiment model records, artifacts, hindcast rows, active
  artifacts, and assignments before launching the full run.

Verification:

```bash
uv run pytest tests/unit/flows/test_onboard_model_flow.py tests/unit/services/test_model_onboarding.py -q
```

## Phase 5 — Skill and Alert-Relevance Report

**Scope**: Produce the decision artifact.

Tasks:

- Summarize Brier Score, BSS, reliability, POD, FAR, CSI, CRPS/CRPSS where
  representable, MAE, NSE, KGE, runtime, and model failure rate.
- Separate metrics produced by SAP3's current skill service from experiment-only
  metrics computed by a dedicated evaluator.
- Separate lead-1 results from lead-2 through lead-5 results.
- Separate operational thresholds from empirical research thresholds.
- Record whether improvements are broad, station-specific, or threshold-specific.
- Recommend one of:
  - do not pursue
  - keep as offline research only
  - implement as a SAP3-native / FI-native model candidate
  - wait for ForecastInterface direct exceedance-probability support

Verification:

```bash
uv run pytest tests/unit/services/skill tests/unit/services/test_model_onboarding.py -q
```

## Risks

- **Representation risk**: EQRN may be strongest as direct risk output, while
  SAP3 currently requires physical forecast variables. Mitigation: benchmark a
  quantile-grid candidate and keep direct probabilities as research-side output.
- **Method complexity risk**: if a faithful Python implementation becomes large,
  stop before productionizing and keep the benchmark external.
- **Runtime risk**: the model itself may be fast, but Flow 13 does more than
  inference: it trains, writes artifacts, runs hindcasts, persists hindcast rows,
  computes skill, evaluates gates, and creates assignments for every eligible
  station. Mitigation: one-station preflight, bounded daily horizon, explicit
  timing report, and all-eligible-BAFU only after preflight passes.
- **Data leakage risk**: tail models are sensitive to split design. Mitigation:
  blocked/rolling splits only.
- **False confidence risk**: Flow 13 promotion defaults can pass through with
  empty skill thresholds. Mitigation: configure non-empty experiment thresholds.
- **Dependency/licensing risk**: R/GPL packages must not become runtime
  dependencies of the MIT Python service.

## Exit Criteria

- The benchmark protocol is fixed before model fitting.
- Existing model baselines run over the same all-eligible-BAFU
  station/time/threshold scope.
- The EQRN candidate either passes Flow 13 gates or records exactly where it does
  not fit the contract.
- The final report separates method skill from integration fitness.
- No production dependency, schema, or operational assignment changes are made
  without a follow-up READY implementation plan.

## Dependency Graph

```json
{
  "phases": [
    {
      "id": "phase-1",
      "tasks": ["benchmark-protocol"],
      "parallel": false
    },
    {
      "id": "phase-2",
      "tasks": ["baseline-model-runs"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-3",
      "tasks": ["minimal-eqrn-candidate-shape"],
      "parallel": false,
      "depends_on": ["phase-1"]
    },
    {
      "id": "phase-4",
      "tasks": ["flow-13-isolated-experiment-benchmark"],
      "parallel": false,
      "depends_on": ["phase-2", "phase-3"]
    },
    {
      "id": "phase-5",
      "tasks": ["skill-and-alert-report"],
      "parallel": false,
      "depends_on": ["phase-4"]
    }
  ]
}
```
