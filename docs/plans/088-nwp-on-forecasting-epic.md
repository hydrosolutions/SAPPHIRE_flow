# Plan 088 — NWP-on operational runoff forecasting (epic / vision)

**Status**: DRAFT
**Type**: epic / vision (milestone-decomposition sign-off artifact)
**Phase**: v0b → v1 bridge (NWP-consuming runoff forecasting)
**Parent vision**: "NWP-on operational runoff forecasting" — operational
forecasts that consume ICON-CH2-EPS basin-average forcing to predict river
discharge, end-to-end, ensemble-first.
**Created**: 2026-06-30
**References**: Plans **071** (MeteoSwiss reanalysis adapter), **072** (hybrid
forcing resolver), **078** (forecast provenance for NWP-less forecasts), **084**
(dev-deployment validation, 2-station runoff-only), **086** (NWP-cycle
memory-bounded streaming — MERGED/READY), **087** (ICON mesh basin extraction —
MERGED/READY), plus the locked ensemble-first decision
(`docs/architecture-context.md`) and the v0 NWP variable allowlist
(`tp` + `t_2m`).

> This is a **WF1 sign-off artifact**: it folds the vision-decompose advisories
> into a refined four-milestone chain and pins the resolved design decisions so
> each milestone can be promoted to its own `READY` plan and run through WF2
> (`vision-build`). It defines no tasks of its own and authorises no code: each
> milestone gets a separate draft→review→READY plan before its WF2 run (see
> **Per-milestone WF2 readiness**).

---

## Vision (end state)

The operational `forecast-cycle` produces **discharge forecasts that consume NWP
forcing**. For a station bound to `nwp_source = icon_ch2_eps`, the cycle:

