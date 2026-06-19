---
status: DONE
created: 2026-06-18
completed: 2026-06-18
supersedes: 014
source_of_truth:
  - docs/requirements/03-forecast-interface-adherence.md
  - git log --oneline main..HEAD, commits 596d150..83a0b94
  - tags v0.1.458..v0.1.478
---

# 076 — ForecastInterface adherence implementation record

This records the completed SAP3 ForecastInterface adherence work. It supersedes
Plan 014, whose framing assumed FI's `interface/` module was still missing and
operational GROUP inference was deferred.

## Delivered phases

### P0 — dependency pin and version guard

- Added the `forecastinterface` 0.1.17 dependency pin.
- Added the SAP3-side FI version guard so the adapter boundary fails fast if the
  installed package drifts from the supported interface version.

### P1 — adapter and conformance boundary

- Built `ForecastInterfaceAdapter` as the single FI conformance boundary: an
  FI `ForecastModel` now satisfies SAP3's `StationForecastModel` or
  `GroupForecastModel` protocols through the wrapper.
- Added FI → SAP3 unit mapping, including canonical discharge unit `"m³/s"`.
- Projected FI `InputRequirement` into SAP3 `ModelDataRequirements`.
- Converted SAP3 `StationTrainingData` / `GroupTrainingData` /
  `StationModelInputs` / `GroupModelInputs` into FI `ModelInputs`, including
  train delegation.
- Converted FI `VariableOutput` into SAP3 `ForecastEnsemble` for
  deterministic, quantile, and trajectory outputs.
- Wired `predict()` and `predict_batch()` end to end, including station-code
  key mapping, `ModelFailure` / empty / all-failure output mapping to
  `ModelOutputError`, and reusable conformance tests for protocol shape,
  serialize → deserialize round-trip, fixed-seed determinism, and output
  validity.

### P2 — onboarding and runtime gates

- Added FI-aware compatibility checks for unit match, supported spatial input
  type, and resolvable station codes.
- Added the runtime `max_nan` pre-`predict` gate using FI input requirements.
- Added operational floor enforcement at integration for FI models: at least
  20 trajectory members or at least 7 quantile levels with tails.
- Adapted discovered FI models at the model-discovery/onboarding boundary via
  `adapt_if_fi`.

### P3 — operational GROUP path in Flow 1

- Added GROUP operational input assembly and discovery.
- Added `run_group_forecast` for operational batch prediction, including batch
  validation, per-station fan-out, and `StoreError` propagation.
- Wired Flow 1 to run GROUP models sequentially alongside station-scoped models.
- Added regression coverage for GROUP-only forecasts, non-operational member
  dropping, missing group-store behavior, and station-model plus group-model
  coexistence.

### P4 — e2e smoke and docs

- Added an end-to-end FI adapter capstone smoke test covering conformance,
  serialize → deserialize, station-keyed output, operational floors, and
  failure mapping.
- Re-baselined the FI requirements and synchronized this implementation record
  with the spec and v0 scope docs.

## Decisions and realisations

- **A1 adapter architecture** from
  `docs/requirements/03-forecast-interface-adherence.md` holds: SAP3 keeps its
  native protocols and uses `ForecastInterfaceAdapter` as the single boundary.
  SAP3 internals (`ForecastEnsemble`, `ModelDataRequirements`, QC behavior)
  remain unchanged.
- **A2 operational GROUP path** was built now. FI v0.1.17 ships a complete
  `interface/` module and station-keyed `ModelOutput`, so the old Plan 014
  blocker no longer applies.
- **A3 input obligations** were implemented as onboarding-time unit/spatial/code
  checks plus the runtime `max_nan` gate. SAP3 does not auto-convert units.
- **A4 enforcement** was implemented through the pin/version guard, reusable
  conformance checks, integration-time operational floors, and discovery-time
  FI adaptation.
- The canonical SAP3 discharge unit is the Unicode registry string `"m³/s"`,
  not ASCII `"m3/s"`.
- GROUP station keys are gauge codes resolved through an injected resolver.
  STATION scope uses SAP3's fixed FI station key. This follows FI decision
  1.10 / Q10.
- FI `ForecastFlag` values and `VariableStatus.PARTIAL` are log-only at this
  boundary. There is no `QcFlag` channel in the model-protocol return type, so
  SAP3 QC behavior is unchanged per A1.
- The operational GROUP path is sequential. Parallel `task.map` fan-out remains
  a performance follow-up.
- **B3:** station-model and group-model forecasts coexist. The flow does not
  deduplicate them; both are stored and merged into the alert accumulators.
- Two adversarial-review checkpoints were run: one on the adapter and one on
  the operational GROUP path. The GROUP review caught a production bug where
  `fetch_groups_for_model()` discovered through station-level model assignments
  instead of active `group_model_assignments`; the fix made production match the
  fake store and enabled the operational GROUP path to run.

## Deferred

- Warm-start retrain / FI stateful routing remains deferred to Flow 9 work.
- `ModelParams` ↔ FI `config` typing remains deferred pending Q8.
- Operational GROUP parallelisation with Prefect `task.map` remains deferred.
