---
status: DRAFT
created: 2026-08-11
plan: 152
title: aquacast pooled (GROUP) probabilistic model integration — onboard the externally-trained daily `cmal_pool_PT` artifact on control forcing
scope: Integrate hydrosolutions/aquacast pooled models into SAPPHIRE Flow through the existing ForecastInterface boundary as GROUP-scoped artifacts, running on CONTROL forcing and emitting quantile-backed ForecastEnsembles. Owner selected `cmal_pool_PT` (pooled, DAILY-only, discharge + precipitation + temperature only) — the sole artifact clearing both the radiation gap (G2) and the multi-resolution gap (G5) at once, at the accepted cost of being the weakest of the three pooled models; this DEFERS Plan 153 and sub-daily out of scope. The artifacts are IN HAND and PT's contract is verified from its own config.yaml (T0.0), and T0c has run the model on a synthetic feed (mechanics proven; its real skill number and go/no-go still outstanding) — so the remaining critical path is engineering, not a human round-trip. TWO BLOCKERS dominate it: G9, the model emits discharge in mm/day which has NO SAP3 canonical unit (fi_unit_to_canonical raises; two locked tests enforce the omission), so the shim must expose m³/s and do the area-aware conversion; and G10, an external shim is invisible to discover_models() in production, resolved by a dedicated aquacast worker image (D10). Also covers the fail-loud guard against today's silent misread of multi-resolution requirements, gap-structure validation of the 210-day daily window, the Caravan↔HydroATLAS static alias map, the external-artifact import path and its provenance representation, the total-and-injective station-identity mapping (attached nowhere a GROUP model actually runs today), and a quantile-aware skill comparison — which per D11 is REANALYSIS-forced and therefore flatters a model trained on ERA5-Land, with the cycle-faithful NWP hindcast a named follow-on required before go-live. Uncertainty is model-owned (control forcing only, by FI declaration), so this plan needs nothing from the redesign's ensemble machinery.
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
- **D1, D3, D4, D10 and D11 are RESOLVED** (2026-08-12). **Only D9** (NWP bias correction) is
  intentionally parked, pending evidence that does not exist yet. Flagging D9 as "unresolved" is not
  a finding.

