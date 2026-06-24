# ForecastInterface Adherence — SAP3 Gap Analysis & Decisions

> **Status: DRAFT** — internal planning artifact (SAP3 side). Becomes a phased
> implementation plan in a later pass. No subagent runs from this until promoted to READY.
>
> **Question answered:** is the ForecastInterface (FI) contract already covered/adhered to
> in SAPPHIRE_flow, and how do we make the interface well-defined (enforced, not just
> documented)? Produced from a grill-me session, 2026-06-17.
>
> **Companion docs:** `02-forecast-interface-requirements.md` (index → the FI repo contract)
> and the FI repo's `docs/{model_interface,input_requirement,open_design_questions}.md`.

## 1. Headline finding — not adhered to yet

SAP3 does **not** implement the FI contract today. It has its **own native protocols**
(`StationForecastModel` / `GroupForecastModel` in `protocols/forecast_model.py`), and
`forecastinterface` (PyPI `forecastinterface` v0.1.17, deps polars + pydantic) **is not a
dependency**. An FI model cannot be consumed at all today — SAP3 runs only native models
(`LinearRegressionDaily` etc.). The bridge — the **`ForecastInterfaceAdapter`** — is
**designed (Plan 014) but unbuilt**; Plan 014 is archived as DONE while no adapter code
exists. It must be superseded.

## 2. The two contracts diverge

| Axis | ForecastInterface | SAP3 native protocol |
|---|---|---|
| Requirement decl | `input_requirement: InputRequirement` (targets/dynamic/static; per-variable `unit`, `max_nan`, `aggregation`, product hierarchy) | `data_requirements: ModelDataRequirements` (flat `frozenset[str]` names) |
| `train` | `train(inputs: ModelInputs, *, config, rng)` | `train(data: StationTrainingData, params: ModelParams, rng)` |
| `predict` | `predict(artifact, *, inputs, issue_datetime, rng) -> ModelResult` | `predict(artifact, inputs, rng, prior_state) -> tuple[dict[str, ForecastEnsemble], bytes \| None]` |
| Output | `ModelOutput` (station-keyed `dict[str, dict[str, VariableOutput]]`) | `ForecastEnsemble` (MEMBERS / QUANTILES) |
| Failure | structured `ModelResult` (`ModelSuccess \| ModelFailure`, never raises) | tuple-or-**raise** (SAP3 catches `Exception`) |
| Station id | opaque **str** = gauge **code** | **UUID** `StationId` (+ `StationConfig.code`) |
| Units | first-class on inputs *and* outputs (`Unit` enum) | input: names only; output: canonical strings |
| State | state-free (v0; `StatefulModel` reserved) | `prior_state` / `new_state` bytes |
| Floors | structural: ≥3 quantiles, ≥8 trajectories | runtime `ForecastEnsemble`: ≥7 quantiles w/ P05+P95, ≥1 member |

## 3. Decisions (grill-me, 2026-06-17)

