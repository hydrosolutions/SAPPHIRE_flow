---
status: DRAFT
created: 2026-08-11
plan: 152
title: aquacast pooled (GROUP) probabilistic model integration — onboard the externally-trained daily `cmal_pool_PT` artifact on control forcing
scope: Integrate hydrosolutions/aquacast pooled models into SAPPHIRE Flow through the existing ForecastInterface boundary as GROUP-scoped artifacts, running on CONTROL forcing and emitting quantile-backed ForecastEnsembles. Owner selected `cmal_pool_PT` on 2026-08-11 — pooled, DAILY-only, discharge + precipitation + temperature only — the sole artifact clearing both the radiation gap (G2) and the multi-resolution gap (G5) at once, at the accepted cost of being the weakest of the three pooled models. This DEFERS Plan 153 and sub-daily out of scope and collapses the critical path to T0 (a human artifact request) → T2/T3 → T4 → T5 → T6. Covers the feasibility audit and colleague question list, a fail-loud guard against today's silent misread of multi-resolution requirements (shipped even though nothing here needs multi-resolution), an early offline skill spike, past-forcing depth for the 210-day daily window, an external shim distribution, the missing external-artifact import path and its provenance representation, the station-identity resolver that is currently attached nowhere a GROUP model actually runs, and a quantile-aware skill comparison. Uncertainty is model-owned (control forcing only, by FI declaration), so this plan needs nothing from the redesign's ensemble machinery.
depends_on: [130]
blocks: []
supersedes: []
---

# Plan 152 — aquacast pooled model integration

## Status
**DRAFT.** Grounded against `main` `7c2eaf3` on 2026-08-11 by direct grep/read; aquacast facts come
from `hydrosolutions/aquacast` `main` (pushed 2026-08-10) via the GitHub API.

