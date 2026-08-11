---
status: DRAFT
created: 2026-08-11
plan: 152
title: aquacast pooled (GROUP) probabilistic model integration — onboard an externally-trained multi-resolution FI artifact on control forcing
scope: Integrate hydrosolutions/aquacast pooled models into SAPPHIRE Flow through the existing ForecastInterface boundary as GROUP-scoped artifacts, running on CONTROL forcing and emitting quantile-backed ForecastEnsembles. Two first-class goals — the skill verdict AND daily+sub-daily forecasting capability. Owner selected the multi-resolution (daily + hourly, radiation-free) artifact on 2026-08-11, which makes multi-resolution model support (G5) the critical path, split into prerequisite Plan 153. Covers the feasibility audit, a fail-loud guard against today's silent misread of multi-resolution requirements, an EARLY offline skill spike that de-risks the whole investment before it is made, past-forcing depth for both the daily and hourly windows, an external shim distribution, the missing external-artifact import path and its provenance representation, the station-identity resolver that is currently attached nowhere a GROUP model actually runs, and a quantile-aware skill comparison. Uncertainty is model-owned (control forcing only, by FI declaration), so this plan needs nothing from the redesign's ensemble machinery.
depends_on: [130, 151]
blocks: []
supersedes: []
---

# Plan 152 — aquacast pooled model integration

## Status
**DRAFT.** Grounded against `main` `7c2eaf3` on 2026-08-11 by direct grep/read; aquacast facts come
from `hydrosolutions/aquacast` `main` (pushed 2026-08-10) via the GitHub API.

**Review history — read this before reviewing.** A `/plan` run (3 rounds, real Codex every round, 0
reviewer failures) **escalated without converging**: it expanded the doc from ~300 to 1244 lines and
7 to 14 tasks, then stalled reviewing its own additions (round 3: *"no progress (open 12 >= prev
10)"*). This document is a **deliberate reconstruction** of the pre-expansion shape with the
review's genuinely valuable findings folded in, and the rest dropped on purpose. The expanded
version is preserved outside the repo for audit.