**Second review round (bounded, 2 rounds, real Codex each round) — escalated but PRODUCTIVE.** It
again over-expanded the doc (845 → 1579 lines), so it was reverted to the curated baseline and the
findings folded in by hand. Unlike round 1 the findings were **grounded and largely correct**; five
were independently verified against `main` and are now **G9** (mm/day unmappable — the most
consequential finding in the plan's history), **G10** (no deployment path for the shim), **T4**
injectivity, **T6** NWP-hindcast impossibility + scoring, and the **`max_nan` correction** (it counts
null cells, not missing rows — a correction to this plan's own T0c write-up). The scope note below
worked: findings addressed the plan as scoped rather than inventing tasks.

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
- **Quantile output takes the right BRANCH** — `_ensemble_from_variable_output`
  (`adapters/forecast_interface.py:186-267`) tries trajectories (`:199`) → quantiles (`:221`) →
  deterministic (`:243`); a CMAL head declares only `{DETERMINISTIC, QUANTILES}` and refuses to fake
  trajectories, so it lands on `ForecastEnsemble.from_quantiles` (`:228`). **But the branch then
  RAISES on the unit — see G9.** The structural path is right; the conversion is not yet possible.
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
- **Units are aquacast's problem — on the INPUT side only.** Its adapter converts declared →
  canonical, including the area-based `m³/s → mm/day`, which is why `area` must be among the
  statics. **The OUTPUT side is a blocker — see G9.**
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

Three **pooled** models exist (from the colleague's runs). These are the donors. **All three are in
hand** (owner's Dropbox, `2025-01-BARHKH/models/global/`), and **PT's row below is verified from its
own `config.yaml` and by running the model on a synthetic feed** (T0.0 / T0c RESULT — the mechanics
only; **T0c's real Swiss/NWP skill number and its go/no-go are still outstanding**) — the other two rows remain
owner-reported:

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

- **The Dudh Koshi fine-tune is a group of ONE** — *background only; it is NOT the selected
  artifact.* **`cmal_pool_PT` is genuinely multi-basin, so T4 onboards a real multi-station group and
  the degenerate case does not arise.** For the fine-tune: it declares GROUP scope, so onboarding
  takes the GROUP branch (`services/model_onboarding.py:489`) and the resolver gap (G6) still
  applies — but the `StationGroup` has a single member. **No task in this plan needs to verify that
  degenerate case**; it returns only if the fine-tune is ever onboarded. The plan's GROUP framing is
  right for the **destination**, not
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
   (Plan 117) and Swiss daily discharge history is deep. **PT is daily-only, so no hourly data is
   required** (the 168 h hourly branch belonged to the deferred multi-resolution fine-tune). **What it proves depends on which artifact runs it** — see T0c.
2. **Dudh Koshi — the real target.** Requires Nepal onboarding: no Nepal station config exists in
   the repo, **Plan 143 is unimplemented**, and the DHM questionnaire is unanswered. Proceeds in
   parallel; it is the source of the *trustworthy* skill verdict.

**Artifact availability — RESOLVED, they are in hand.** They are not in version control (no
`models/` tree or `.pt` in the aquacast repo, `.gitignore` excludes `/experiments/`, no releases or
LFS, no weights repo in the org) — they live on the **owner's Dropbox**,
`2025-01-BARHKH/models/global/`, holding `cmal_pool_PT`, `cmal_pool_20s_no_wd`, `cmal_pooled_big`,
`cmal_2` and a `README.md`. **T0 is therefore verification, not a request.**

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
aquacast declares its statics in **Caravan** names; our basin package carries **raw HydroATLAS**
codes. (The pooled family declares **51**; **`cmal_pool_PT` declares 50** — it omits
`degree_of_regulation`.) `_static_inputs` (`adapters/forecast_interface.py:1059-1071`) raises `ConfigurationError` on
any name it cannot find, so **every mismatched static is reported missing at every predict** — **21**
for `cmal_pool_PT`, 22 against the pooled 51-static set
— the model would never run. Verified against `tests/fixtures/basin_static/nepal-dhm-basins/`
(92 catalog features / 93 parquet columns): 29 already match and **22 are a pure rename** against the
pooled 51-static set — **21 of which PT actually needs**. **Owner-confirmed the map on 2026-08-11.** Closed by T1b.

### G9 — **BLOCKER: `mm/day` has no SAP3 canonical unit, so the model's output cannot be converted**
`cmal_pool_PT` emits `discharge` in **`mm/day`** (T0c-verified) and consumes discharge/precipitation
in `mm/day`. But `_FI_UNIT_TO_CANONICAL` (`adapters/forecast_interface.py:119-130`) **deliberately
omits `MM_PER_DAY`** — its own comment says so — and `fi_unit_to_canonical` (`:153-160`) raises
`ConfigurationError` for it. Two locked tests enforce the omission
(`tests/unit/types/test_forcing_schema.py:103`, `tests/unit/adapters/test_fi_unit_mapping.py:31`).

`_ensemble_from_variable_output` calls `fi_unit_to_canonical(var_output.metadata.unit)` (`:194`)
**before** building the ensemble, so **every predict would raise** at the output boundary.
Compatibility validation rejects the unit too (`services/model_onboarding.py:173`), so the model
would not even onboard.

**Why T0c missed it:** the probe called `AquacastModel.predict()` **directly**, never through
`ForecastInterfaceAdapter`. It proved the model runs; it did not prove *our* boundary accepts the
result. **This is the single most consequential finding of the review round**, and it invalidates
the earlier "units already work" claim.

**Resolution belongs in T2 (the shim), not in SAP3's unit map.** Our canonical discharge is `m³/s`
and mm/day↔m³/s is **area-dependent** — so a bare map entry would be wrong. The shim must present a
SAP3-compatible unit at its **outward FI boundary** (`M3_PER_S` for discharge) and do the area-aware
conversion itself, in both directions. Precipitation needs the same audit: if declared `MM_PER_DAY`,
expose canonical daily-accumulation `MM`. **This is a legitimate shim responsibility, not an FI
workaround** — the shim is our code, and the FI contract explicitly allows declaring the unit your
pipeline actually has.

### G10 — **BLOCKER: the external shim has no operational deployment path**
`discover_models()` sees only **installed entry points** (`services/model_registry.py:78`). The
runtime image runs `uv sync --frozen --no-dev` against **this repo's** `pyproject.toml`/`uv.lock`
(`Dockerfile:32`) and copies only that virtualenv (`:82`). T2 places the shim **outside** this repo
and forbids adding torch here; T4/T5 run only in a disposable experiment environment, so nothing made
the entry point available to the operational forecast worker. **RESOLVED (D10, owner 2026-08-12): a
dedicated aquacast worker image/environment** carrying the shim + torch runtime, built and owned by
T2, with a **cold-start `discover_models()` test in that deployed environment** as the acceptance
gate. `docs/standards/cicd.md` must gain the new topology.

### G11 — **BLOCKER: the hindcast path stores every ensemble as MEMBERS, destroying quantile semantics**
`run_group_hindcast` builds each `HindcastForecast` with a **hardcoded**
`representation=EnsembleRepresentation.MEMBERS` (`services/hindcast.py:669`), regardless of what the
model actually returned. Reconstruction then branches on that stored header
(`store/hindcast_store.py:283`, `:306`, `:313`, `:323`). So a **quantile-backed** PT hindcast would be
written with a MEMBERS header and read back as members — **the quantile levels are lost before T6 can
score them**, making T6's quantile-aware WIS comparison unsatisfiable through the existing path.
*(Found by the verification round; independently confirmed.)* **T6 must preserve the returned
representation end to end**; a red-first test storing a quantile ensemble and asserting it round-trips
as QUANTILES fails against today's code.

### G12 — **BLOCKER: Swiss basins do not carry HydroATLAS statics at all**
T1b's rename map was verified against `tests/fixtures/basin_static/nepal-dhm-basins/` and the plan
then generalised it to "our basin package". **That generalisation is wrong for Switzerland.** Swiss
`Basin.attributes` are built from the **CAMELS-CH** attribute row
(`adapters/camelsch_adapter.py:214`), a different namespace entirely; no Swiss basin *package* has
ever been imported, and the HydroATLAS codes (`slp_dg_sav`, `gla_pc_sse`, `pre_mm_syr`, `glc_pc_s*`)
appear in **exactly one file in the whole repo** — the Nepal fixture. Verified by grep.

So on the Swiss track the shortfall is **not 21 renames**: it is a large fraction of PT's 50 statics,
including all 22 `glc_pc_s*` land-cover classes and the HydroATLAS-coded soil/topography set, which
have **no CAMELS-CH counterpart under any alias**. `_static_inputs`
(`adapters/forecast_interface.py:1059-1071`) raises on every one it cannot find.

Closing it means a **HydroATLAS extraction over the Swiss basin polygons plus a package import** — a
data workstream of the same order as the radiation ingest this plan deferred as too expensive.
**This bites T0c, the go/no-go gate, not just T5**: T0c hand-shapes `ModelInputs` for Swiss stations
and needs the 50 static *values*, which do not exist. **Owner decision required — see D12.**

### G13 — **BLOCKER: Swiss NWP tops out at 5 days; PT declares a 15-day horizon**
PT's contract is `forecast_days: 15` with `future_known steps=15, max_nan=0`. The Swiss operational
source is ICON-CH2-EPS only, whose fetch window is **120 h = 5 days**
(`adapters/meteoswiss_nwp.py:689`). The recap gateway (IFS, ~15 d) is HRU-registered and
Nepal-scoped, so it is not available for Swiss basins.

**And the shortfall would be silent.** Per this plan's own T0c finding, `_missing_value_count`
(`adapters/forecast_interface.py:666-672`) counts nulls in *existing rows*, so **missing rows escape
the `max_nan` gate**; nothing else validates horizon. PT would receive a two-thirds-truncated
forecast window and return numbers.

This also makes T0c's stated requirement — "score on operational-like NWP forcing" — unbuildable at
15-day lead on the Swiss track. Since D11 leans on T0c as **the only NWP-forced evidence in the
plan**, a T0c that quietly degrades to reanalysis leaves the plan with **zero** NWP evidence and D9
permanently unmeasurable. **Owner decision required — see D12.**

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
**A gate, not a formality.** It was originally blocked on a human round-trip; **the artifacts turned
up on the owner's Dropbox and PT's contract is now verified** (T0.0 below), so T0 is verification
work. The residual colleague questions are kept because a wrong list would still cost a round-trip.

**T0.0 (superseded — kept for the record).** The original colleague request, before the artifacts
were found on the owner's Dropbox. Items 1, 2, 4, 5 and 6 are now answered by the artifacts
themselves; only the **skill gap** question remains outstanding, and it is **not blocking**.

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

*(Historical record — the original colleague question list. **Every item below is now ANSWERED
except the skill gap (item 3), which is outstanding and non-blocking.** Items 1, 2, 4, 5 and 6 were
answered by the artifacts themselves or by the repo; do not re-send them. Kept so the reasoning
behind each question survives.)*

**Answered without the colleague (owner + repo, 2026-08-11):** pool size ~17,007 basins in
`global_pool.txt` (PT itself trained on 12,952); `no_wd` = no weight decay, a training
hyperparameter, so `20s_no_wd` shares the radiation-requiring contract; cadence `issue_hours: [0]`
(once daily at 00Z); the statics enumerated and the rename map CONFIRMED (T1b); NWP-fed degradation
expected.**

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
4. Exact `input_requirement` — **answered for PT by construction** (daily-only, 210 d / 15 d, 50
   statics). Retained as the standing check for **any future artifact**, whose shape must not be
   assumed to match PT's.
5. Statics: **does PT use the same 51-static set** as the other pooled configs, or a reduced one?
   (The Caravan↔HydroATLAS rename map is confirmed — T1b — so only the *set* is open. `area` must be
   in it: `m³/s → mm/day` fails without it.)
6. ~~Does the hourly branch require hourly *observed* discharge?~~ **Moot for this plan** — PT is
   daily-only. The question returns with the deferred multi-resolution work, where our reading of
   `is_target_in_past: true` + `past_dynamic: [discharge]` confines it to `nepal_20010` today.

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
4. **Historical depth AND gap structure — daily only.** PT is daily-only (`issue_hours: [0]`), so
   audit the **210-day daily** window for past discharge, precipitation and temperature. Because
   `max_nan=0` counts null *cells* but not missing *rows* (see T0c RESULT), audit **calendar
   completeness**, not just depth: leading/trailing/internal gaps, duplicate and off-grid stamps.
   Produces the T1 scope.
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

**CONSTRAINT (verification round 3 — an earlier note here was wrong).** `discover_models` does
**not** contain a `ConfigurationError`: it catches and **re-raises** it
(`services/model_registry.py:93-95`), swallowing only generic `Exception`. So a `ConfigurationError`
raised anywhere in the try block — including from `_project_requirements` via `adapt_if_fi` — **aborts
discovery for EVERY model, a registry-wide blackout.** T0b's guard therefore must **not** simply
raise `ConfigurationError` from inside the projection. Either the guard signals in a way discovery
skips per entry point, or `discover_models` is changed to skip-and-continue on a bad model. **Which
one is an implementation choice; the invariant is that one unsupported model must never darken the
registry.** Red-first must include that: with one multi-resolution model installed alongside good
ones, `discover_models()` still returns the good ones.

This converts a silent misread into a loud refusal, and its red test is what Plan 153 later turns
green. *(Touches `adapters/forecast_interface.py` — coordinate with 151 T2.)*

### T0c — Offline skill spike (throwaway; **gates the whole investment**)
**The highest-leverage task in the plan, and it comes near the start.** T6 — the number that says
whether any of this is worth building — otherwise sits at the very end, behind T1, T2, T3, T4 and
Plan 153. The Risks section notes the pooled model may not survive contact with operational forcing.
Finding that out last is the expensive ordering.

**Correction from review — it DOES need a minimal translation layer (G9).** A run fed from *our*
assembly hits the `mm/day` boundary, and T2 (which fixes that) would otherwise be gated on T0c.
Resolution: T0c carries an **explicitly throwaway** projection implementing the same outward
name-and-unit contract T2 will productionise, records that raw canonical inputs fail without it, and
scores only the translated run. It still needs neither the import path, the resolver, nor packaging.
So: hand-shape **daily** FI `ModelInputs` (PT is daily-only, `issue_hours: [0]`) for the target
stations from historical data, call `AquacastModel.predict()` directly, and score with the T6
quantile-aware metrics. Entirely decoupled from `discover_models()`.

**Run it on the Swiss track** (the Nepal target is not onboarded). **`cmal_pool_PT` being pooled is
what makes this worth doing**: generalising to unseen basins is what a pooled model is *for*
(`test_unknown_station_generalizes`), so a Swiss zero-shot run is a **genuine skill signal**, not
merely a smoke test. That was not true of the Dudh Koshi fine-tune, where a Swiss run would have
measured single-basin transfer degradation and proved integration only.

**Score on operational-like forcing (NWP), not the ERA5-Land feed PT was trained on** — otherwise
the number flatters the model on exactly the axis the Risks section flags. T0c is therefore the
**only** NWP-forced evidence in this plan until the D11 follow-on lands (T6 is reanalysis-forced).

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

1. **`max_nan=0` on a 210-step window, for all three past variables** — but **it does not by itself
   guarantee a gap-free series** (review correction). `_missing_value_count`
   (`adapters/forecast_interface.py:666-672`) counts **nulls and NaNs in existing rows**; a
   completely **absent day** carries no null and **escapes the gate entirely**, and the past-dynamic
   read (`services/operational_inputs.py:468-489`) does not resample onto or complete a cadence grid.
   So `max_nan=0` catches *null cells*, while a *missing timestamp* passes silently — the more likely
   operational failure. T1 must validate the **expected timestamp grid** (length, uniqueness,
   leading/trailing/internal gaps), not null counts.
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
station carries the full lookback at every operational issue, **for the daily branch** (PT is
daily-only; the hourly branch went with the deferred multi-resolution work). If the target
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
`tests/fixtures/basin_static/nepal-dhm-basins/`: against the pooled **51**-static set, 29 match exactly
(`area`, `p_mean`, `frac_snow`, `high_prec_*`, `low_prec_*`, all 22 `glc_pc_s*`); the other **22 (of
which PT needs 21) are
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
and raises `ConfigurationError` — all of PT's 21 unaliased statics would be reported missing.

**Approach — additive aliasing (recommended).** Emit the Caravan aliases as **additional columns**
alongside the HydroATLAS canonicals when building the static frame. Purely additive: our canonical
namespace and the `feature_catalog.json` / `required_by_models` seam
(`services/basin_importer.py:185`) are untouched, `_static_inputs` selects only what the model asks
for so extra columns are inert, and — importantly — **it needs no change to
`adapters/forecast_interface.py`**, avoiding the Plan 151 collision. *(Rejected: renaming at import,
which would churn our canonical namespace; or an alias layer inside the FI adapter, which collides
with 151's T2.)*

**Red-first — the RED assertion is the positive one.** With aliasing, a HydroATLAS-named static
frame **resolves all of PT's declared statics**; that fails today because `_static_inputs`
(`adapters/forecast_interface.py:1059-1071`) demands exact declared names and has no aliasing. (The
converse — that an unaliased frame raises — is a *characterization* assertion that already passes;
keep it, but it is not the gate.) Pin the map in one place with a test asserting **all 50
of `cmal_pool_PT`'s declared statics resolve** (PT needs **21** renames — it does not declare
`degree_of_regulation`). Keep the 22nd mapping (`degree_of_regulation` → `dor_pc_pva`) as optional
forward-compatibility for the radiation-requiring pooled models.

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

**Scope raised by review — the shim owns the unit boundary (G9).** It must present SAP3-compatible
units at its outward FI boundary and convert numerically in both directions:
- **discharge**: expose `M3_PER_S`, doing the **area-aware** `mm/day ↔ m³/s` conversion internally
  (`area` is already a required static).
- **precipitation**: audit what PT declares; if `MM_PER_DAY`, expose canonical `MM` (daily
  accumulation) and translate consistently.
- **Red-first — assert NUMBERS, not mappability.** `M3_PER_S` and `MM` are **already** in
  `_FI_UNIT_TO_CANONICAL` (`adapters/forecast_interface.py:119-130`), so a test that merely proves
  `fi_unit_to_canonical` succeeds on the shim's declared units **passes today and proves nothing**.
  The genuinely red assertions are: (i) a **numeric** area-aware round trip — a known discharge in
  mm/day at a known `area` arrives as the correct m³/s value, catching relabelling-without-conversion;
  and (ii) the **cold-start `discover_models()`** check in the deployed worker image (G10).

**Scope raised by review — deployment (G10), decided (D10).** T2 **builds a dedicated aquacast
worker image/environment** carrying the shim and its torch runtime, because the runtime image
installs only this repo's lockfile (`Dockerfile:32,82`) and would never see an external entry point.
This keeps ~2 GB of PyTorch out of every existing worker. **Acceptance requires a cold-start
`discover_models()` test in that deployed environment** — without it this plan may not claim
"operational". Update `docs/standards/cicd.md` with the topology.

Config↔artifact pinning is D1. **The unit and packaging work is testable against synthetic
single-resolution FI models**, so it does not wait on anything — though the artifacts are in hand, so
the real config can be used directly.

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
called**. Testable with synthetic models, so it does not wait on anything.

### T4 — Group onboarding on a disposable database (closes G6)
Isolated experiment DB, artifact location and model ids per Plan 135 decision 7 — **never** the
operational DB. Create the `StationGroup` over the T0 target set; then **close G6**: a durable,
namespaced `StationId → external gauge code` mapping, and resolver attachment at **every** consumer
where a GROUP model runs (forecast cycle, hindcast, training, station onboarding — see the G6 table),
not just model onboarding.

**The mapping must be TOTAL and INJECTIVE** (review finding, verified). `predict_batch` builds
`station_ids_by_code` as a dict comprehension keyed by external code
(`adapters/forecast_interface.py:774-777`); two `StationId`s resolving to the **same** gauge code
collapse silently to one entry, output is attributed through that collapsed dict (`:798`), and the
"omitted stations" guard compares **external-code** sets (`:789`) so it **cannot detect the lost
station**. That is silent misattribution of one basin's forecast to another — the worst failure mode
in this plan.

**Red-first:** a station with no mapping raises `ConfigurationError` **at onboarding**, not at predict
time in production; **two stations sharing one external code are rejected at onboarding and
defensively in `predict_batch`**; and a GROUP model reaching the operational path has a resolver
attached (the test that fails against today's code).

### T5 — First operational group forecast on control forcing
`run_group_forecast` end to end on the experiment DB: quantile ensembles stored, provenance correct,
per-station failures isolated, **issue-time semantics correct** — horizon 1 is the nowcast, so the
first row's `datetime` equals the issue stamp; do not shift the issue back a day. Validate the
returned grid against the requested issue rather than trusting it.

**CORRECTION (verification round 3): T5 is a CHARACTERIZATION task, not a red-first one.** An
earlier revision required "validate non-crossing quantiles before storage" — **that guard already
exists**: `_apply_quantile_crossing` (`services/forecast_qc.py:204`) flags crossing QUANTILES
ensembles and `run_group_forecast.py:296` drops them on `QC_FAILED` before a forecast is built.
Likewise the quantile representation is already carried into `OperationalForecast`
(`run_group_forecast.py:319`) and round-tripped by the store (`store/forecast_store.py:285`), and a
failed station already does not suppress its siblings (`run_group_forecast.py:468`). **All three
behaviours T5 was going to "add" are present.**

So T5 **proves the existing path carries PT end to end** — it must not pretend to be red-first, and a
store-level crossing test would fail only by bypassing the service guard, not by finding a defect.

**Acceptance (characterization, expected GREEN on a correct implementation):** a golden test pinning
the quantile ensemble shape end to end through `run_group_forecast`; the issue-time semantics
(horizon 1 is the nowcast, first row's `datetime` == issue stamp, validated against the *requested*
issue rather than trusted); one station's `FAILURE` leaving siblings intact; and a crossing-quantile
ensemble being dropped by QC **at the service level**. Anything here that goes red is a genuine
regression, not planned work.

### T6 — Quantile-aware skill comparison vs the incumbents (closes G7)
Hindcast over the target set and compare against the incumbents on alerting-relevant metrics.

**First fix the metrics.** Pseudo-member treatment (`services/skill/service.py:49`) makes a
7-quantile forecast incomparable to a 21-member ensemble: the rank histogram would use 8 bins
(`services/skill/diagrams.py:76-77`). Score quantile forecasts with **WIS (Weighted Interval Score)**
— proper on a finite quantile grid — **not** a pinball integral relabelled as CRPS, which is not
identified because FI forbids quantile levels at 0 and 1 (open tails, always). Validate non-crossing
quantiles before any CDF interpolation, and treat all FI quantile output as open-tailed: there is no
tail-closure metadata in `QuantileData` or `VariableMetadata` to read.

**Review finding — the existing hindcast path cannot do an NWP-forced hindcast.**
`services/hindcast.py` takes a `WeatherReanalysisSource` (`:263`, `:440`), uses reanalysis as
**teacher forcing** by design (`:195`, "v0-scope §A13"), and stores `ForcingType.REANALYSIS`
(`:372`). So T6 cannot, as written, measure the NWP degradation D9 is about. **RESOLVED (D11, owner 2026-08-12): option (b).** T6 uses the existing
**reanalysis** hindcast and **claims only reanalysis skill**. The cycle-faithful NWP hindcast (read
historical `weather_forecasts` by issue cycle, control member 0, cycle provenance preserved, correct
forcing type, leakage tests proving each issue sees only the cycle available then) is a **named
follow-on, REQUIRED before any go-live decision rests on the skill number**. Because PT was trained
on ERA5-Land, a reanalysis-only verdict **systematically flatters** it — T6 must say so beside the
number rather than leave the reader to infer it.

**First fix the hindcast representation (G11).** The path hardcodes a MEMBERS header
(`services/hindcast.py:669`) and reconstruction follows it (`store/hindcast_store.py:283-323`), so a
quantile hindcast is silently converted to members before scoring. Preserve the returned
representation end to end.

**Red-first — at the SERVICE boundary, not the store.** Run `run_group_hindcast` with a model
returning QUANTILES and assert the persisted `HindcastForecast.representation` is `QUANTILES`; it
fails today because `hindcast.py:669` hardcodes MEMBERS. **A store-level round-trip test would NOT
work**: `store_hindcast` already branches on the *actual* ensemble representation
(`store/hindcast_store.py:50`) and reconstruction honours the stored header, so such a test passes
against today's code and proves nothing. The defect is introduced upstream, and the test must sit
upstream of it.

**Score both representations the same way.** Giving WIS to quantile forecasts while incumbents keep
member-CRPS compares two different estimators, which is not the apples-to-apples verdict the
Objective promises. Compute **WIS for both** on one pinned quantile grid — deriving empirical
quantiles from member ensembles — and keep CRPS only as a member-only diagnostic.

**Metric changes need a computation version.** Skill computation is hardcoded to version 1
(`services/skill/service.py:40`) and inserts use `ON CONFLICT DO NOTHING`
(`store/skill_store.py:31,58`), so recomputation cannot replace old pseudo-member rows and "latest"
reads take the max stored version — leaving stale version-1 rank histograms visible. T6 must bump the
computation version and define how superseded scores/diagrams drop out of latest reads. Note `wis` is
not currently an allowed onboarding skill-gate metric (`types/model_onboarding.py:79`) — decide
whether it becomes one.

Per Plan 135 decision 8 the verdict is two-dimensional: **method skill** and **integration fitness**,
reported separately.

## Sequencing

**Selecting `cmal_pool_PT` collapsed the critical path.** The old chain
(`Plan 151 T2 → Plan 153 → T4 → T5 → T6`) existed *only* because the artifact was multi-resolution.
A pooled daily model needs none of it: **Plan 153 is deferred, and nothing in this plan waits on
Plan 151.**

**The critical path is now: T0 → T0c (go/no-go gate) → T2 ‖ T3 → T4 → T5 → T6**, with **T1 and T1b
running alongside and joining at T5** (T4 onboards against synthetic data, so it needs neither the
real forcing depth nor the alias map; T5 is the first task that touches real assembly).
The artifacts are in hand, so **the longest pole is engineering, not a human round-trip** — and
within it, **T1** (210 days of gap-free daily history per station) and **T2** (the G9 unit boundary
plus the G10 worker image) are the two that actually set the date.

**Before the gate:** **T0** (verification, not a request — artifacts in hand) and **T0b**, which has
no dependencies at all. T0b ships regardless: nothing here needs multi-resolution, which is *precisely*
why the guard matters — it is the only protection against a multi-resolution artifact being silently
mis-onboarded later.

**After T0c's go/no-go, in parallel:**
- **T1** — past-forcing depth, the long engineering pole: 210 days of **gap-free daily** discharge +
  precipitation + temperature per station. Swiss depth we largely hold; Nepal follows onboarding.
- **T1b** — the Caravan↔HydroATLAS static alias map. Small, but **PT cannot predict without it**.
- **T2** — shim, the **G9 unit boundary**, and the **G10 aquacast worker image**; the unit and
  packaging work is exercised against synthetic FI models, the image needs a cold-start test.
- **T3** — import path + provenance; exercised against synthetic models, no artifact needed.

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
    { "id": "T1",  "tasks": ["T1"],  "parallel": false, "depends_on": ["T0c"] },
    { "id": "T1b", "tasks": ["T1b"], "parallel": false, "depends_on": ["T0c"] },
    { "id": "T2",  "tasks": ["T2"],  "parallel": false, "depends_on": ["T0c"] },
    { "id": "T3",  "tasks": ["T3"],  "parallel": false, "depends_on": ["T0c"] },
    { "id": "T4",  "tasks": ["T4"],  "parallel": false, "depends_on": ["T2", "T3"] },
    { "id": "T5",  "tasks": ["T5"],  "parallel": false, "depends_on": ["T1", "T1b", "T4"] },
    { "id": "T6",  "tasks": ["T6"],  "parallel": false, "depends_on": ["T5"] }
  ]
}
```

**No `plan-153` edge** — deferred with sub-daily (D7). No Plan 151 edge either; that relationship is
now file-level coordination only. T0b has no dependencies and ships first.
**T0c is encoded as a real gate**: T1, T1b, T2 and T3 depend on it, so a poor result stops them
rather than merely informing them (an earlier graph let them start in parallel, contradicting the
gate's stated purpose). **T1b is a dependency of T5** — without the static alias map PT cannot
predict at all.

## Risks

- **We deliberately chose the weakest of three models.** `cmal_pool_PT` was selected for pipeline fit,
  not skill. If T6 shows it does not beat the incumbents, the honest reading is *"the cheap model is
  not good enough"* — **not** *"aquacast is not good enough"*; `cmal_pool_20s_no_wd` remains untested
  behind the radiation workstream. T0.0 q3 asks for the skill gap so this is quantified before we
  build, and T6 must report the verdict against **PT specifically**, never against the model family.
- **PT's contract is VERIFIED (risk retired 2026-08-11).** Read from its own `config.yaml` and
  confirmed by running the model: daily-only, 210 d / 15 d, `future_dynamic = [precipitation,
  mean_temperature]`, 50 statics, 7 quantile levels. The residual version of this risk is narrow —
  **a future artifact could differ**, so T0 must re-read `input_requirement` for every delivered
  artifact rather than assuming PT's shape carries over.
- **The silent-misread failure mode (G5) is worse than a hard failure** — plausible wrong numbers,
  accepted by onboarding. T0b makes it loud first, and is worth landing even if the rest slips.
- **Degradation from ERA5-Land training to NWP-fed operation is EXPECTED, not hypothetical**
  (owner-confirmed 2026-08-11; `configs/regions/nepal.yaml`: "ERA5-Land daily is the ONLY forcing").
  A pooled model on out-of-distribution forcing can be confidently wrong. **T0c scores on operational-like NWP forcing.
  T6 does NOT** — per D11 it uses the reanalysis hindcast path, so its number flatters the model on
  exactly the axis we know is weak, and must be reported with that caveat attached. Closing the gap
  is the D11 follow-on, required before go-live. If degradation proves large, bias correction of the
  NWP forcing toward the training climatology becomes a follow-on (see D9); it is explicitly NOT
  scoped here.
- **Silent lookback truncation** produces plausible bad forecasts. Mitigated by T1's hardening.
- **Station-identity mapping is a hidden coupling** — a wrong map silently forecasts the wrong basin.
  Mitigated by T4's fail-at-onboarding test.
- **Sub-daily data availability** — *not a risk for this plan* (PT is daily-only, `issue_hours: [0]`).
  It returns with the deferred multi-resolution work: 168 h of hourly forcing *and* hourly
  observations at every issue, ×8/day, which only `nepal_20010` can supply today.
- **Concurrent Plan 151** — see the collision map.

## Open items (design forks for the owner)

- **D1 — Where the aquacast `config.yaml` lives — RESOLVED (owner, 2026-08-12): option (A),
  package data in the shim, one entry point per trained config.** The shim release *is* the
  config↔weights contract, so they cannot drift; no new store surface; and `discover_models()` stays
  a pure function of what is installed rather than gaining a DB dependency at discovery time — a
  change that would touch the registry every model uses. Cost: a shim release + aquacast-worker
  image redeploy per new model, acceptable while models arrive occasionally. **Reversible** — moving
  to (B) later is possible; doing (B) first would change the registry before the first model is
  proven. *(Rejected: (B) artifact-store sidecar, (C) metadata JSONB — both decouple retrains from
  releases but require discovery to read DB state.)* **Revisit if** aquacast models start being
  swapped frequently (monthly experiments), where (B)'s decoupling would start to earn its cost.


- **D2 — Target station set — RESOLVED (owner grill-me, 2026-08-11): BOTH, as parallel tracks.**
  **Dudh Koshi is onboarded as the real target** (accepting the Plan 143 + DHM dependency), and
  **while that proceeds the model is applied to Swiss data directly — not fine-tuned**. See
  § Artifact identity. T1's scope therefore splits: Swiss depth (which we largely hold; Plan 130
  applies) unblocks T0c now, Nepal depth follows onboarding. *(Rejected: Swiss-only via a Swiss
  fine-tune — an extra ask on the colleague for a basin we do not ultimately target.)*
- **D3 — Import-only, or must retrain work? — RESOLVED (owner, 2026-08-12): import-only for v1.**
  We import artifacts the colleague trains; T3 stays a validate → store → provenance → promote path
  and FI `train`/`retrain` plus `assemble_group_training_data` stay unexercised. Matches how the
  fine-tunes are produced today (on their side). **Retrain/fine-tune in SAP3 is a named follow-on** —
  it would enlarge T3 substantially (training-data assembly, finetune config, checkpoint monitor) and
  pull training compute into our deployment.


- **D4 — Alert eligibility of a quantile-backed ensemble — RESOLVED BY EVIDENCE (2026-08-12): it
  already works.** The system carries a **dedicated quantile floor**,
  `min_operational_quantile_levels` (default **7**, validated `>= 7`,
  `config/deployment.py:124,192-199`), applied in both the alert gate
  (`services/alert_checker.py:184-187`) and the onboarding floor
  (`services/model_onboarding.py:726-728`). Quantile ensembles are **never** judged against the
  20-member `min_operational_ensemble_size`. `cmal_pool_PT` emits `[0.05, 0.1, 0.25, 0.5, 0.75, 0.9,
  0.95]` and `ForecastEnsemble.from_quantiles` requires ≥7 levels with min ≤0.05 and max ≥0.95
  (`types/ensemble.py:97-106`) — **PT satisfies every check.**
  **⚠ Zero margin, and that is a real fragility:** 7 levels is exactly the floor, 0.05 exactly the
  required minimum, 0.95 exactly the required maximum. Any narrowing of `inference.quantile_levels`
  in a future config breaks onboarding *and* alert eligibility at once. **T0 must re-verify the
  declared levels for every delivered artifact**, and this is worth stating to the colleague as a
  contract we depend on.


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
- **D12 — BLOCKING FORK: the Swiss track cannot satisfy PT's input contract (G12 + G13).** Nepal is
  blocked on Plan 143 + DHM; Swiss was the executable track, and it can supply neither PT's 50
  HydroATLAS statics nor its 15-day forcing window. **T0c — the gate the whole plan is sequenced
  around — cannot run as scoped.** Options:
  **(A)** Swiss HydroATLAS extraction over the basin polygons + package import, and accept a
  reanalysis-forced T0c (no 15-day Swiss NWP exists). Unblocks the pilot on data we control, at a
  data-workstream cost comparable to the deferred radiation ingest.
  **(B)** Wait for Nepal onboarding (Plan 143 + DHM). The Nepal basin package already carries the
  HydroATLAS statics, and recap/IFS reaches ~15 days — **both blockers vanish on the Nepal track**.
  Cost: no date, gated on an unanswered DHM questionnaire.
  **(C)** Ask the colleague for a PT variant matched to our envelope — reduced static set and/or a
  5-day horizon. Cheapest for us, a model-side change for them, and it changes what we are
  evaluating.
  *Recommendation: ask (C) first — it is a config-and-retrain on their side and may cost days rather
  than weeks — while scoping (A) as the fallback. (B) is the architecturally cleanest but has no
  date. **Do not start T1/T1b/T2/T3 until this is settled**: their scope depends on the answer.*

- **D9 — NWP forcing bias correction (parked, likely needed).** PT is trained on ERA5-Land; we feed
  NWP. Owner expects degradation. **Only T0c (NWP-forced) or the D11 follow-on can measure this — T6
  cannot**, since D11 scopes it to reanalysis forcing. If it proves material, options are: correct
  the NWP forcing toward the training climatology, ask the colleague to fine-tune on NWP-like
  forcing, or accept the loss. **Not scoped here** — but it is the most likely reason a good model
  scores badly for us, so every skill number must carry its forcing provenance.
- **D10 — How does the shim reach the operational worker? — RESOLVED (owner, 2026-08-12): option
  (A), a dedicated aquacast worker image/environment**
  carrying the shim + torch runtime, keeping ~2 GB of PyTorch out of every existing worker and
  isolating the model's dependency closure. **Acceptance requires a cold-start `discover_models()`
  test in that deployed environment**; without it this plan may not claim "operational". T2 owns the
  image; `docs/standards/cicd.md` must be updated with the new topology. *(Rejected: (B) install the
  shim into the existing image — pulls a large torch runtime into every worker; (C) accept
  experiment-only and drop "operational" from the Objective.)*
- **D11 — Does T6 measure NWP degradation or reanalysis skill? — RESOLVED (owner, 2026-08-12):
  option (B), reanalysis.** T6 uses the existing reanalysis hindcast and **drops the claim that it
  measures NWP degradation**. Because PT was trained on ERA5-Land, a reanalysis-only verdict
  **systematically flatters** it, so the caveat must travel beside the number. The **cycle-faithful
  NWP hindcast is a named follow-on, REQUIRED before any go-live decision rests on the skill
  number** — read historical `weather_forecasts` by issue cycle, control member 0, cycle provenance
  preserved, correct forcing type, with leakage tests proving each issue sees only the cycle
  available then. **T0c is the only NWP-forced evidence in this plan until it lands.** *(Rejected:
  (A) build that path now — the honest measurement, but a substantial task inside a plan already
  carrying three blockers.)*


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