**The artifacts are in hand** (owner's Dropbox, `2025-01-BARHKH/models/global/`) and
**`cmal_pool_PT`'s contract is verified from its own `config.yaml`** — see T0.0. T0 is no longer
blocked on a human round-trip, and the plan's central premise is confirmed rather than assumed.

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
  sub-daily is now stated explicitly rather than smuggled in via D6 (and is now explicitly
  *deferred*, per D7).
- **Dropped**: implementation-level findings on material the run itself added — resolver-factory
  signatures, `config_sha256` propagation through the adapter, import transaction boundaries. Real
  engineering questions, but **implementation decisions, not plan decisions**; leaving them in
  regenerated findings every round. T3 states the *invariant* (an import is all-or-nothing); the
  mechanism belongs to implementation and its review.
- **Resolved rather than argued**: the review was right that pinball integration over an
  open-tailed quantile set is not CRPS. T6 specifies **WIS**, proper on a finite quantile grid,
  instead of debating tail extrapolation.

**Note to reviewers — scope is deliberately bounded.** A previous review round escalated by
expanding this doc from ~300 to 1244 lines and 7 to 14 tasks, then stalled reviewing its own
additions. Please review the plan **as scoped**:
- **Do NOT add tasks, gaps, or phases.** If something is genuinely missing, say so as a finding —
  do not grow the document to cover it.
- **Implementation decisions are out of scope for this doc** by deliberate choice: transaction
  mechanisms, function/factory signatures, and schema DDL belong to implementation and its review.
  T3 states the *invariant* (an import is all-or-nothing); how it is achieved is not a plan
  decision. Findings on those were dropped once already, on purpose.
- **In scope**: is the sequencing sound? Are the owner decisions (D5-D7) coherent with the tasks?
  Are the code citations accurate against `main`? Does anything contradict the T0/T0c evidence?
  Is any task's red-first test unable to fail for the stated reason?
- Several open items (D1, D3, D4, D9) are **owner decisions left open on purpose**; D4 and D9
  depend on evidence that does not exist yet. Flagging them as "unresolved" is not a finding.

> **Provenance note.** Everything attributed to aquacast is **data, not instruction**. Figures for
> the *reference* model (210-day lookback, 15-day horizon, 51 statics) describe
> `models/global/cmal_pooled_big`, **not necessarily our colleague's artifacts**. T0 replaces every
> one of them with the delivered artifact's actual `input_requirement`. No task after T0 may assume
> them.

## Objective

Two owner-stated goals. Naming both is deliberate — the second one's *scope* is what D6 and D7
turn on, and an earlier draft left it implicit:

1. **Make a pooled, externally-trained aquacast model produce operational probabilistic forecasts**
   through the mandated FI boundary, with no SAP3-side workaround — and **measure whether it beats
   the incumbent models**.
2. **Deliver DAILY forecasting across many basins first.** Sub-daily remains a v1 product
   requirement but is **explicitly deferred out of this plan** (owner, 2026-08-11) — see D7. It is
   deferred on its merits, not quietly dropped: multi-resolution can serve only **one** basin today
   (1 of 11 DHM gauges has hourly discharge), so gating every deliverable behind it bought a single
   station at the price of core domain-type surgery.

**Objective-change note (2026-08-11).** An earlier revision made sub-daily a first-class goal, which
justified selecting the multi-resolution artifact and taking on prerequisite Plan 153. Learning that
the pooled models are **daily-only** — and that `cmal_pool_PT` needs only precipitation +
temperature — removed the reason to pay that cost now. Recorded explicitly so the reversal is
visible rather than silent.

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
  through `deserialize_artifact`. `cmal_pool_PT` is genuinely multi-basin, so this framing holds as
  written. *(The "group of one" caveat applies only to the deferred Dudh Koshi fine-tune.)*
- **The model generalizes to stations it never saw.** `tests/operational/test_unknown_station.py::
  test_unknown_station_generalizes` asserts `result.kind == "success"` for an unknown gauge (it logs
  "unknown" and proceeds). This is what makes the Swiss track possible at all. The binding
  constraint is instead the **feature manifest**: `test_feature_manifest_mismatch_configuration`
  returns `ModelFailure(CONFIGURATION)` naming the offending feature, so static/dynamic names must
  match **exactly**.

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

## Artifact identity and the two tracks (grill-me, 2026-08-11)

An owner grill-me settled what we are actually integrating. This section supersedes any earlier
reading of "pooled".

### The selected artifact: `cmal_pool_PT` (owner, 2026-08-11)

Three **pooled** models exist (owner-reported, from the colleague's runs). These are the donors:

| Model | Forcing required | Resolution | Fits our pipeline today | Skill (owner-reported) |
|---|---|---|---|---|
| `cmal_pool_20s_no_wd` | precip + temp + radiation *(assumed; contract unverified)* | daily | ✗ needs G2 | **globally best**, relatively good for Nepal |
| `cmal_pooled_big` | precip + temp + **thermal_net_radiation + solar_net_radiation** *(confirmed)* | daily | ✗ needs G2 | best for Dudh Koshi, globally worse than `20s_no_wd` |
| **`cmal_pool_PT`** ← **SELECTED** | **discharge + precipitation + temperature_2m only** | daily | ✓ **yes** | weakest of the three |

**Confirmed from committed configs**: every `configs/global_train/daily/*.yaml`
(`cmal_pooled`, `cmal_pooled_big_capacity_ens`, `cmal_pooled_muon`) and
`configs/kaz_zeroshot/cmal_pool_big_capacity.yaml` is **daily-only, 210 d lookback, 15 d horizon**,
and requires **both radiation variables**. `cmal_pool_PT` has **no committed config** — its contract
is owner-reported and **T0 must verify it**.

**Why PT wins**: its forcing is exactly our v0 NWP allowlist (`tp` + `t_2m`, Plan 063), it is
**pooled** so it generalises zero-shot to Swiss *and* Nepal basins
(`test_unknown_station_generalizes`), and it is **daily** so it needs no multi-resolution support.
It is the only artifact that clears G2 and G5 simultaneously. The cost is skill — accepted, on the
reasoning that a weaker model running today beats a better one blocked behind a forcing workstream
and core domain-type surgery. **T6 measures whether that trade was right.**

Note the pooled models are also **daily-only and genuinely multi-basin**, so the plan's original
GROUP framing — one artifact serving many stations — is correct again, and G6's resolver work
matters *more*, not less. The "group of one" caveat below applies only to the Dudh Koshi fine-tune,
which is no longer the near-term target.

### Background: the fine-tune family (deferred, retained for the sub-daily phase)

**Neither Nepal config is itself pooled.** Both are built *from* a pooled pretrained donor
(`nepal_daily_cmal` refers to "the 13.7k-basin pretrain"; a recent aquacast commit reads
"13.7k -> 17.0k pool"). `aquacast/operational/model.py:505-511` derives scope from the config:
`gauge_ids` of length exactly 1 → STATION, otherwise GROUP.

| Artifact | Scope reported | Contract | Serves |
|---|---|---|---|
| **Dudh Koshi fine-tune** (delivered) | **GROUP** — scopes via `basins_files`, so `gauge_ids` is `None` | multi-res daily+hourly, 210 d/168 h, 3 d/72 h, precip + temp, **no radiation** | **one basin** (`configs/basins/dudh_koshi.txt` = `nepal_20010`) |
| **The donor** (to request) | GROUP | same contract (inferred — `_lstm_cov_feed.yaml` injects donor weights under an "identical feature contract"; a manifest mismatch hard-fails) | ~13.7k basins |
| `nepal_daily_cmal` (not chosen) | STATION — "one gauge per sweep via `--gauge-ids`" | daily-only, 15 d, **requires radiation** | one gauge per model |

**Consequences, all owner-ratified:**

- **The pilot artifact is a group of ONE.** It declares GROUP scope, so onboarding takes the GROUP
  branch (`services/model_onboarding.py:489`) and the resolver gap (G6) still applies — but the
  `StationGroup` has a single member. The plan's GROUP framing is right for the **destination**, not
  for the pilot's economics. **T4 must verify the degenerate group-of-one case explicitly.**
- **Production target**: one multi-basin fine-tune across Nepal, with the donor as its base.
- **Only 1 of 11 DHM gauges has hourly discharge.** `configs/regions/nepal.yaml`: "DHM, 11 gauges;
  **one** with native sub-daily data" / "the **sole** native hourly discharge (`nepal_20010`)". The
  hourly branch needs hourly *observed* discharge (`is_target_in_past: true`, `past_dynamic:
  [discharge]` at hourly), so multi-resolution can serve exactly one basin today — **which is why
  D7 defers it**. It is justified by DHM's deployment roadmap, not by today's coverage — owner: more stations are
  coming; daily-only sites are a documented **backup tier** (a lower-priority assignment in the
  fallback chain, which is architecturally clean). *Open: a daily-only backup on the
  `nepal_daily_cmal` contract would reintroduce radiation (G2) — a radiation-free daily config
  would be needed instead.*
- **Trained on ERA5-Land reanalysis.** `configs/regions/nepal.yaml`: "ERA5-Land daily is the ONLY
  forcing (Nepal ships no native daily meteorology)". Operationally we feed NWP. This makes the
  out-of-distribution-forcing risk **concrete, not hypothetical**.

**Two tracks, run in parallel (owner, 2026-08-11):**

1. **Swiss — interim, unblocks now.** Apply the model to Swiss basins **directly, not fine-tuned**.
   Feasible because the model generalizes to unknown stations, our basin package is HydroATLAS-derived
   (Plan 117), the hourly branch needs only 168 h ≈ 7 days of hourly discharge, and Swiss daily
   discharge history is deep. **What it proves depends on which artifact runs it** — see T0c.
2. **Dudh Koshi — the real target.** Requires Nepal onboarding: no Nepal station config exists in
   the repo, **Plan 143 is unimplemented**, and the DHM questionnaire is unanswered. Proceeds in
   parallel; it is the source of the *trustworthy* skill verdict.

**Artifact availability — we do not have either.** Verified 2026-08-11: no `models/` tree and no
`.pt`/`.ckpt` in the aquacast repo, `.gitignore` excludes `/experiments/`, no GitHub releases or LFS
assets, no weights repo in the org. `models/global/cmal_pooled_big` is a path in the colleague's
workspace. **Both artifacts are a human ask — this is exactly what T0 is gated on.**

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
**No longer the critical path** (owner, 2026-08-11): `cmal_pool_PT` is daily-only, so this plan does
not need multi-resolution and **Plan 153 is deferred**. The gap is documented in full because it is
real, unchanged, and **returns the moment sub-daily does** — and because **T0b's fail-loud guard is
now *more* important, not less**: with support deferred, the only thing preventing a multi-resolution
artifact from being silently mis-onboarded is that guard. Ship T0b even though nothing in the
near-term path needs multi-resolution.

Separate it from "sub-daily support":

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

### G8 — Statics are declared in Caravan names; we store raw HydroATLAS codes
aquacast declares its 51 statics in **Caravan** names; our basin package carries **raw HydroATLAS**
codes. `_static_inputs` (`adapters/forecast_interface.py:1059-1071`) raises `ConfigurationError` on
any name it cannot find, so **all 22 mismatched statics would be reported missing at every predict**
— the model would never run. Verified against `tests/fixtures/basin_static/nepal-dhm-basins/`
(92 catalog features / 93 parquet columns): 29 of 51 already match, 22 are a pure rename with a
counterpart we hold. **Owner-confirmed the map on 2026-08-11.** Closed by T1b.

## Non-goals

- **Retraining or fine-tuning in SAP3.** Import-only; FI `train`/`retrain` stay unexercised.
- **Group operational ENSEMBLE fan-out** (`run_group_forecast.py:442` — one batch call, no fan-out).
  Control forcing only; the model's uncertainty is internal.
- **The forecast-cycle redesign per-track path.** Plan 151 keeps GROUP on the legacy superset
  assembler (its D8-group). This plan stays there deliberately.
- **Trajectory output.** Mixture heads cannot produce it and refuse to fake it.
- **Radiation forcing** (G2, avoided by D6 — `cmal_pool_PT` needs none).
- **Multi-resolution support and sub-daily forecasting** (G5 / Plan 153) — **deferred** by D7, not
  cancelled. T0b's fail-loud guard still ships so the deferral is safe.
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
**A gate, not a formality**, and it is **blocked on a human round-trip** — we hold neither artifact.
A wrong question list costs a full round-trip, so it is pinned here.

**T0.0 — the colleague request (send first).**

**T0.0 — ARTIFACTS LOCATED AND CONTRACT VERIFIED (2026-08-11).** The models are on the owner's
Dropbox — `.../2025-01-BARHKH/models/global/` — holding `cmal_pool_PT`, `cmal_pool_20s_no_wd`,
`cmal_pooled_big`, `cmal_2`, and a `README.md`. Files are materialised (PT's `checkpoints/best.pt`
is 5,087,101 bytes), so **T0 is no longer blocked on a human round-trip.**

**`cmal_pool_PT` verified contract** (read directly from its `config.yaml`, superseding every
owner-reported description in this plan):

| Property | Value |
|---|---|
| resolutions | `[daily]` — **daily only**, confirming D6/D7 |
| window | `lookback_days: 210`, `forecast_days: 15`, `issue_hours: [0]` (once daily, 00Z) |
| target / past | `targets: [discharge]`, `is_target_in_past: true`, `past_dynamic: [discharge]` |
| **future_dynamic** | **`[precipitation, mean_temperature]` — NO radiation** ✓ |
| statics | **50** — the pooled-big set of 51 **minus `degree_of_regulation`** |
| inference | `strategy: {kind: point}`, `n_aleatoric: 200`, `quantile_levels: [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95]` |
| training pool | 12,952 train / 1,500 test basins |

**Consequences:**
- The plan's premise **holds as written** — PT's forcing is exactly our `tp` + `t_2m` allowlist.
- **The rename map is 21 entries, not 22** (T1b): PT drops `degree_of_regulation` (`dor_pc_pva`).
- `strategy: {kind: point}` means **no epistemic spread** — PT emits DETERMINISTIC + 7 QUANTILES,
  precisely the `ForecastEnsemble.from_quantiles` path (`adapters/forecast_interface.py:221-228`).
- `inference.stride: 3` is present but **the FI adapter forces stride 1**, so it is inert.

**Skill baseline (from the models' `README.md`, owner's colleague):**
- `cmal_pooled_big`: ungauged spatial holdout (750+ basins) **median NSE 0.52**; "uncertainty bands
  generally a bit too wide, especially for short lead times".
- `cmal_pool_20s_no_wd`: less regularisation → **ungauged median NSE 0.56** globally, but
  **"slightly worse performance on the Nepal basins"**. *This materially weakens the case for ever
  paying the radiation cost to get the globally-best model: it is not the best model for our target
  region.*
- `cmal_pool_PT`: "a quick train on only precipitation and temperature_2m … **worse** than the other
  models, but might be useful if the other forcing inputs are not available" — which is exactly our
  situation. No ungauged-holdout figure given; PT's own `metrics.csv` final epoch reads
  `val_nse_median_daily = 0.680` on 998 **gauged** validation basins, which is **not** comparable to
  the 0.52/0.56 ungauged numbers. **T0c produces our own comparable number.**

**Potentially major, needs verifying (T0):** the README says the model "can use past discharge if it
is available but **also can run without it (ungauged mode)**", and aquacast has a
`predict --withhold-past-discharge` path. If that applies to PT, the **210-day past-discharge
requirement is optional**, which would substantially shrink G1 for stations lacking discharge
history. Do not assume it — PT's config sets `is_target_in_past: true`, so ungauged is a
*predict-time* option, not the configured default.

**Still worth asking the colleague** (no longer blocking): PT's ungauged-holdout NSE, so its gap to
0.52/0.56 is a number rather than "worse".

*(Superseded — the original question list is preserved below for the record; items 1, 2, 5 and 6 are
now answered by the artifacts themselves.)*

**Most of the original list was answered without the colleague (owner + repo, 2026-08-11): pool
size ~17,007 basins; `no_wd` = no weight decay, i.e. a training hyperparameter, so `20s_no_wd`
almost certainly shares the radiation-requiring contract; cadence `issue_hours: [0]` (once daily at
00Z); the 51 statics enumerated and the rename map CONFIRMED (T1b); NWP-fed degradation expected.
Only the following remain.**

*Artifacts — `cmal_pool_PT` is the one we need first*
1. Please send **`cmal_pool_PT`** — **exactly two files** suffice (aquacast's own
   `docs/operational/fi_integration.md`: "The adapter needs exactly two of these files"):
   **`config.yaml`** and **`checkpoints/best.pt`**. Paths on shared storage are fine if we can read
   them. It is the only one
   whose forcing (discharge + precipitation + `temperature_2m`) matches our operational pipeline
   today — the others need radiation, which we do not carry.
2. **PT's exact contract — self-resolving on receipt** (owner, 2026-08-11): we read
   `input_requirement` by constructing the model from its `config.yaml`. No question needed; T0
   verifies. **Until then every statement about PT's inputs in this plan is owner-reported, not
   verified.**
3. **PT's skill gap** vs `cmal_pool_20s_no_wd`, globally and for Nepal. **No Swiss/Alpine numbers
   exist yet** (owner) — T0c produces the first Swiss evidence, so it is measured against *our
   incumbents*, not against a known PT-vs-`20s` baseline.
4. How many basins is PT pooled over, and does its config scope via `gauge_ids` or `basins_files`?
   (Determines whether it reports STATION or GROUP on our side.)
5. Does `cmal_pool_20s_no_wd` in fact require both radiation variables? (We assumed so from the
   other pooled configs but never verified — it changes whether the radiation workstream would buy
   us the *best* model or merely a better one.)
6. What does **`20s`** denote — 20 static features? If PT uses a reduced static set, that materially
   eases our static-coverage problem.

*Contract*
4. Exact `input_requirement`: resolutions, lookback/horizon **per resolution**, exact feature names
   + units, full **static** list. (We verify by construction; this confirms intent.)
5. Statics: **does PT use the same 51-static set** as the other pooled configs, or a reduced one?
   (The Caravan↔HydroATLAS rename map is confirmed — T1b — so only the *set* is open. `area` must be
   in it: `m³/s → mm/day` fails without it.)
6. **Does the hourly branch require hourly *observed* discharge**, or can it run on daily obs +
   hourly forcing? We read `is_target_in_past: true` + `past_dynamic: [discharge]` as requiring
   hourly obs — which confines multi-resolution to `nepal_20010` today. Confirm or correct.

*Applicability*
7. Zero-shot on Swiss basins: known caveats — statics namespace, normalization, expected degradation?
8. Training used **ERA5-Land reanalysis**; operationally we feed NWP (ICON-CH2-EPS / IFS). Expected
   degradation, and any recommended bias handling?
9. `issue_hours: [0, 3, …, 21]` — is 8 issues/day the intended operational cadence?

*Forward*
10. For the Nepal multi-basin fine-tune: which gauges, and what would it take to get hourly discharge
    beyond `nepal_20010`?

**Then, on the delivered artifacts**, construct the model and report as a committed audit doc:

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

**It needs neither the shim, the import path, nor the resolver.** FI's `dynamic:
dict[timedelta, SpatialInputSpec]` **already expresses multi-resolution** — the limitation is purely
SAP3's internal projection layer. So: hand-shape multi-resolution FI `ModelInputs` for the target
stations from historical data, call `AquacastModel.predict()` directly, and score with the T6
quantile-aware metrics. Entirely decoupled from `discover_models()`.

**Run it on the Swiss track** (the Nepal target is not onboarded). **`cmal_pool_PT` being pooled is
what makes this worth doing**: generalising to unseen basins is what a pooled model is *for*
(`test_unknown_station_generalizes`), so a Swiss zero-shot run is a **genuine skill signal**, not
merely a smoke test. That was not true of the Dudh Koshi fine-tune, where a Swiss run would have
measured single-basin transfer degradation and proved integration only.

**Score on operational-like forcing (NWP), not the ERA5-Land feed PT was trained on** — otherwise
the number flatters the model on exactly the axis the Risks section flags.

**Exit gate:** integration proven end to end, a skill number against the incumbents, and an **owner
go/no-go on committing to T1/T2/T3**. A poor number here should stop the plan — that is the whole
point of running it third rather than last.

### T0c RESULT — the model runs on a SAPPHIRE-shaped feed (executed 2026-08-11)

`cmal_pool_PT` was constructed from its `config.yaml`, `best.pt` deserialized, and `predict()` run
against a **synthetic SAPPHIRE-shaped feed** (one station, 210 daily past steps ending at issue−1,
15 future forcing steps starting at the issue, 50 statics). Throwaway heredoc probe per
`CLAUDE.md` § Ad-hoc Analyses; the aquacast env was installed in a scratch clone, **not** added to
this repo.

**Confirmed — every output-path claim in this plan is now empirical, not argued:**

| Check | Result |
|---|---|
| construct + `deserialize_artifact` | OK → `ModelBundle` |
| `artifact_scope` | **`ArtifactScope.GROUP`** ✓ |
| statics | **50** ✓ |
| past_known (product `aquacast`) | `discharge`, `precipitation`, `mean_temperature` — all `lookback=210`, **`max_nan=0`** |
| future_known | `precipitation`, `mean_temperature`, `steps=15`, **`ensemble_mode=SINGLE`** ✓ (control-only, by declaration) |
| target | `discharge`, `mm/day` |
| **`predict()`** | **`kind=success`**, `status=SUCCESS`, 0 rejected samples |
| output representations | `det=True, quant=True, traj=False, epi=False` → **exactly the `from_quantiles` branch** (`adapters/forecast_interface.py:221-228`) |
| quantile levels | `[0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95]`, 15 rows |
| issue semantics | `forecast_horizon=15`, `offset=1`, **first row's `datetime` == issue stamp** (nowcast) ✓ |
| AR channel | **consumed** — perturbing past discharge 1.0 → 50.0 moves the median path ~0.95 → ~41 |
| target product namespace | **not sensitive** — identical results under the declared `aquacast` product and under `obs` |

**Two findings that change the plan:**

1. **`max_nan=0` on a 210-step window, for all three past variables.** Zero NaN tolerance: a single
   gap in 210 consecutive days of discharge, precipitation *or* temperature rejects the window, and
   our own pre-`predict` gate (`_variables_over_nan_tolerance`,
   `adapters/forecast_interface.py:629-662`) would drop the station before the model ever sees it.
   **This raises G1 from "we need 210 days of history" to "we need 210 days of GAP-FREE history at
   every issue, for three variables."** T1 must audit gap structure, not merely depth — and the
   fallback chain must be expected to carry stations that fail it.
2. **Ungauged mode is NOT a way out of G1.** Omitting past discharge entirely does not degrade
   gracefully — it **raises** `ColumnNotFoundError: unable to find column "discharge"` from
   `build_qc` → `compute_annual_maxima`, i.e. an unanticipated crash rather than a returned
   `ModelFailure`. So the README's "ungauged mode" is not reachable by withholding the FI input; it
   needs a config change (`data.quality_control`) or the `predict --withhold-past-discharge` path.
   **G1 stands at full 210-day depth.** *(Worth raising with the colleague: per `CLAUDE.md` § FI
   Adherence an anticipated failure should be RETURNED, not raised — this looks like an FI-contract
   deviation on the aquacast side, and is a candidate upstream issue rather than a SAP3 workaround.)*

**Not yet proven** (T0c's remaining scope): a skill number on real Swiss data with operational NWP
forcing. The mechanics are proven; the value is not.

### T1 — Past-forcing depth to the required lookback (long pole; data, not code)
**Scope defined by T0.4.** Extend stored historical forcing and past-target coverage so every target
station carries the full lookback at every operational issue, **for both resolutions**. If the target
set is Swiss this **depends on Plan 130** (READY, unimplemented) — land it rather than duplicating it.

**Scope raised by T0c: `max_nan=0` means GAP-FREE, not merely deep.** Audit gap structure across
210 days for discharge, precipitation and temperature per station — a single missing day disqualifies
the issue. Expect the fallback chain to carry stations that fail this, and size how often that is.

Additionally harden the silent-degradation hole: a station with insufficient lookback must yield an
explicit assignment failure, not a warning plus a forecast
(`services/operational_inputs.py:443-466`). **Red-first.** *(Touches `services/operational_inputs.py`
— see collision map.)*

### T1b — Caravan↔HydroATLAS static alias map (closes G8)
**Owner-confirmed 2026-08-11.** aquacast declares statics in **Caravan** names; our basin package
carries **raw HydroATLAS codes**. Verified against
`tests/fixtures/basin_static/nepal-dhm-basins/`: 29 of the model's 51 statics already match exactly
(`area`, `p_mean`, `frac_snow`, `high_prec_*`, `low_prec_*`, all 22 `glc_pc_s*`); the other **22 are
a pure rename**, every one with a counterpart we already hold:

`slope`→`slp_dg_sav`, `stream_gradient`→`sgr_dk_sav`, `lake_fraction`→`lka_pc_sse`,
`degree_of_regulation`→`dor_pc_pva`, `air_temperature`→`tmp_dc_syr`, `precip_annual`→`pre_mm_syr`,
`pet_annual`→`pet_mm_syr`, `aet_annual`→`aet_mm_syr`, `aridity_index`→`ari_ix_sav`,
`climate_moisture_index`→`cmi_ix_syr`, `snow_cover`→`snw_pc_syr`, `snow_cover_max`→`snw_pc_smx`,
`glacier_fraction`→`gla_pc_sse`, `cropland_fraction`→`crp_pc_sse`, `pasture_fraction`→`pst_pc_sse`,
`clay_fraction`→`cly_pc_sav`, `silt_fraction`→`slt_pc_sav`, `sand_fraction`→`snd_pc_sav`,
`soil_organic_carbon`→`soc_th_sav`, `soil_water_content`→`swc_pc_syr`, `karst_fraction`→`kar_pc_sse`,
`irrigated_fraction`→`ire_pc_sse`.

**Without this the model fails at every predict**: `_static_inputs`
(`adapters/forecast_interface.py:1059-1071`) computes `missing = static_names - set(static.columns)`
and raises `ConfigurationError` — all 22 would be reported missing.

**Approach — additive aliasing (recommended).** Emit the Caravan aliases as **additional columns**
alongside the HydroATLAS canonicals when building the static frame. Purely additive: our canonical
namespace and the `feature_catalog.json` / `required_by_models` seam
(`services/basin_importer.py:185`) are untouched, `_static_inputs` selects only what the model asks
for so extra columns are inert, and — importantly — **it needs no change to
`adapters/forecast_interface.py`**, avoiding the Plan 151 collision. *(Rejected: renaming at import,
which would churn our canonical namespace; or an alias layer inside the FI adapter, which collides
with 151's T2.)*

**Red-first:** a static frame carrying only HydroATLAS names fails with all 22 listed as missing
(proves the gap); with aliasing it resolves. Pin the map in one place with a test asserting all 51
of the model's declared statics resolve.

**Resolved by T0 (2026-08-11):** `cmal_pool_PT` uses **50** statics — the pooled set minus
`degree_of_regulation`. So the map PT actually needs is **21 renames**, and every one is covered by
names our basin package already carries. `dor_pc_pva` stays mapped anyway for the radiation models.

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
well ahead of the artifact arriving.**

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
called**. Testable with synthetic models — lands ahead of the artifact arriving.

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

**Selecting `cmal_pool_PT` collapsed the critical path.** The old chain
(`Plan 151 T2 → Plan 153 → T4 → T5 → T6`) existed *only* because the artifact was multi-resolution.
A pooled daily model needs none of it: **Plan 153 is deferred, and nothing in this plan waits on
Plan 151.**

**The critical path is now just: T0 (colleague ask) → T2/T3 → T4 → T5 → T6** — and its longest pole
is the **human round-trip in T0.0**, not engineering.

**In parallel, all independent:**
- **T0b** — the multi-resolution fail-loud guard. Nothing here needs multi-resolution, which is
  *precisely* why the guard must ship: it is now the only protection against a multi-res artifact
  being silently mis-onboarded later.
- **T1** — past-forcing depth. Still the long engineering pole: 210 days of daily discharge +
  precipitation + temperature per station. Swiss depth we largely hold; Nepal follows onboarding.
- **T2 / T3** — shim and import path; exercised against synthetic models, no artifact needed.

**Deferred out of this plan:** Plan 153 (multi-resolution) and sub-daily, returning when DHM hourly
coverage makes them serve more than one basin. **Plan 151 is now only a file-level coordination
concern** (T0b and T1 touch files it edits) — not a dependency.

## Phase dependency graph

```json
{
  "phases": [
    { "id": "T0",  "tasks": ["T0"],  "parallel": false },
    { "id": "T0b", "tasks": ["T0b"], "parallel": false },
    { "id": "T0c", "tasks": ["T0c"], "parallel": false, "depends_on": ["T0"] },
    { "id": "T1",  "tasks": ["T1"],  "parallel": false, "depends_on": ["T0"] },
    { "id": "T1b", "tasks": ["T1b"], "parallel": false, "depends_on": ["T0"] },
    { "id": "T2",  "tasks": ["T2"],  "parallel": false, "depends_on": ["T0"] },
    { "id": "T3",  "tasks": ["T3"],  "parallel": false, "depends_on": ["T0"] },
    { "id": "T4",  "tasks": ["T4"],  "parallel": false, "depends_on": ["T2", "T3"] },
    { "id": "T5",  "tasks": ["T5"],  "parallel": false, "depends_on": ["T1", "T4"] },
    { "id": "T6",  "tasks": ["T6"],  "parallel": false, "depends_on": ["T5"] }
  ]
}
```

**No `plan-153` edge** — deferred with sub-daily (D7). No Plan 151 edge either; that relationship is
now file-level coordination only. T0b has no dependencies and ships first. **T0c remains a decision
gate: a poor result should stop T1/T2/T3, not merely inform them.**

## Risks

- **We deliberately chose the weakest of three models.** `cmal_pool_PT` was selected for pipeline fit,
  not skill. If T6 shows it does not beat the incumbents, the honest reading is *"the cheap model is
  not good enough"* — **not** *"aquacast is not good enough"*; `cmal_pool_20s_no_wd` remains untested
  behind the radiation workstream. T0.0 q3 asks for the skill gap so this is quantified before we
  build, and T6 must report the verdict against **PT specifically**, never against the model family.
- **PT's contract is owner-reported, not verified.** Its config is not committed; everything about its
  inputs comes from a one-line description. If T0 finds it needs anything beyond precipitation +
  temperature, the plan's whole premise moves. **T0 must construct it and read `input_requirement`
  before any other task starts.**
- **The silent-misread failure mode (G5) is worse than a hard failure** — plausible wrong numbers,
  accepted by onboarding. T0b makes it loud first, and is worth landing even if the rest slips.
- **Degradation from ERA5-Land training to NWP-fed operation is EXPECTED, not hypothetical**
  (owner-confirmed 2026-08-11; `configs/regions/nepal.yaml`: "ERA5-Land daily is the ONLY forcing").
  A pooled model on out-of-distribution forcing can be confidently wrong. **T0c and T6 must score on
  operational-like NWP forcing, never the ERA5-Land training feed** — otherwise the number flatters
  the model on exactly the axis we know is weak. If degradation proves large, bias correction of the
  NWP forcing toward the training climatology becomes a follow-on (see D9); it is explicitly NOT
  scoped here.
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
- **D2 — Target station set — RESOLVED (owner grill-me, 2026-08-11): BOTH, as parallel tracks.**
  **Dudh Koshi is onboarded as the real target** (accepting the Plan 143 + DHM dependency), and
  **while that proceeds the model is applied to Swiss data directly — not fine-tuned**. See
  § Artifact identity. T1's scope therefore splits: Swiss depth (which we largely hold; Plan 130
  applies) unblocks T0c now, Nepal depth follows onboarding. *(Rejected: Swiss-only via a Swiss
  fine-tune — an extra ask on the colleague for a basin we do not ultimately target.)*
- **D3 — Import-only, or must retrain work?** This plan assumes import-only (T3). If v1 needs
  SAP3-side fine-tuning, the FI `train`/`retrain` path and `assemble_group_training_data` must also be
  exercised — a materially larger T3.
- **D4 — Alert eligibility of a quantile-backed ensemble.** If T0.6 finds the floors or alert path
  assume member-backed ensembles, decide between forecast-only-not-alert-eligible for v1, or extending
  the alert path. *Do not work around it in the adapter.*
- **D5 — Uncertainty ownership — RESOLVED (owner, 2026-08-11).** Per-model; control member only for
  self-uncertain models. Already declarative via FI `ensemble_mode`. No design work needed.
- **D6 — Which artifact? — RESOLVED (owner, 2026-08-11, superseding the earlier answer):
  `cmal_pool_PT`** — pooled, daily-only, discharge + precipitation + temperature only. It is the sole
  artifact that clears **both** G2 (no radiation) and G5 (no multi-resolution) at once, and being
  pooled it generalises zero-shot to Swiss and Nepal alike. *(Superseded: the multi-resolution
  `dudh_koshi` fine-tune — deferred with sub-daily. Rejected: `cmal_pooled_big` / `cmal_pool_20s_no_wd`
  — better skill, but both require the two radiation variables.)* **Accepted cost: PT is the weakest
  of the three; T6 measures whether the trade was right, and T0.0 q3 asks for the skill gap up front.**
- **D7 — Multi-resolution + sub-daily — DEFERRED (owner, 2026-08-11).** Plan 153 is **off this plan's
  critical path** and is not a dependency. Rationale: multi-resolution can serve only one basin today
  (1 of 11 DHM gauges has hourly discharge), so gating every deliverable behind core domain-type
  surgery bought a single station. It returns when DHM hourly coverage grows. **T0b still ships**, so
  a multi-resolution artifact fails loudly rather than being silently mis-onboarded in the meantime.
- **D9 — NWP forcing bias correction (parked, likely needed).** PT is trained on ERA5-Land; we feed
  NWP. Owner expects degradation. If T0c/T6 show it is material, options are: correct the NWP forcing
  toward the training climatology, ask the colleague to fine-tune on NWP-like forcing, or accept the
  loss. **Not scoped here** — but it is the most likely reason a good model scores badly for us, so
  T6 must report forcing provenance alongside the skill number.
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