| # | Decision |
|---|---|
| **A1 Adapter architecture** | Keep SAP3 native protocols; add `forecastinterface` as a **pinned dependency**; build a thin **`ForecastInterfaceAdapter`** that wraps an FI model to satisfy SAP3's `GroupForecastModel`/`StationForecastModel`. The adapter is the **single conformance boundary**; SAP3 internals (`ForecastEnsemble`, `ModelDataRequirements`, QC) unchanged. (Plan 014's design.) |
| **A2 Operational GROUP path** | **Build it now**, as one effort with the adapter — the first FI artifact is GROUP-scoped (eastern group). Flow 1: assemble `GroupModelInputs` per group → adapted `predict_batch` → fan per-station results → store. (Currently only in hindcast / Flow 7.) |
| **A3 Input obligations** | Implement **both**: onboarding-time **unit-match** (reject if model's declared input unit ≠ SAP3 canonical unit for that parameter; **no auto-conversion in v1**) + runtime **`max_nan` pre-`predict` gate** (skip/fail stations over tolerance; deliver residual NaNs as-is). |
| **A4 Enforcement (well-defined)** | (1) Pin `forecastinterface` + version-compat check (per-artifact `interface_version` provenance is a Phase-4 FI item — until then, pin the package); (2) a **reusable conformance test suite** (protocol shape via `isinstance`, serialize→deserialize round-trip, determinism under fixed seed, output validity); (3) **extend the Flow 13 onboarding gate** with FI-specific checks — unit-match, **operational floors at integration** (≥7 quantiles w/ tails, ≥20 members), spatial type supported, `targets ⊆ station.forecast_targets`, station-code keying — and **reject loudly**. |

## 4. Gap analysis — covered vs build

| Capability | Today | Gap |
|---|---|---|
| FI contract types | absent (not a dependency) | **Build:** add `forecastinterface` pin; import at the adapter only |
| Adapter (FI model → SAP3 protocol) | designed (Plan 014), **unbuilt** | **Build:** input-bundle construction (SAP3 inputs → `ModelInputs`), output conversion (`ModelOutput` → `ForecastEnsemble`), failure mapping, `isinstance` routing of `RetrainableModel`/`BatchHindcastModel`, station code↔UUID |
| Operational GROUP path (Flow 1) | hindcast only (Flow 7) | **Build:** `GroupModelInputs` assembly + `predict_batch` orchestration + per-station fan-out + store |
| Input units on the wire | not tracked | **Build:** unit-match at onboarding (A3) |
| `max_nan` gate | not implemented (QC filter ≠ gate) | **Build:** per-variable NaN gate pre-`predict` (A3) |
| Operational floors | runtime in `ForecastEnsemble` | **Build:** move to **integration-time** gate (A4); keep runtime validator as a backstop |
| Version compatibility | none | **Build:** pin + version check (A4) |
| Conformance tests | none | **Build:** reusable FI-model conformance suite (A4) |
| Station code↔UUID | UUID-only in model IO | **Build:** adapter maps via `StationConfig.code` |
| Onboarding gate | exists (compat + smoke + skill) | **Extend:** FI-specific checks (A4) |

## 5. Build list (ours) — priority order

1. **Add `forecastinterface` pin** + version-compat check.
2. **`ForecastInterfaceAdapter`** — input bundle, output conversion, failure mapping,
   `isinstance` routing, station code↔UUID. Adapter = conformance boundary.
3. **Operational GROUP path in Flow 1** (A2) — the orchestration half.
4. **Input gates** (A3) — unit-match at onboarding; `max_nan` pre-`predict` gate.
5. **Enforcement** (A4) — conformance test suite + extended Flow 13 gate (incl. floors at
   integration).
6. **Supersede Plan 014** with the resulting implementation plan.

## 6. Mechanical mappings (adapter handles — no decision)

`ModelResult` → SAP3 (raise `ModelOutputError` on total `ModelFailure`; per-station
`VariableStatus.FAILURE`/`PARTIAL` → skip-station + `QcFlag`); `TrajectoryData`→
`from_members`, `QuantileData`→`from_quantiles`, `DeterministicData`→degenerate
single-member, `EpistemicUncertaintyData`→dropped (v0b); `ForecastFlag`→`QcFlag`;
`VariableMetadata`→`ForecastEnsemble` fields.

## 7. External dependencies

- **Sandro's `config` contents (FI Q8)** — owed before `ModelParams ↔ config` is typed;
  `config` stays `Any` until then.
- **SAP3 Flow 9 retraining** (deferred) — before warm-start (`RetrainableModel.retrain`)
  routing is operationally live. Cold `train` works now.

## 8. Cross-references

- `protocols/forecast_model.py`, `types/model.py`, `types/ensemble.py`, `services/run_station_forecast.py`, `flows/run_forecast_cycle.py`, `flows/onboard_model.py`
- `docs/plans/archive/014-forecast-interface-adapter-design.md` — to be superseded
- FI repo: `docs/model_interface.md`, `docs/input_requirement.md`, `docs/open_design_questions.md`
- `00-internal-gap-analysis.md` (the broader Nepal-v1 build list), `02-forecast-interface-requirements.md` (index to the FI contract)