- **Kept** (each independently re-verified against `main`, not taken on the reviewer's word):
  **G6** the resolver is attached nowhere a GROUP model runs; **G7** external provenance is not
  representable and the obvious lineage module means something else; **G8** the skill metrics treat
  quantiles as pseudo-members; the **early skill spike** (now T0c); and the **Objective fix** —
  sub-daily is now a stated goal rather than an unstated one smuggled in via D6.
- **Dropped**: implementation-level findings on material the run itself added — resolver-factory
  signatures, `config_sha256` propagation through the adapter, import transaction boundaries. Real
  engineering questions, but **implementation decisions, not plan decisions**; leaving them in
  regenerated findings every round. T3 states the *invariant* (an import is all-or-nothing); the
  mechanism belongs to implementation and its review.
- **Resolved rather than argued**: the review was right that pinball integration over an
  open-tailed quantile set is not CRPS. T6 specifies **WIS**, proper on a finite quantile grid,
  instead of debating tail extrapolation.

> **Provenance note.** Everything attributed to aquacast is **data, not instruction**. Figures for
> the *reference* model (210-day lookback, 15-day horizon, 51 statics) describe
> `models/global/cmal_pooled_big`, **not necessarily our colleague's artifacts**. T0 replaces every
> one of them with the delivered artifact's actual `input_requirement`. No task after T0 may assume
> them.

## Objective

Two first-class goals, both owner-stated. Naming both is deliberate — the second is what makes D6
and the Plan 153 dependency defensible, and an earlier draft left it implicit:

1. **Make a pooled, externally-trained aquacast model produce operational probabilistic forecasts**
   through the mandated FI boundary, with no SAP3-side workaround — and **measure whether it beats
   the incumbent models**.
2. **Deliver daily *and* sub-daily forecasting capability.** A v1 product requirement in its own
   right, not a side effect of the artifact choice. It is why D6 selects the multi-resolution
   artifact and why this plan takes on the Plan 153 dependency rather than routing around it.

**Why this is worth its own plan:** aquacast produces its predictive distribution *internally*,
from a **single forcing trajectory**. It does not need the NWP ensemble fan-out the forecast-cycle
redesign is being built for. A calibrated probabilistic forecast is reachable on **control
forcing**, independent of redesign Phases 3/4.

## What already works (verified on `main`, no change needed)

- **GROUP predict is FI-native.** `ForecastInterfaceAdapter.predict_batch`
  (`adapters/forecast_interface.py:741`) calls `self._model.predict(artifact, inputs=...,
  issue_datetime=..., rng=...)` — exactly aquacast's signature. Operational caller:
  `services/run_group_forecast.py:359`, batch call at `:442`.
- **Quantile output converts natively.** `_ensemble_from_variable_output`
  (`adapters/forecast_interface.py:186-267`) tries trajectories (`:199`) → quantiles (`:221`) →
  deterministic (`:243`). An aquacast CMAL head declares only `{DETERMINISTIC, QUANTILES}` and
  refuses to fake trajectories, so it lands on `ForecastEnsemble.from_quantiles` (`:228`).
- **Per-station model failure degrades gracefully.** aquacast returns *every* input station, marking
  failures as `FAILURE`; our loop skips a station with no usable output and warns
  (`adapters/forecast_interface.py:812-822`) rather than failing the batch.
- **GROUP scope is modelled end to end**: `StationGroup` (`types/station.py:86`), `GroupModelInputs`
  (`types/model.py:80`), group assignment creation (`services/model_onboarding.py:949`), GROUP
  onboarding validation (`:489`).
- **Statics reach the model.** `static_df` from `basin.attributes`
  (`services/operational_inputs.py:556`), stacked per station
  (`services/run_group_forecast.py:181-209`). The namespace is free-form
  (`types/basin_package.py:164`), so attribute *coverage* is a data question, not a code one.
- **Units are aquacast's problem.** Its adapter converts declared → canonical, including the
  area-based `m³/s → mm/day` — which is why `area` must be among the statics.
- **A pooled artifact maps cleanly**: one `StationGroup`, one GROUP artifact, one `best.pt` blob
  through `deserialize_artifact`.

## Uncertainty ownership (owner decision, 2026-08-11)

**Uncertainty is a per-model property, not a system-wide mode.** A model producing its own
predictive distribution is fed the **control member only**; the NWP fan-out exists for models that
take uncertainty from the forcing.

**This is already the contract, and already declarative.** FI's `FutureKnownVariable.ensemble_mode`
defaults to `SINGLE` (`forecast_interface/input/variable.py:40`); aquacast's
`requirement_from_config` never overrides it; our adapter projects it
(`adapters/forecast_interface.py:474-475`); and Plan 151's `ForcingTrackKey` carries ensemble mode
in the track identity, so a SINGLE requirement projects to a **control track**. No new flag, no
per-model config, no branch.

Consequences, all reducing: operational download fetches only the control member; onboarding
exercises the control shape only; **this plan needs nothing from the redesign's exact-N machinery.**

**Cross-reference — member counts are source-specific (51 ≠ 21).** IFS is 51; ICON-CH2-EPS is **21**.
Nothing in production hardcodes 51 (only a docstring, `services/operational_inputs.py:177`);
MeteoSwiss has its own floor (`adapters/meteoswiss_nwp.py:963-971`) and
`min_operational_ensemble_size` defaults to 20 (`config/deployment.py:123`). The hardcoded-51 risk
is introduced by **Plan 151**, where it is **already tracked as round-0 blocker B0-1**. **No action
here — do not duplicate it.**

## The real gaps

### G1 — Past-forcing depth (owner-confirmed we do not have it)
The assembler reads past forcing over `[issue_time - lookback_steps * time_step, issue_time]`
(`services/operational_inputs.py:412`, `:468-489`), so a long lookback is **expressible** — the gap
is stored data. An AR model needs that depth of **both** past target and past dynamic forcing at
every issue. `operational_inputs` only **warns** on a short lookback (`:443-466`), so an under-fed
window degrades silently rather than announcing itself. **Plan 130** (READY, unimplemented) fixes
the MeteoSwiss tail gap and is a dependency if T0 pins Swiss stations.

### G2 — Radiation forcing — **avoided by D6, retained for the record**
Radiation is not hardcoded in aquacast: `requirement_from_config` derives the requirement from the
trained config's feature roles. `nepal_daily_cmal/from_scratch.yaml` declares
`thermal_net_radiation` + `solar_net_radiation` as `future_dynamic`; `dudh_koshi/from_scratch.yaml`
declares only precipitation + mean_temperature. **D6 selected the radiation-free artifact**, so the
radiation workstream (two variables, ≈210 d history *and* forecast horizon, against a `tp` + `t_2m`
allowlist — Plan 063) is **out of scope**. Retained because a later config change could reintroduce
it, and T0 must confirm the delivered artifact is in fact radiation-free.

### G3 — The entry-point registry cannot construct an aquacast model
`discover_models` constructs each entry point **with no arguments** — `raw_instance = cls()`
(`services/model_registry.py:78-82`; group `"sapphire_flow.models"` at `:23`; current entries
`pyproject.toml:137-143`). aquacast needs `AquacastModel(ModelTemplate.from_yaml(...), device=...)`.
And the adapter computes `data_requirements` from `input_requirement` **at construction**
(`adapters/forecast_interface.py:449`), which derives from that config — so **the config must bind
at import time**, i.e. **one entry point per trained config**.

### G4 — No external-artifact import path, and provenance is not representable
Flow 13 runs scope → register → validate → smoke → assemble → **train** → store → promote → assign
(`flows/onboard_model.py`; `services/model_onboarding.py:1105`). No path registers an
**externally-trained** artifact, and we will not retrain a pooled model on the mac-mini.

Worse, there is nowhere to record what such an artifact *is*:
- `model_artifacts` has no provenance column (`db/metadata.py:907-951`), `ModelArtifactRecord` has
  no provenance field (`types/model.py:292-307`), and `ModelArtifactStore.store_artifact`
  **requires** `training_period_start`, `training_period_end`, `trained_at`
  (`protocols/stores.py:416-427`) — all `nullable=False` (`db/metadata.py:934-936`) — with no
  defined meaning for an artifact we did not train.
- The obvious-looking module means something **else**:
  `store/model_artifact_lineage.py::record_artifact_basin_lineage` (`:33-103`) writes one row per
  **basin the artifact trained on**, to drive the Plan 120 stale-basin retrain SLA (docstring
  `:40-58`). Calling it with *our target* stations would durably assert a falsehood.

*(Found by the `/plan` review; independently verified.)*

### G5 — Multi-resolution is not representable — and today it is silently misread
**The critical path.** Separate it from "sub-daily support":

- **(a) Sub-daily** — running at an hourly `time_step`. Already representable
  (`ModelAssignment.time_step`, `types/station.py:66`); needs *data*, not new types.
- **(b) Multi-resolution within one model** — one model consuming daily **and** hourly branches in a
  single `predict` call, which is what an MTS-LSTM-style aquacast config is. **Not representable
  anywhere in our domain types.**

| Layer | Evidence | Shape |
|---|---|---|
| `ModelDataRequirements` | `types/model.py:262-272` | one flat feature set, **one** `lookback_steps`, **one** `forecast_horizon_steps` |
| `ModelInputs` / `GroupModelInputs` | `types/model.py:121`, `:163` | a single `time_step` |
| Group stacking | `types/model.py:204-207` | **raises** on inconsistent `time_step` |
| Assignment | `types/station.py:66`, `:75` | one `time_step` |

The projection **actively mis-reads** the FI contract: `_iter_dynamic_specs`
(`adapters/forecast_interface.py:827-831`) flattens every `time_step` branch into one stream, and
`:519` sets `supported_time_steps=frozenset(req.dynamic)` — reinterpreting *"requires all these
resolutions"* as *"can run at any one of them"*. Downstream acts on the wrong reading, including
arbitrary selection via **`next(iter(req.supported_time_steps))`**
(`services/model_onboarding.py:295`, `:368`, `:485`; `services/onboarding.py:806`, `:962`).

**The failure is silent.** A multi-resolution artifact onboarded today would have its features
merged, its lookback/horizon max-collapsed as bare step counts (210 daily vs 168 hourly → `210`,
applied at whichever resolution won a `frozenset` coin toss), and be handed a single-resolution
frame. It would produce numbers, and they would be wrong.

**Per `CLAUDE.md` § FI Adherence this is path 1**: the FI expresses multi-resolution correctly
(`InputRequirement.dynamic` is *keyed by* `time_step`); **we** collapse it. The fix belongs on the
SAP3 side — no FI issue.

### G6 — The station-code resolver is attached nowhere a GROUP model actually runs
`predict_batch` resolves every station via `self._station_code(station_id)`
(`adapters/forecast_interface.py:747-749` → `:1113-1119` → `_require_resolver` `:1121-1126`), which
raises `ConfigurationError("station_code_resolver required for GROUP input conversion / train /
predict")` when none is attached. `discover_models()` wraps with `adapt_if_fi(raw_instance)` and
**no resolver** (`services/model_registry.py:83-85`). Exactly **one** site attaches one afterwards:

| Consumer | Call site | Resolver? |
|---|---|---|
| Model onboarding | `flows/onboard_model.py:846-849` | **Yes** |
| Forecast cycle | `flows/run_forecast_cycle.py:1629-1631` | **No** |
| Hindcast | `flows/run_hindcast.py:217-219` | **No** |
| Training | `flows/train_models.py:428-430` | **No** |
| Station onboarding | `services/onboarding.py:785`, `:892` | **No** |

**So a GROUP model that onboards cleanly raises `ConfigurationError` on its first operational
forecast.** And the one resolver that exists maps to `station.code` (`flows/onboard_model.py:93`) —
the network's own code (`db/metadata.py:232`) — **not** the Caravan/GRDC-style key a pooled artifact
is trained on. There is no durable, namespaced station-identity mapping in the schema.

*(Found by the `/plan` review; independently verified. Missing from the pre-review draft, and a hard
blocker for T5.)*

### G7 — The skill metrics treat quantiles as pseudo-members
`services/skill/service.py:49` handles a QUANTILES ensemble by using **quantile values as
pseudo-members**. Downstream metrics then assume equally-weighted members: the rank histogram sets
`n_bins = n_members + 1` (`services/skill/diagrams.py:76-77`), so a 7-quantile forecast yields an
8-bin histogram, and sharpness stacks members directly (`services/skill/service.py:204-207`).

Comparing a 7-quantile aquacast forecast against a 21-member ICON ensemble through pseudo-member
metrics is **not apples-to-apples** — which is exactly what T6 exists to do.
*(Found by the `/plan` review; independently verified.)*

## Non-goals

- **Retraining or fine-tuning in SAP3.** Import-only; FI `train`/`retrain` stay unexercised.
- **Group operational ENSEMBLE fan-out** (`run_group_forecast.py:442` — one batch call, no fan-out).
  Control forcing only; the model's uncertainty is internal.
- **The forecast-cycle redesign per-track path.** Plan 151 keeps GROUP on the legacy superset
  assembler (its D8-group). This plan stays there deliberately.
- **Trajectory output.** Mixture heads cannot produce it and refuse to fake it.
- **Radiation forcing** (G2, avoided by D6).
- **A new FI contract.** Nothing found requires one. If T0 surfaces something the FI genuinely
  cannot express, the resolution is an upstream FI issue — never a SAP3 patch.

## Parallel-session collision map (Plan 151 runs concurrently)

Plan 151 Phase 3 touches `types/forcing_track.py` (new), `adapters/forecast_interface.py`,
`protocols/adapters.py`, `adapters/recap_gateway.py`, `services/operational_inputs.py`,
`services/run_station_forecast.py`, `flows/run_forecast_cycle.py`.

1. **`adapters/forecast_interface.py` — direct overlap.** G5 requires the projection to stop
   flattening `req.dynamic` (`:519`, `:827-831`); 151's T2 adds **per-`time_step` accessors over the
   same structure**. Same seam, two directions. **Do not edit from both tracks.** Plan 153 should
   **build on 151's accessors once they land** — an argument for sequencing 153 after 151 T2. T0b
   also touches this file and must be coordinated.
2. **`services/operational_inputs.py`** — T1's hardening; 151 edits it too. Sequence after, or rebase.
3. **Radiation** — avoided by D6; no longer a coordination point.

## Phases

Every task is red-first, with the standard exit gate: `uv run pytest -q` + `uv run pyright` +
`uv run ruff check`. Multi-model review before READY and post-implementation per `docs/workflow.md`.

### T0 — Feasibility audit against the *delivered* artifacts (no production code)
**A gate, not a formality.** Obtain the artifacts + `config.yaml`, construct the model, and report
as a committed audit doc:

1. **The real `input_requirement`** — per-resolution lookback/horizon, exact variable names + units +
   ensemble modes, target, full static list. Confirm it is radiation-free (G2).
2. **Static coverage** vs `basin_package.static_attributes` for candidate stations. **`area` is
   mandatory** or the `m³/s → mm/day` conversion fails at predict.
3. **Forcing-variable coverage** against our pipeline today.
4. **Historical depth, per resolution** — stored past discharge and past dynamic forcing vs the
   required lookback, **separately for the daily (≈210 d) and hourly (≈168 h) branches**, at the
   declared `issue_hours` cadence. Hourly *observation* availability is likely the binding
   constraint. Produces the T1 scope.
5. **Station identity** — what gauge codes the artifact keys on, and whether a total map from our
   `StationId` exists for the candidate set. Feeds G6/T4.
6. **Operational floors + alert eligibility** for a quantile-backed ensemble
   (`services/model_onboarding.py:437`, `:715`). If it fails, escalate — do not work around it.

**Exit gate:** committed audit + owner decision on the target station set (D2). Heredoc probe per
`CLAUDE.md` § Ad-hoc Analyses, not a checked-in script.

### T0b — Fail loudly on a multi-resolution requirement (ship first, no dependencies)
Small, high-value, independent. Today a multi-resolution `InputRequirement` is silently flattened
(G5) and produces plausible wrong numbers.

**Red-first:** an FI model whose `InputRequirement.dynamic` carries **more than one `time_step` key**
is rejected with a clear `ConfigurationError` naming the resolutions, instead of being flattened.
Sweep the arbitrary `next(iter(req.supported_time_steps))` sites (`services/model_onboarding.py:295`,
`:368`, `:485`; `services/onboarding.py:806`, `:962`) so none can silently pick a resolution.

**Note:** any `ConfigurationError` inside `discover_models`' try block — including from
`_project_requirements` via `adapt_if_fi` (`services/model_registry.py:82`) — is caught per entry
point; the guard must not turn one bad model into a registry-wide blackout.

This converts a silent misread into a loud refusal, and its red test is what Plan 153 later turns
green. *(Touches `adapters/forecast_interface.py` — coordinate with 151 T2.)*

### T0c — Offline skill spike (throwaway; **gates the whole investment**)
**The highest-leverage task in the plan, and it comes near the start.** T6 — the number that says
whether any of this is worth building — otherwise sits at the very end, behind T1, T2, T3, T4 and
Plan 153. The Risks section notes the pooled model may not survive contact with operational forcing.
Finding that out last is the expensive ordering.

**It needs neither Plan 153, the shim, the import path, nor the resolver.** FI's `dynamic:
dict[timedelta, SpatialInputSpec]` **already expresses multi-resolution** — the limitation is purely
SAP3's internal projection layer. So: hand-shape multi-resolution FI `ModelInputs` for the target
stations from historical data, call `AquacastModel.predict()` directly, and score with the T6
quantile-aware metrics. Entirely decoupled from `discover_models()`.

**Exit gate:** a skill number against the incumbents on *operational-like* forcing, and an **owner
go/no-go on committing to T1/T2/T3/Plan 153**. Heredoc probe, deliberately not checked in.

*(This also substitutes for the stepping stone lost when D7 rejected option (C): it needs no
daily-only artifact, only the multi-resolution one we have.)*

### T1 — Past-forcing depth to the required lookback (long pole; data, not code)
**Scope defined by T0.4.** Extend stored historical forcing and past-target coverage so every target
station carries the full lookback at every operational issue, **for both resolutions**. If the target
set is Swiss this **depends on Plan 130** (READY, unimplemented) — land it rather than duplicating it.

Additionally harden the silent-degradation hole: a station with insufficient lookback must yield an
explicit assignment failure, not a warning plus a forecast
(`services/operational_inputs.py:443-466`). **Red-first.** *(Touches `services/operational_inputs.py`
— see collision map.)*

### T2 — `sapphire-aquacast` shim distribution (external repo; closes G3)
A thin package **outside this repo** (Plan 135 decision 3 — no torch in our `pyproject.toml`)
exposing **one zero-argument entry-point class per trained config**:

```toml
[project.entry-points."sapphire_flow.models"]
aquacast_cmal_pooled_daily = "sapphire_aquacast.models:AquacastCmalPooledDaily"
```

Each class binds its `ModelTemplate.from_yaml(...)` + device at construction and declares the
`model_tier` / `alert_eligibility` attributes `_assert_model_classification_declared` requires
(`services/model_registry.py:60-67`). `adapt_if_fi` wraps it at discovery (`:76-84`).

Config↔artifact pinning is D1. **Testable against synthetic single-resolution FI models, so it lands
well before Plan 153.**

### T3 — External-artifact import path + provenance (closes G4)
Two parts:

**(a) Provenance must become representable.** Decide and implement how an artifact we did not train
records what it is — source repo, commit, config hash — given `store_artifact` demands
`training_period_start`/`end`/`trained_at` as non-nullable (`db/metadata.py:934-936`). Do **not**
reuse `record_artifact_basin_lineage`: it asserts *training* basins and would record a falsehood.

**(b) An executable import entry point**, not just a service function: validate the bytes deserialize
via the model's own `deserialize_artifact`, store, record provenance, and promote — **without
entering the training path**. Flow 13 has no artifact/provenance/import inputs today, so this needs a
real flow or CLI boundary plus deployment registration.

**Invariant (implementation owns the mechanism):** an import is **all-or-nothing** — a failure leaves
no artifact row of any status, no changed prior ACTIVE row, no provenance row, and no orphaned file.

**Red-first:** an undeserializable blob fails loudly; a valid import yields a promotable artifact with
external provenance; **an acceptance test from the public boundary proving `train()` is never
called**. Testable with synthetic models — lands before Plan 153.

### T4 — Group onboarding on a disposable database (closes G6)
Isolated experiment DB, artifact location and model ids per Plan 135 decision 7 — **never** the
operational DB. Create the `StationGroup` over the T0 target set; then **close G6**: a durable,
namespaced `StationId → external gauge code` mapping, and resolver attachment at **every** consumer
where a GROUP model runs (forecast cycle, hindcast, training, station onboarding — see the G6 table),
not just model onboarding.

**Red-first:** a station with no mapping raises `ConfigurationError` **at onboarding**, not at predict
time in production; and a GROUP model reaching the operational path has a resolver attached (the test
that fails against today's code).

### T5 — First operational group forecast on control forcing
`run_group_forecast` end to end on the experiment DB: quantile ensembles stored, provenance correct,
per-station failures isolated, **issue-time semantics correct** — horizon 1 is the nowcast, so the
first row's `datetime` equals the issue stamp; do not shift the issue back a day. Validate the
returned grid against the requested issue rather than trusting it.

**Red-first:** a golden test pinning the quantile ensemble shape end to end; a test proving one
station's `FAILURE` does not darken its siblings.

### T6 — Quantile-aware skill comparison vs the incumbents (closes G7)
Hindcast over the target set and compare against the incumbents on alerting-relevant metrics.

**First fix the metrics.** Pseudo-member treatment (`services/skill/service.py:49`) makes a
7-quantile forecast incomparable to a 21-member ensemble: the rank histogram would use 8 bins
(`services/skill/diagrams.py:76-77`). Score quantile forecasts with **WIS (Weighted Interval Score)**
— proper on a finite quantile grid — **not** a pinball integral relabelled as CRPS, which is not
identified because FI forbids quantile levels at 0 and 1 (open tails, always). Validate non-crossing
quantiles before any CDF interpolation, and treat all FI quantile output as open-tailed: there is no
tail-closure metadata in `QuantileData` or `VariableMetadata` to read.

Per Plan 135 decision 8 the verdict is two-dimensional: **method skill** and **integration fitness**,
reported separately.

## Sequencing

Losing the daily-only stepping stone (D7) does **not** stall the plan.

**Immediately, in parallel, independent of Plan 153:** T0 (audit), **T0b** (guard — the only thing
between a multi-resolution artifact and silently wrong numbers), **T0c** (skill spike — gates
everything downstream), T1 (data; long pole), T2 and T3 (exercised against synthetic
single-resolution FI models).

**Waits on Plan 153:** T4, T5, T6 — they need a multi-resolution requirement to be *representable*,
not merely refused.

**Critical path:** Plan 151 T2 → Plan 153 → T4 → T5 → T6. Everything else runs beside it — and
**T0c's number should decide whether that path is walked at all.**

## Phase dependency graph

```json
{
  "phases": [
    { "id": "T0",  "tasks": ["T0"],  "parallel": false },
    { "id": "T0b", "tasks": ["T0b"], "parallel": false },
    { "id": "T0c", "tasks": ["T0c"], "parallel": false, "depends_on": ["T0"] },
    { "id": "T1",  "tasks": ["T1"],  "parallel": false, "depends_on": ["T0"] },
    { "id": "T2",  "tasks": ["T2"],  "parallel": false, "depends_on": ["T0"] },
    { "id": "T3",  "tasks": ["T3"],  "parallel": false, "depends_on": ["T0"] },
    { "id": "T4",  "tasks": ["T4"],  "parallel": false, "depends_on": ["T2", "T3", "plan-153"] },
    { "id": "T5",  "tasks": ["T5"],  "parallel": false, "depends_on": ["T1", "T4"] },
    { "id": "T6",  "tasks": ["T6"],  "parallel": false, "depends_on": ["T5"] }
  ]
}
```

`plan-153` is the external prerequisite (multi-resolution support, D7), itself sequenced after Plan
151's T2. T0b has no dependencies and ships first. **T0c is a decision gate: a poor result should
stop T1/T2/T3 and Plan 153 rather than merely inform them.**

## Risks

- **G5 is the critical path and touches core domain types.** Avoiding radiation moved the cost from
  *data* to *architecture*, which ripples through training, hindcast, onboarding and both forecast
  paths. Mitigation: D7 splits it into Plan 153, sequenced after 151's T2; **T0c de-risks the decision
  to start it at all.**
- **The silent-misread failure mode (G5) is worse than a hard failure** — plausible wrong numbers,
  accepted by onboarding. T0b makes it loud first, and is worth landing even if the rest slips.
- **The pooled model may not survive operational forcing.** Trained on a Caravan/ERA5-Land climatology
  we cannot reproduce; a pooled model on out-of-distribution forcing can be confidently wrong. T0c
  must score on *operational-like* forcing, not the training feed.
- **Silent lookback truncation** produces plausible bad forecasts. Mitigated by T1's hardening.
- **Station-identity mapping is a hidden coupling** — a wrong map silently forecasts the wrong basin.
  Mitigated by T4's fail-at-onboarding test.
- **Sub-daily data availability** — 168 h of hourly forcing *and* hourly observations at every issue,
  ×8/day. Audited separately in T0.4.
- **Concurrent Plan 151** — see the collision map.

## Open items (design forks for the owner)

- **D1 — Where the aquacast `config.yaml` lives.** **(A, recommended)** package data in the shim, one
  entry point per config — version-pins config↔code, but each retrain needs a shim release. **(B)**
  artifact-store sidecar — decouples retrains, but adds a store surface and makes discovery depend on
  DB state. **(C)** artifact metadata/lineage JSONB. *Whichever is chosen, a config change that alters
  the requirement shape must produce a new model id — a same-id config swap can strand an ACTIVE
  artifact.*
- **D2 — Target station set: Swiss or Nepal?** **(A)** Swiss BAFU — validates on data we hold; makes
  Plan 130 the immediate dependency. **(B)** Nepal — the real v1 target, but blocked on onboarding
  (Plan 143) and DHM data. *Changes T1's entire scope; decide before T1 is written.*
- **D3 — Import-only, or must retrain work?** This plan assumes import-only (T3). If v1 needs
  SAP3-side fine-tuning, the FI `train`/`retrain` path and `assemble_group_training_data` must also be
  exercised — a materially larger T3.
- **D4 — Alert eligibility of a quantile-backed ensemble.** If T0.6 finds the floors or alert path
  assume member-backed ensembles, decide between forecast-only-not-alert-eligible for v1, or extending
  the alert path. *Do not work around it in the adapter.*
- **D5 — Uncertainty ownership — RESOLVED (owner, 2026-08-11).** Per-model; control member only for
  self-uncertain models. Already declarative via FI `ensemble_mode`. No design work needed.
- **D6 — Which trained config? — RESOLVED (owner, 2026-08-11): the multi-resolution, radiation-free
  `dudh_koshi` shape.** Daily + sub-daily is a first-class Objective goal, so the multi-resolution
  architecture is wanted on its own merits. *(Rejected: `nepal_daily_cmal` — daily-only but needs
  radiation end-to-end.)*
- **D7 — Multi-resolution sequencing — RESOLVED (owner, 2026-08-11).** Split into prerequisite **Plan
  153**, sequenced after Plan 151's T2. The daily-only stepping stone was **rejected as unavailable** —
  no such artifact exists and we have no daily model of our own. **T0c substitutes for it**, giving the
  same early signal without a new artifact.
- **D8 — A daily-only stand-in later (parked).** aquacast ships an optional `tirex` extra (NX-AI
  TiRex-2, **zero-shot**), which needs no trained artifact. If Plan 153 stalls, a zero-shot daily config
  could unblock T2–T6 validation independently. Costs an NXAI Community License dependency and a
  research detour. **Not scoped here.**

## References

- `docs/design/forecast-cycle-redesign.md` — why GROUP stays on the legacy path in Phase 3.
- `docs/plans/151-...md` (DRAFT, `sapphire-plan151` worktree) — concurrent; see collision map and its
  round-0 blocker B0-1.
- `docs/plans/135-eqrn-offline-model-onboarding-benchmark.md` — DRAFT; decisions 3, 4, 7, 8 reused.
  **Note:** its "use onboarding as the benchmark harness, not a standalone notebook" premise was cheap
  for EQRN, which carried none of G3–G7's cost. T0c deliberately departs from it for the *early*
  signal, while T4–T6 still honour it for the *final* verdict.
- `docs/plans/130-temperature-reanalysis-live-tail.md` — READY, unimplemented; T1 dependency under D2
  option (A).
- `CLAUDE.md` § ForecastInterface Adherence — the FI-gap escalation rule.
- hydrosolutions/aquacast: `docs/operational/fi_integration.md`, `docs/forecast-interface-usage.md`,
  `aquacast/operational/`.