1. fetches + archives the ICON-CH2-EPS cycle (already solved — Plans 086/087),
2. extracts **basin-average** precipitation + 2 m temperature from the
   unstructured ICON mesh (already solved — Plan 087's `MeshBasinExtractor`),
   **retaining the 21-member ensemble dimension**,
3. **aggregates** the hourly ICON forcing to the **daily** resolution the model
   was trained at (precipitation accumulated via the FI-sanctioned
   `AggregationMethod.SUM`, temperature averaged via `MEAN`), **per member** —
   this is **aggregation, NOT disaggregation**: it averages/accumulates real
   hourly data into the daily step and invents no sub-daily structure,
4. runs a **linear-regression** runoff model — authored as a `forecastinterface`
   model declaring `FutureKnownVariable(precipitation)` +
   `FutureKnownVariable(temperature)` under `future_known` (and
   `PastKnownVariable(discharge)` under `past_known`), onboarded via the existing
   Plan 076 `ForecastInterfaceAdapter` so it surfaces as a `StationForecastModel`
   with `future_dynamic_features = {precip, temp}` — trained on historical daily
   forcing + observed discharge, producing a **21-member discharge ensemble** by
   mapping inference over the forcing members,
5. persists each forecast with **correct provenance** (NWP-primary / NWP-fallback
   / runoff-only) so a consumer can tell, via the API and schema, which input
   classes a forecast actually consumed.

The end state is **ensemble-first** throughout: the ICON member axis is the
load-bearing source of forecast spread, preserved from extraction through
aggregation to inference output. It does **not** collapse to a member-mean before
inference.

The model timestep is **DAILY** (resolution #7): there is **no free Swiss gridded
*hourly historical* product**, so the only viable free training history is daily
(Plan 071's `ch.meteoschweiz.ogd-surface-derived-grid`). A sub-daily track exists
but is a **deferred follow-on epic** (ERA5-Land hourly forcing — see *Follow-on
epic (deferred): ERA5-Land sub-daily forcing*), explicitly out of scope here.

This vision closes the gap left by v0a/v0b, whose only shipped models are
NWP-free (`linear_regression_daily`, `persistence_fallback`,
`climatology_fallback` — all declare `future_dynamic_features = frozenset()`).

---

## Current state (verified against code, 2026-06-30)

- **086 / 087 are MERGED-equivalent READY** and **model-independent**. 086 makes
  the NWP fetch/archive memory-bounded (the Plan 084 OOM fix); 087 adds
  `MeshBasinExtractor` for ICON's unstructured mesh `(valid_time, member, values)`
  with no lat/lon dims. Plan 087 explicitly states both are independent of each
  other and of any downstream model. **These are DONE dependencies of this epic,
  not work this epic re-implements.**
- **Extraction is currently inert for the runoff-only deployment.** In
  `flows/run_forecast_cycle.py:318-328` the extractor filters station configs to
  those whose `nwp_source` matches the grid source; with no station bound to an
  ICON source it logs `nwp.extraction_skipped reason=no_matching_sources` and
  returns. Binding a station to `icon_ch2_eps` is what activates the path (M3).
- **All v0 models are NWP-free** (`src/sapphire_flow/models/*`): `artifact_scope =
  ArtifactScope.STATION`, `future_dynamic_features = frozenset()`. They satisfy
  the **`StationForecastModel`** Protocol (per-station `predict(...) ->
  tuple[dict[str, ForecastEnsemble], bytes | None]`). Their ensemble spread comes
  from training-residual sampling, not from forcing members.
- **The FI→native model boundary already exists (verified).** The Plan 076
  `ForecastInterfaceAdapter` (`adapters/forecast_interface.py`) wraps a
  `forecastinterface` model as a `StationForecastModel`: `_project_requirements`
  maps FI `InputRequirement.future_known` → `future_dynamic_features` (`:452-475`),
  and `_future_known_inputs` (`:881-896`) delivers future forcing into the FI
  model. **M2's NWP model is authored as an FI model and reuses this adapter — its
  boundary work is "use it," not "build it" (resolution #5).**
- **`operational_inputs` does not aggregate future-dynamic forcing.**
  `_pivot_nwp_records` (`services/operational_inputs.py:45-77`) preserves raw
  `valid_time` resolution and, when members are present, emits `param_member`
  columns (e.g. `precipitation_0 … precipitation_20`). `resample_to_time_step` is
  applied only to `past_targets` (line 150), never to `future_dynamic`. A model
  trained on **daily** forcing fed **hourly** ICON rows would see a shape/row-count
  mismatch. **Hourly→daily aggregation is net-new and owned by M3.** Note: delivery
  of the pivoted forcing **into `StationModelInputs.future_dynamic` already exists**
  (`_pivot_nwp_records` at `:207`, assembled into `StationInputData.future_dynamic`
  at `:244`); what M3 adds is the **per-member daily aggregation before the pivot**,
  not the delivery plumbing.
- **No "no-NWP" provenance value exists.** `NwpCycleSource` has exactly `PRIMARY`
  and `FALLBACK` (`types/enums.py:173-175`); the forecast row stores
  `nwp_cycle_source NOT NULL CHECK IN ('primary','fallback')` and
  `nwp_cycle_reference_time NOT NULL`. Runoff-only forecasts today record a fake
  `PRIMARY` + cycle-time reference. **Plan 078 owns the fix and is parked pending
  a grill-me design gate.**
- **Forcing history is thin.** `historical_forcing` is fed by CAMELS-CH (training,
  ≤2020) only; Plans 071/072 (the forward-accumulating MeteoSwiss reanalysis
  adapter + hybrid resolver) are **DRAFT**. `RawHistoricalForcing` already carries
  a nullable `member_id`, so the schema can represent both deterministic
  reanalysis (member_id=None) and member-resolved forcing.

---

## Milestones

The chain is **M1 → M2 → M3 → M4**. The advisory-driven refinements are folded in
below; the **Resolved design decisions** section pins the rationale.

> **Note on 086/087 vs M3 (advisory fix):** the vision-decompose draft framed M3
> as "refine/implement 086/087". That is wrong — 086/087 are READY/merged and
> model-independent. M3 builds **on top of** them: it is the station weather-source
> binding + the net-new per-member daily aggregation + feeding the M2 model. The
> 086 OOM fix and 087 mesh extraction are **prerequisite infrastructure already
> done**, listed as dependencies, not milestone work. The Plan 076
> `ForecastInterfaceAdapter` (FI→native forcing delivery) is likewise a **done
> dependency** M3 reuses, not work M3 builds.

### M1 — Historical weather forcing resolver

**Goal**: Give models a forward-accumulating, multi-source daily weather-history
stream. Implement Plan 071 (MeteoSwiss open-data daily reanalysis adapter —
RprelimD precip, TabsD/TminD/TmaxD temperature → basin-averaged via the **raster**
`ExactExtractGridExtractor` on the regular Swiss grid → `historical_forcing`) and
Plan 072 (the hybrid per-parameter priority resolver MeteoSwiss → CAMELS-CH,
opt-in via `DeploymentConfig.reanalysis_source`).

**Maps to / supersedes**: Plans **071** and **072** (both promoted from DRAFT;
this epic is their parent). No supersession — M1 *is* 071+072, sequenced and
scoped under the epic.

**dependsOn**: none.

**New components introduced**:
- `MeteoSwissOpenDataReanalysisAdapter` (raster path, regular grid).
- `ForcingSource` enum + `SOURCE_ATTRIBUTIONS` (071 T1).
- `PerSourceStoreReader` + `HybridForcingSource` + `default_hybrid_forcing_source`
  (072).
- **The shared canonical forcing-schema contract** (see resolution #6) — a pinned
  artifact (canonical variable names, units, daily temporal resolution,
  basin-average spatial representation) that M2 declares its features against and
  that both the M1 raster extractor and the M3 ICON mesh extractor are validated
  against independently.

**acceptanceCriteria** (test-verifiable):
1. `MeteoSwissOpenDataReanalysisAdapter.fetch_reanalysis(...)` over a replay
   fixture returns `list[RawHistoricalForcing]` with the canonical parameter names
   (`precipitation`, `temperature`, `temperature_min`, `temperature_max`), the
   correct per-product `ForcingSource` tag, and deterministic content-hash
   versions (same bytes → same version).
2. Basin-averaging via `ExactExtractGridExtractor` produces one value per
   `(station_id, valid_time, parameter)` at **daily** resolution; reanalysis rows
   carry `member_id = None` (single deterministic trajectory — see resolution #1).
3. `default_hybrid_forcing_source(...).fetch_reanalysis(...)` over a window
   spanning the CAMELS-CH (pre-2020) and MeteoSwiss (post-2026-04) regimes yields
   the documented per-parameter source distribution with no duplicate
   `(station_id, valid_time, parameter)` rows (supersession filter active).
4. **Shared forcing-schema contract is a committed, testable artifact**: a single
   declaration of canonical variable names + units + daily resolution +
   basin-average `SpatialRepresentation`, asserted against the raster adapter's
   output. (M3 asserts the ICON path against the *same* contract.)

### M2 — NWP-consuming runoff model

**Goal**: Build and onboard a v0b **linear-regression** runoff model with **NWP
features** (owner-confirmed), authored as a `forecastinterface` model that declares
an `InputRequirement` with `FutureKnownVariable(precipitation)` +
`FutureKnownVariable(temperature)` under `future_known` (the v0 allowlist
`tp` + `t_2m`) and `PastKnownVariable(discharge)` under `past_known`. It is
**onboarded via the existing Plan 076 `ForecastInterfaceAdapter`**, so it surfaces
as a `StationForecastModel` with `future_dynamic_features = {precipitation,
temperature}`. It trains on M1 historical **daily** forcing + observed discharge and
is onboarded for BAFU stations `2009` / `2091`.

**Protocol (resolution #5 — pinned)**: the model is a `forecastinterface` model
**onboarded via the existing Plan 076 `ForecastInterfaceAdapter`**, which surfaces
it as a per-station **`StationForecastModel`** (`artifact_scope =
ArtifactScope.STATION`), matching the three existing v0 sample models and exercised
through the per-station forecast loop (not `run_group_forecast.py`). **The FI→native
boundary is already plumbed** — `_project_requirements` maps FI
`InputRequirement.future_known` → `future_dynamic_features`
(`adapters/forecast_interface.py:452-475`) and `_future_known_inputs` (`:881-896`)
delivers future forcing to the FI model — so M2's boundary work is **"use it," not
"build it."** M2's real content is the **linear-regression model logic**
(precip/temp predictors + a discharge lookback), its training, and onboarding (via
`adapt_if_fi` at discovery) for `2009`/`2091`.

**Ensemble contract (resolution #1 — pinned)**: the model is trained on a
**single deterministic daily forcing trajectory** (M1 reanalysis,
`member_id = None`). The FI `FutureKnownVariable.ensemble_mode` is set to
**`ENSEMBLE`** to declare it accepts the 21-member ICON forcing; the model itself
does **not** know about ICON members. At inference, an ensemble forecast is produced
by the adapter/cycle **mapping the model's `predict` over each forcing member**
(21 ICON members → 21 discharge trajectories → a 21-member `ForecastEnsemble`),
consistent with this resolution. M2's own acceptance is verified with a
deterministic forcing input; the member-mapping wiring lives in M3. The model must
**not** internally collapse members to a mean.

**Maps to / supersedes**: **new plan** (no existing plan owns it). Resolves the
Phase-8 v0b "NWP-consuming model" gap.

**dependsOn**: **M1** (needs the canonical forcing schema + historical forcing
rows to train).

**New components introduced**:
- A new **`forecastinterface` model** (linear regression with NWP features)
  declaring its `InputRequirement` (`future_known` precip/temp, `past_known`
  discharge); onboarded through the **existing** `ForecastInterfaceAdapter` — no new
  adapter, no native-Protocol-from-scratch implementation.
- Its training + onboarding wiring (existing `train_models` / `onboard_model`
  framework + `adapt_if_fi` discovery; no new framework).

**acceptanceCriteria** (test-verifiable, member-dim aware):
1. The FI model's `InputRequirement` declares `future_known` precip/temp **against
   the M1 canonical forcing schema** (resolution #6); once wrapped by
   `ForecastInterfaceAdapter`, the surfaced
   `data_requirements.future_dynamic_features` equals `{precipitation, temperature}`
   and `artifact_scope == ArtifactScope.STATION`, and the adapted model satisfies
   the `StationForecastModel` runtime-checkable Protocol.
2. `train(...)` on M1 historical daily forcing + discharge for `2009`/`2091`
   produces a serializable `ModelArtifact`; round-trips through
   `serialize_artifact`/`deserialize_artifact`.
3. `predict(...)` on a **single deterministic daily forcing** input (one
   trajectory, daily rows matching the trained resolution) returns
   `dict[str, ForecastEnsemble]` for `discharge` — i.e. the model consumes
   future-dynamic forcing and the call shape is correct.
4. **Member-mapping property**: given N distinct deterministic forcing
   trajectories run through `predict` independently, the N resulting discharge
   trajectories are combinable into an N-member `ForecastEnsemble`
   (`from_members`) without the model collapsing or averaging the inputs — i.e.
   member spread is preserved by construction. (The operational mapping over 21
   ICON members is exercised end-to-end in M3.)
5. Onboarding marks `2009`/`2091` operational with an ACTIVE artifact for this
   model (per the Plan 084 onboarding gate semantics).

### M3 — Operational ICON forcing path (binding + per-member daily aggregation)

**Goal**: Activate the NWP-on operational cycle. Bind `2009`/`2091` to
`nwp_source = icon_ch2_eps` so the **already-merged** 086 archive + 087
`MeshBasinExtractor` run (resolving the `no_matching_sources` skip); add the
**net-new per-member hourly→daily aggregation** between mesh extraction and
model-input assembly; **deliver the extracted basin-average forcing into
`StationModelInputs.future_dynamic`** so the **FI-wrapped M2 model consumes it**,
producing a 21-member discharge ensemble. (Delivery into `future_dynamic` already
exists — `operational_inputs._pivot_nwp_records` at `:207`, assembled at `:244`; M3
inserts the daily aggregation **before** that pivot. It is the *aggregation*, not
the delivery, that is net-new.)

**Maps to / supersedes**: **new plan** (operational wiring + aggregation). Builds
**on** Plans 086 + 087 **and the Plan 076 `ForecastInterfaceAdapter`** (all DONE
dependencies — *not* re-implemented; see the M3 note above and resolution #3).

**dependsOn**: **M2** (the aggregation target resolution and the inference call
need the trained model). **086 + 087 + the FI adapter are prerequisite
infrastructure, already done** — they are not blockers gated behind M1/M2; M3
consumes their output.

**New components introduced**:
- **Per-member hourly→daily aggregation step** (resolution #2): a net-new
  transform, sited between `MeshBasinExtractor` extraction and
  `operational_inputs` model-input assembly, that buckets ICON's hourly
  basin-average records into **calendar-day valid-time buckets**, **accumulating
  precipitation** (FI `AggregationMethod.SUM`) and **averaging temperature** (FI
  `AggregationMethod.MEAN`), **independently per ICON member**. This is
  **aggregation, NOT disaggregation**: it accumulates/averages real hourly data
  into the daily step and invents no sub-daily structure (the owner's
  "don't disaggregate, uncertainties too high" concern targets the opposite
  daily→sub-daily direction, which this epic does **not** do).
  `operational_inputs._pivot_nwp_records` does not aggregate today; the step must
  run before pivoting so the `future_dynamic` frame has daily rows the M2 model
  expects. The member axis is **retained** through extract → aggregate → model
  input.
- Station↔`icon_ch2_eps` weather-source binding for `2009`/`2091` (config /
  onboarding), which makes `run_forecast_cycle.py:318-328` stop skipping with
  `no_matching_sources`.
- The per-member inference mapping (resolution #1): drive M2's `predict` once per
  ICON member, assemble a 21-member `ForecastEnsemble`.

**acceptanceCriteria** (test-verifiable, member-dim aware):
1. With `2009`/`2091` bound to `icon_ch2_eps`, the cycle runs 087's
   `MeshBasinExtractor` (no `no_matching_sources` skip) and produces
   `BasinAverageForecast` basin-average forcing **retaining all 21 members**
   (member dimension present in the extracted records).
2. The per-member daily-aggregation step converts hourly ICON basin-average
   records to **daily** records with **precipitation accumulated** and
   **temperature averaged** over each calendar-day valid-time bucket, **computed
   independently per member** (a record-count and per-member value assertion on a
   fixture with known hourly inputs).
3. The aggregated daily forcing validates against the **M1 canonical forcing
   schema** (resolution #6) — same variable names, units, daily resolution,
   basin-average representation as the M1 raster path.
4. End-to-end: one NWP-on `forecast-cycle` for `2009`/`2091` produces a stored
   **21-member discharge `ForecastEnsemble`** per station (member count == ICON
   member count), with members **not** collapsed to a mean before inference.
5. Memory stays bounded across the fetch→archive→extract→aggregate→infer path
   (086's streaming guarantee is not regressed by the added aggregation/inference
   — assert no full-set materialization in the new step).

### M4 — Forecast provenance + validation (design-gate-first)

**Goal**: Make a forecast's input provenance explicit and correct so NWP-primary,
NWP-fallback, and runoff-only forecasts are distinguishable via schema + API; then
record the consequential Plan 084 doc update.

**Maps to / supersedes**: refines Plan **078** (the parked provenance plan) and
updates Plan **084** (the doc update is a *consequence* of M3, not standalone
work).

**dependsOn**: **M3** (M3's NWP-on cycle is what makes mixed provenance —
runoff-only vs NWP-primary vs NWP-fallback — a live, load-bearing distinction).

**M4 is split into three steps (resolution #4):**

- **M4(a) — design gate (human-in-the-loop, NOT auto-implementable).** Run Plan
  078's grill-me session to **resolve the provenance representation**. The choice
  is open and must NOT be hardcoded by this epic: (i) a new `NwpCycleSource` value
  (`NONE`/`NOT_APPLICABLE`) vs (ii) nullable `nwp_cycle_reference_time` vs (iii) a
  broader nested **input-provenance** object. The gate also decides the
  **breaking-change/backfill** question: is the API contract change breaking for
  external consumers, and are the **existing rows** (including M3's dev-validation
  forecast rows, written under the legacy `NOT NULL` + `CHECK` schema before M4
  settles) **left as-is or backfilled**? This step's output is a re-drafted Plan
  078 with a concrete representation, migration strategy, and JSON dependency
  graph — *then* it flips DRAFT→READY.
- **M4(b) — implementation (auto-implementable once M4(a) decides).** Implement the
  chosen representation: schema/migration, what `run_forecast_cycle.py` records per
  mode (NWP-primary / NWP-fallback / runoff-only), API response contract,
  `input_quality` messaging. Acceptance criteria for M4(b) are restated against the
  representation M4(a) picks (they cannot be fully pinned here — see Open questions).
- **M4(c) — Plan 084 doc update (consequence of M3).** Mark the optional-NWP /
  NWP-on path validated in Plan 084 once M3's end-to-end cycle is green. This is a
  documentation consequence, noted as such, not independent engineering.

**acceptanceCriteria** (test-verifiable; the two non-testable draft ACs are
dropped/rephrased per resolution #4):
1. *(M4b, shape pinned by M4a)* A runoff-only forecast persists and exposes an
   **explicit runoff-only provenance state** distinguishable, via schema query and
   API response, from an NWP-primary forecast — **without** writing a fake NWP
   cycle reference that misrepresents "no NWP". (The concrete column/enum/object
   shape is whatever M4(a) selects; the *behavioral* assertion — runoff-only is
   distinguishable and not a fake-primary — is representation-agnostic.)
2. *(M4b)* An NWP-primary and an NWP-fallback forecast each persist and expose
   their respective provenance states; the API surfaces all three states
   distinctly.
3. *(M4b)* `input_quality` human-readable messaging renders a distinct string for
   runoff-only vs primary vs fallback (assert on the rendered message for each
   mode).
4. *(M4b)* The Alembic migration applies and existing rows follow the M4(a)
   leave-as-is-vs-backfill decision (assert post-migration row state matches the
   decision).

> The vision-decompose draft's two process ACs ("Plan 078 records the
> user-confirmed API decision…" and "Plan 084 is updated to state…") are **not**
> automated acceptance gates. The first is folded into **M4(a)** (the design gate
> output); the second is **M4(c)** (a doc consequence). Neither is an M4(b)
> acceptance test.

---

## Resolved design decisions (pinned for WF2)

1. **Ensemble — per-member inference, no pre-inference collapse (load-bearing).**
   ICON-CH2-EPS is a 21-member ensemble; M1 reanalysis training forcing is a
   single deterministic daily series (`member_id = None`). The operational ICON
   basin-average forcing **retains the member dimension**; daily aggregation runs
   **per member**; the deterministic-trained M2 model runs **inference once per
   member**, yielding a 21-member discharge `ForecastEnsemble`. The member axis is
   the load-bearing forecast-spread source and is preserved end-to-end
   (extract → aggregate → infer → persist). Verified feasible against
   `types/ensemble.py` (`ForecastEnsemble.from_members`) and the per-station
   `StationForecastModel.predict` signature. This honours the locked ensemble-first
   decision.
2. **Daily aggregation is net-new and owned by M3 — aggregation, NOT
   disaggregation.** ICON is hourly; M1 training/model resolution is daily. A new
   hourly→daily aggregation step (precipitation **accumulated** via FI
   `AggregationMethod.SUM`, temperature **averaged** via `MEAN` over each
   calendar-day valid-time bucket, **per member**) sits between 087's
   `MeshBasinExtractor` and `operational_inputs` assembly. It accumulates/averages
   real hourly data into the daily step and invents **no** sub-daily structure — the
   owner's "don't disaggregate, uncertainties too high" concern targets the opposite
   daily→sub-daily direction, which this epic does **not** do. Verified that
   `operational_inputs._pivot_nwp_records` does not aggregate (preserves raw
   `valid_time`; `resample_to_time_step` only touches `past_targets`); delivery of
   the pivoted frame into `StationModelInputs.future_dynamic` **already exists**
   (`:207`, assembled at `:244`), so M3 adds only the pre-pivot aggregation. This
   step exists in neither 086, 087, nor the current operational path.
3. **M3 builds on the merged 086/087 — does not re-implement them.** 086 (OOM/
   streaming) and 087 (mesh extraction) are READY/merged and model-independent;
   they are M3 **dependencies (done)**, not M3 work. M3 = station weather-source
   binding + per-member daily aggregation + feeding the M2 model. (This also
   resolves the advisory that M3-as-drafted stalled the model-independent 086/087:
   they are already done and are not gated behind M1/M2.)
4. **M4 is design-gate-first.** Split into M4(a) grill-me design gate (resolves
   representation + breaking-change/backfill — **not hardcoded**), M4(b)
   implementation against the chosen representation, M4(c) the Plan 084 doc
   consequence. Every M4(b) acceptance criterion is behavioral/test-verifiable;
   the two process ACs from the draft are reassigned to M4(a)/M4(c). M3's
   dev-validation rows are written under the legacy schema before M4 settles —
   the leave-as-is-vs-backfill call is explicitly part of M4(a).
5. **M2 model = a `forecastinterface` model onboarded via the EXISTING Plan 076
   `ForecastInterfaceAdapter`, surfacing as a `StationForecastModel`** (owner
   decision; supersedes the rev-1 "native `StationForecastModel` from scratch"
   framing). Per-station (`ArtifactScope.STATION`), matching the three v0 sample
   models; exercised through the per-station forecast loop, not
   `run_group_forecast.py`. The FI→native boundary is **already plumbed** —
   `_project_requirements` maps FI `InputRequirement.future_known` →
   `future_dynamic_features` (`adapters/forecast_interface.py:452-475`);
   `_future_known_inputs` (`:881-896`) delivers future forcing — so M2's boundary
   work is **"use it," not "build it."** M2's real content is the linear-regression
   model logic + training + onboarding for `2009`/`2091`. The model is a **linear
   regression with NWP features**; ensemble forcing is declared via FI
   `FutureKnownVariable.ensemble_mode = ENSEMBLE`, with per-member `predict` mapping
   (no collapse-to-mean) per resolution #1.
6. **Shared canonical forcing-schema contract (pinned artifact); units anchored to
   the FI `Unit` enum.** M1 emits an explicit, committed contract — canonical
   variable names (`precipitation`, `temperature`; aligned to the v0 `tp`/`t_2m`
   allowlist), **daily** temporal resolution, basin-average `SpatialRepresentation`,
   and **frozen units in FI-enum terms** (owner: "use the ForecastInterface
   definitions"): precipitation = `Unit.MM` (`"mm"`, daily-accumulated per
   day-step) — **NOT `mm/day`**; temperature = `Unit.DEG_C` (`"°C"`); target
   discharge = `Unit.M3_PER_S` (`"m³/s"`). `mm/day` is deliberately **rejected** at
   the SAP3 boundary — `_FI_UNIT_TO_CANONICAL`
   (`adapters/forecast_interface.py:112-123`) omits `MM_PER_DAY` — so the contract
   uses `Unit.MM`. Reconciliation: ICON `tp` (accumulated kg/m² ≡ mm) →
   de-accumulate to per-step mm → daily SUM = daily-accumulated mm; ICON `t_2m` (K)
   → °C → daily MEAN; reanalysis RprelimD daily mm and TabsD daily °C are native. M2
   declares its `future_known` variables against this; **both** extractors (M1
   raster reanalysis, M3 ICON mesh) are validated against it **independently**. This
   is a concrete M1 deliverable/decision, not "same as M1 by inspection."
7. **Model timestep = DAILY now; sub-daily (ERA5-Land) is a deferred follow-on
   epic** (owner decision, investigation-backed). There is **no free Swiss gridded
   *hourly historical* product** — the only free Swiss gridded history is **daily**
   (`ch.meteoschweiz.ogd-surface-derived-grid`, Plan 071's source); hourly Swiss
   options are forecast-only (nowcasting), commercial (CombiPrecip/INCA), or point
   (SwissMetNet, which the architecture deliberately left). The only
   sub-daily + gridded + historical source is **ERA5-Land** (hourly, ~9 km, CDS
   API/auth, not yet wired — the Nepal-v1 reanalysis path). This epic therefore ships
   a **daily** model: M1 stays on Plan 071's daily Swiss reanalysis; M3 keeps the
   ICON hourly→daily per-member **aggregation** (resolution #2, aggregation not
   disaggregation). The sub-daily track is captured under *Follow-on epic (deferred):
   ERA5-Land sub-daily forcing* and is **out of scope** for this epic.

**Minor resolutions folded in:**
- **084 doc-update placement**: it is a *consequence* of M3 (M4(c)), not standalone
  M4 engineering.
- **M3-not-086/087-refinement framing**: removed all "refine/implement 086/087"
  language; 086/087 are DONE dependencies.
- **Cross-milestone schema cross-reference → pinned artifact**: the "same schema
  as M1" cross-reference is replaced by the committed shared-contract artifact
  (resolution #6) that both extractors test against.

---

## Follow-on epic (deferred): ERA5-Land sub-daily forcing

**Out of scope for this epic — a later epic, not a milestone here** (resolution #7).

A sub-daily (hourly) NWP-on forecasting track is a **forward-investment for Nepal
v1** but is explicitly deferred. It would add:

- A new **`WeatherReanalysisSource` ERA5-Land hourly adapter** (`cdsapi`; requires a
  **CDS account / Copernicus licence**) as M1's **sub-daily training source**
  (hourly, ~9 km).
- An **hourly model timestep** end-to-end (no ICON→daily aggregation; the hourly
  ICON forcing is consumed directly).
- Acceptance of the **ERA5 (~9 km) ↔ ICON (~2 km) cross-source spatial-resolution
  bias** as a managed **bias-correction** concern (training source ≠ operational
  source).

This is **not** a milestone of the present (daily) epic; it is recorded so the daily
decision (resolution #7) is traceable to a planned sub-daily successor.

---

## Per-milestone WF2 readiness

Each milestone is promoted to its **own** plan and run through the standard
draft→self-review→user-confirm→READY gate (`docs/workflow.md`) **before** its WF2
(`vision-build`) run. This epic authorises none of them directly.

| Milestone | Plan action | Notes |
|---|---|---|
| **M1** | Promote Plans **071 + 072** DRAFT→READY (refine: add the shared forcing-schema contract artifact + the `member_id=None` reanalysis clause). | Sequenced 071→072 per 072's hard dependency. |
| **M2** | **New plan** (`forecastinterface` linear-regression model with NWP `future_known`, onboarded via the existing `ForecastInterfaceAdapter`). | Depends on M1 schema + forcing rows; reuses the Plan 076 adapter (boundary already plumbed). |
| **M3** | **New plan** (operational ICON binding + per-member daily aggregation + member-mapping inference). | Builds on merged 086/087; depends on M2. |
| **M4** | Refine Plan **078** (after its M4(a) grill-me gate) + update Plan **084** (M4(c)). | 078 stays parked until M4(a) resolves representation. |

---

## Open questions for the human (resolve before M1's WF2)

1. **Reanalysis↔ICON unit reconciliation — RESOLVED (resolution #6).** Units are
   frozen in FI-enum terms: precipitation = `Unit.MM` (`"mm"`, daily-accumulated;
   **not `mm/day`**, which the SAP3 FI adapter rejects), temperature =
   `Unit.DEG_C` (`"°C"`), discharge = `Unit.M3_PER_S` (`"m³/s"`). Reconciliation:
   ICON `tp` (accumulated kg/m² ≡ mm) → de-accumulate to per-step mm → daily **SUM**
   = daily-accumulated mm; ICON `t_2m` (K) → °C → daily **MEAN**; MeteoSwiss
   RprelimD (daily mm) and TabsD (daily °C) are native. The only remaining item is
   an **implementation detail** — the de-accumulation/normalization site (M1 adapter
   for reanalysis, M3 aggregation for ICON) — not an open design question.
2. **Model family for M2 — RESOLVED.** The v0b NWP-consuming model is a **linear
   regression with NWP features** (owner-confirmed): precip/temp predictors + a
   discharge lookback, authored as a `forecastinterface` model and onboarded via the
   existing `ForecastInterfaceAdapter` (resolution #5). The per-member inference cost
   (21× `predict`) is unchanged and tracked under Open question #4.
3. **078 provenance representation (M4a) — UNCHANGED, still deferred to M4(a)'s
   grill-me gate.** Genuinely undecided: new `NwpCycleSource` value vs nullable
   `nwp_cycle_reference_time` vs nested input-provenance object, plus the
   breaking-change/backfill decision for existing (including M3 dev-validation) rows.
   M4(b) ACs cannot be fully pinned until this resolves.
4. **Member-mapping inference cost at scale.** 21× `predict` per station is fine
   for the 2-station validation; at the v0 ~1000-station target it is 21 000
   inferences per cycle. Whether that needs `task.map` parallelisation (the
   deferred Phase-8 v0b remainder) or is acceptable serial should be confirmed
   before M3's plan goes READY (does not block M1/M2).
5. **Reanalysis 2-day live-tail gap interaction.** Plan 072 documents MeteoSwiss
   RprelimD's ~2-day publication lag as a known `past_dynamic` gap. Confirm this
   does not interact badly with an NWP-on model that also consumes `past_dynamic`
   forcing for `2009`/`2091` (M2/M3) — i.e. the model's truncation tolerance.

---

## Changelog

- **2026-06-30 (rev 1, this document)** — WF1 sign-off. Folded the eight MAJOR
  vision-decompose advisories into the M1-M4 chain via the six numbered design
  resolutions (+ minors): ensemble per-member inference, net-new per-member daily
  aggregation owned by M3, M3-builds-on-merged-086/087, M4 design-gate-first,
  M2 = `StationForecastModel` (native), and the pinned shared forcing-schema
  contract. Status DRAFT pending user confirmation.
- **2026-06-30 (rev 2, this document)** — Folded in three owner decisions + one
  verified code finding. (1) **Timestep = DAILY now, ERA5-Land sub-daily LATER**
  (new resolution #7): no free Swiss gridded *hourly historical* product exists, so
  the epic ships a daily model; the sub-daily track moves to a new deferred
  *Follow-on epic (ERA5-Land sub-daily forcing)* section. M3's ICON hourly→daily step
  reframed explicitly as **aggregation, NOT disaggregation** (FI
  `AggregationMethod.SUM`/`MEAN`) across Vision, M3, and resolution #2.
  (2) **M2 = a `forecastinterface` model onboarded via the EXISTING Plan 076
  `ForecastInterfaceAdapter`** (resolution #5 reframed from "native
  `StationForecastModel` from scratch"; WF2 table + ACs updated): verified the
  FI→native boundary is already plumbed (`_project_requirements` `:452-475`,
  `_future_known_inputs` `:881-896`); M2 is a **linear regression with NWP features**
  (`future_known` precip/temp + `past_known` discharge), `ensemble_mode = ENSEMBLE`,
  `ArtifactScope.STATION`. (3) **Units anchored to the FI `Unit` enum**
  (resolution #6): precipitation = `Unit.MM` (NOT `mm/day`, which
  `_FI_UNIT_TO_CANONICAL:112-123` rejects), temperature = `Unit.DEG_C`, discharge =
  `Unit.M3_PER_S`. Verified finding: delivery of forcing into
  `StationModelInputs.future_dynamic` already exists (`operational_inputs:207/244`);
  only the pre-pivot daily aggregation is net-new for M3. Open questions #1 (units)
  and #2 (model family) → **RESOLVED**; #3 (provenance) unchanged, still deferred to
  M4(a). Status remains DRAFT.
