# Forecast-cycle redesign — forcing tracks, per-assignment run context, probabilistic multi-track

**Status:** DRAFT design doc — created 2026-07-23, **hardened through THREE independent Codex reviews** (round 1:
architecture direction; round 2: contract structure; round 3: contract precision — all folded; round 3 returned
**no blockers**). **Supersedes the incremental patching of Plans 126 + 144** (folded here). Decisions locked in
`docs/design/v1-forecasting-decisions.md`. **Ready to slice the build sequence into implementation plans.**
Control-only forecasting is live and must stay green at every phase.

## Why this doc exists
Six `/plan` runs on the ensemble-forecasting cluster (142 ×2, 144, 145, 126) stalled — the final 126 pass proved
the failures are **load-bearing assumptions in the v0 forecast cycle**, not plan-doc quality. This doc specifies
the architecture change. **Round-1 Codex review (2026-07-23) confirmed the diagnosis but corrected the design**;
its findings are folded below (chiefly: fetch by *deduplicated forcing track*, not per-assignment; build the
per-assignment run context *first*; the ensemble fan-out already exists; control-only is a fallback *chain*, not
a degenerate single requirement).

## The three structural blockers (verified in code, with the round-1 qualifications)
1. **One cycle per batch — the flow cannot represent heterogeneous per-track cycles.** Phase A stores every
   station result, then picks the **first** result's cycle as the batch cycle
   (`run_forecast_cycle.py:1126,1141,1155`); Phase B turns that into **one** `nwp_cycle_reference_time` +
   readback cycle for every station/group (`:1722,1733,1740`). *Qualification:* Recap resolves one cycle globally
   before fetching (`recap_gateway.py:673,689,724`), so it does not currently emit heterogeneous cycles — the
   blocker is the **inability to support** a CONTROL-fresh + ENSEMBLE-older-00Z split, not an observed Recap bug.
2. **Per-feature horizon is lost — TWO independent collapses.** (a) *Cross-assignment superset collapse*:
   `build_superset_requirements` unions features and takes the **max** horizon
   (`operational_inputs.py:303,310,320`). (b) *Within-FI-requirement collapse*: the FI adapter irreversibly
   `max`-collapses per-variable `future_steps` at construction (`forecast_interface.py:471,476,479`), and
   assembly caps to one scalar horizon (`operational_inputs.py:463,466,505,514`). "precip 2 steps + temp 10
   steps" stays rectangular unless the FI boundary slices per variable. Per-assignment work fixes only (a).
3. **One assembly / one mode per station — an *unsupported configuration*, not silent corruption.** The
   first-priority assignment sets the `time_step`; heterogeneous steps only **warn** and still use the first
   (`run_forecast_cycle.py:1787,1800,1838,1843`). Mixed SINGLE/ENSEMBLE is **explicitly rejected** by
   `build_superset_requirements` (`operational_inputs.py:286,293,295`) — so today it's a hard-fail config, which
   this redesign makes a supported one.

## What already exists — reuse (some of it is more built than the first draft assumed)
- **The ensemble fan-out is already WIRED, not just present.** `run_station_forecast.py:165-190` rejects prior
  state, slices per-member forcing, calls `predict` per member, and reconstructs the ensemble via
  `ensemble_fanout.py:126-141` + `ForecastEnsemble.from_members`; QC already runs on the reconstruction
  (`run_station_forecast.py:224,230`). **So this redesign ROUTES assignment-specific inputs into the existing
  runner — it does not build fan-out.**
- **The store already supports multiple cycles** — `cycle_time` is in readback filtering
  (`weather_forecast_store.py:49,60`) and the natural unique key (`db/metadata.py:733,737`). **No weather-store
  schema migration** for a per-track cycle map. Rejected candidate rows do **not** reach Postgres (rows persist
  only after the adapter returns, `run_forecast_cycle.py:1126,1141`) — contamination risk is only in Recap's
  **in-memory** accumulator (partial `pf`, `recap_gateway.py:727,747,769`).
- **Forecast provenance is already per-forecast** (`types/forecast.py:48,54`; nullable cycle ref
  `db/metadata.py:987,1003`) — different models at one issue time need no forecast-schema migration.
- **Plans 134** (control 6h bridge), **145/146** (snow forcing), **143** (onboarding), `nwp_coverage`,
  `_resolve_effective_cycle`/`resolve_latest_cycle`.

## Target architecture

**Two units, cleanly separated (round-1's central correction): fetch by *deduplicated forcing track*; assemble +
run by *assignment*.** Per-assignment fetch is too granular (repeated 51-member downloads); per-station is too
coarse.

- **`ForcingTrackKey` = (nwp source, ensemble mode, time_step, per-feature horizons, spatial representation).**
  Project every active assignment to a track; **deduplicate identical tracks across assignments/stations**;
  resolve + fetch **once per track**. This is the fetch/resolution unit.
- **A track resolves to ONE cycle; per-station availability varies (round-3 fix).** The track's walk-back picks
  a **single** `resolved_cycle` for the whole track — matching Recap, which resolves one cycle globally by
  probing one HRU (`recap_gateway.py:673`). At that cycle, availability is **per-station**: Recap accepts partial
  spatial resolution and returns only successfully-accumulated stations (`recap_gateway.py:716,778`), and
  readback is station-keyed (`weather_forecast_store.py:49`). So resolution yields
  `TrackFetchResult(resolved_cycle, Mapping[station_id, StationTrackOutcome])`, where a `StationTrackOutcome` is
  *available(records + provenance)* or *unavailable*. There is **no global `map[track→cycle]` second source of
  truth** — contexts are built directly from these outcomes. (Independent per-station walk-back is explicitly
  NOT supported: one cycle per track, availability varies.)
- **`ModelRunContext` (assignment-keyed) = inputs + input metadata + resolved NWP provenance + `prior_state`.**
  The **assignment key is `(station_id, model_id)`** for station assignments and `(group_id, model_id)` for
  groups (round-3: `ModelAssignment` has no id, `types/station.py:55`; assert key uniqueness). This is the
  assemble/run unit and the consumer that makes a per-track cycle non-dormant. **Its construction returns a
  per-assignment success/failure result** — a station that is *unavailable* for its assignment's track turns that
  assignment into an **assignment-local failure** that advances the fallback chain; it must **never** abort the
  whole station's context construction (today the assembler returns `None` when NWP is absent and skips the
  *entire station*, `operational_inputs.py:442` → `run_forecast_cycle.py:1875` — that path is what this changes).

Components:
1. **Track projection with per-feature horizons — three concrete horizon types (round-2 fix).** Derive each
   assignment's `(feature→future_steps map, time_step, mode, spatial)` at the FI boundary **before** the
   max-collapse (`forecast_interface.py:471,476,479`). Do **not** reuse `ModelDataRequirements` (one scalar
   `forecast_horizon_steps`, `types/model.py:262`) as the fetch requirement. Three distinct, typed horizons with
   explicit ownership:
   - **`FeatureFetchHorizons: Mapping[feature, int]`** — the fetch-acceptance contract; owned by the track
     projection, derived per-feature at the FI boundary. A cycle is accepted iff every feature has ≥ its own steps.
   - **`InputFrameHorizon: int`** — the rectangular assembled-frame horizon (= max of the feature horizons),
     owned by assembly; the FI boundary then **slices each variable to its own `future_steps`** before the
     per-variable NaN gate (`forecast_interface.py:705,717`), so a short-horizon feature isn't rejected for
     lacking long-horizon steps.
   - **`OutputHorizon: int`** — the forecast horizon the model emits; separate from forcing, owned by the model.
   Non-FI models project into these via their declared requirements (single-feature-horizon = the scalar case).
2. **A requirement-aware source contract + per-track cycle resolution.** Add
   `fetch_requirement(track, stations, nominal_cycle) -> CandidateFetchResult` (immutable) alongside the existing
   `fetch_forecasts` (kept as a compatibility adapter until control has migrated). Per track, walk back to the
   latest cycle satisfying its completeness (D1: exact-51 for ENSEMBLE features; `fc` for CONTROL) + per-feature
   horizon, bounded by `max_cycle_age_hours` (D4). Because `pf` is 00Z-only (D3), ENSEMBLE tracks resolve to the
   latest 00Z (once/day). Resolution yields `TrackFetchResult(resolved_cycle, station_outcomes)` per track (see
   above); readback + provenance key off each `(track, station)` outcome's cycle. **Candidate-local, immutable
   accumulation**: validate each candidate into a fresh result, commit only on full pass (fixes the Recap
   in-memory partial-`pf` contamination — `recap_gateway.py:727,747,769`).
3. **Assignment-keyed `ModelRunContext` (build this FIRST — see phases).** Replace the single shared
   assembly/metadata/state passed to every assignment (`run_station_forecast.py:334,336,348`) with a per-assignment
   context; `prior_state` loaded by `(station_id, model_id)` (fixes the today-latent shared-`prior_state` bug for
   heterogeneous stateful assignments; ensemble fan-out stays stateless per `ensemble_fanout.py:47,56`).
4. **Per-assignment assembly from shared stored rows.** Each assignment assembles its own frame (its features,
   its time_step, its horizon) from the rows fetched by its track — no cross-assignment superset.
5. **Route into the existing fan-out runner.** ENSEMBLE assignments already fan out per member; CONTROL runs
   member 0. This redesign feeds them assignment-specific inputs; it does not rebuild the runner.
6. **exact-51 + survival — designed, not just named (round-2 fix).** Nothing enforces `{0..50}` today: coverage
   checks identical member sets but not the exact set (`nwp_coverage.py:92,106`); fan-out reconciles equality
   without requiring 0..50 (`ensemble_fanout.py:171`); `min_operational_ensemble_size` gates **alerts**, not
   production (`alert_checker.py:181-188`). Three explicit, separate checks:
   - **Input completeness** — one validator type whose invariant is exactly `member_ids == frozenset(range(51))`
     (+ each feature's horizon), applied at **fetch acceptance** and reused as a pre-run defensive assert.
   - **v1 is all-51-or-fail, NOT member survival (round-3 fix).** The existing fan-out is already all-or-nothing:
     it aborts on any member's prediction exception and only reconstructs once **every** member call succeeds
     (`ensemble_fanout.py:126`), and QC fails a whole parameter without removing members
     (`run_station_forecast.py:224`). So v1 requires **all 51 member predictions**; a member failure fails the
     **assignment** and the fallback chain advances (per assignment, before persistence/combination). There is
     **no post-prediction member-dropping** and no production `min_operational_ensemble_size` gate in v1 — a
     member-survival mechanism (member-local outcome capture, which failures are survivable, cross-parameter
     member-set reconciliation, a production min-count gate) is a **named follow-on**, not this redesign.
   - **Alert eligibility** — unchanged (`min_operational_ensemble_size` keeps gating **alert** evaluation only,
     `alert_checker.py:181-188`).

### Fallback-chain semantics (round-1: control-only is NOT a degenerate single requirement)
A station carries a **list of assignments run as a priority fallback chain** (`run_forecast_cycle.py:1781,1887`);
runoff-only mode strips future features and lets per-model coverage/fallback decide (`:1826,1832`). So an
ordinary CONTROL station is **many** assignments, not one requirement. The redesign must **preserve the chain**:
an assignment whose track/cycle is unavailable **advances to the next assignment**, it does **not** mark the
station dark. Equivalent assignments deduplicate onto one track, so this adds no extra fetches.

**Compatibility is a golden-test invariant, not a safety claim (round-2 fix).** CONTROL assignments can differ
in source/features/time_step/horizon/spatial — the exact `ForcingTrackKey` fields — and today heterogeneous time
steps merely collapse onto the first assignment (`run_forecast_cycle.py:1787,1838`) while requirements are
unioned/maxed (`operational_inputs.py:303,319`). So the invariant is: a **homogeneous** control station (all
assignments share a track) produces exactly one track and today's behaviour; a **heterogeneous** control station
is an **intentional behaviour change** (each assignment gets its own track/assembly instead of silent collapse)
that must be pinned by **golden tests** enumerating the new per-assignment outputs. "Control-only stays green"
means the homogeneous case, verified by golden tests — not that every control config is byte-identical.

### Group models — only group ENSEMBLE fan-out is out of scope (round-2 fix)
Round-1 said "exclude groups"; round-2 showed that is too broad. Group prediction calls `predict_batch` **once**
with **no ensemble-forcing fan-out** (`run_group_forecast.py:110,131,136,440,442`), so operational **group
ensembles are a named follow-on**. **But existing SINGLE group-control assignments must NOT be dropped from track
discovery** — group-control assembly reads the shared cycle for every member today
(`run_forecast_cycle.py:2193,2198,2240`; `run_group_forecast.py:128`), so a requirement used **only** by a group
assignment must still project a track and resolve, or group-control breaks during migration. Scope: **include
SINGLE group assignments in track discovery/resolution** (or preserve a dedicated legacy control track for the
group path); **exclude only group ensemble fan-out** until its follow-on. **Group-CONTROL cycle contract
(round-3):** a group assignment requires **one common acceptable cycle for the whole group** and **fails
atomically** if any member station is unavailable at it — matching today's shared-cycle group assembly
(`run_group_forecast.py:110,359`; `run_forecast_cycle.py:2193,2240`). Per-station group cycles are a follow-on
(with group ensembles), not this redesign.

## Locked decisions (see `docs/design/v1-forecasting-decisions.md`)
D1 exact-51 + walk-back · D3 `pf` 00Z-only (ensemble once/day now, 4×/day later) · D4 walk-back-only · units
canonical from the recap adapter · reuse the already-wired `ensemble_fanout`/`ForecastEnsemble`/`forecast_qc`.

## What this supersedes / relates to
- **Supersedes** Plan 126 (→ components 2 + candidate-local accumulation) and Plan 144 (→ components 1/4/5).
  Both marked `SUPERSEDED`.
- **Separate:** 134 (control bridge), 145/146 (snow forcing), 143 (onboarding). Reuses the existing fan-out stack.

## Build sequence (re-ordered per round-1/2; control-only green at every step)
1. **Assignment-keyed `ModelRunContext` + split prior-state loading (round-2).** Per-assignment inputs +
   metadata + provenance + `prior_state`. Note state is already fetched by `(station_id, model_id)` inside
   assembly (`operational_inputs.py:489`) but today that model id is only the *assembly* assignment
   (`run_forecast_cycle.py:1851`) — so Phase 1 must **split prior-state loading from the shared input assembly**
   and load state per assignment, with a test proving two deterministic assignments get distinct states
   (ensemble rejection unchanged, `ensemble_fanout.py:47`). Inputs are still filled from today's shared frame
   (behaviour-preserving); this is the consumer everything else needs.
2. **Migrate the station runner** to consume per-assignment `ModelRunContext`, returning a per-assignment
   success/failure result (fallback chain intact; a missing context ≠ a dead station). Still one cycle.
3. **`ForcingTrackKey` projection + per-track resolution + per-assignment assembly — ONE atomic phase (round-2
   merged old 3+4).** A per-`(track,station)` cycle has no coherent consumer while assembly is a single shared
   frame, so track resolution and per-assignment assembly land **together**: deduplicated tracks, the
   requirement-aware source contract (`fetch_requirement(...) -> CandidateFetchResult`), candidate-local
   immutable results, `TrackFetchResult(cycle, station_outcomes)` (no global track→cycle map), per-assignment
   assembly (drop the station superset; the three horizon types + FI per-variable slice), and exact-51
   input-completeness + survival checks. **Combined-forecast provenance decided here (round-2):** cross-cycle
   model combination is **disabled/fail-loud** in this redesign (a richer combined-provenance rule is a
   follow-on) — `run_forecast_cycle.py:2031,2035` currently exposes one shared cycle ref.
4. **Remove the legacy station-superset path** once all consumers are on `ModelRunContext`.
5. **Follow-ons (separate plans):** group-ensemble requirements + group fan-out (group **control** stays in
   scope via track discovery, see Group models); hindcast/operational parity (`hindcast.py:328,331,354` uses a
   separate assembler + `prior_state=None`, so it won't reproduce ensemble-forcing fan-out — parity needed before
   ensemble skill comparisons are trusted); a richer combined-forecast provenance rule for differing cycles.

## Non-goals / out of scope
- Group operational **ensembles** (follow-on; group **control** IS in scope). Training-data path (already
  model-specific, `training_data.py:142,178` — no shared operational cycle resolution). Hindcast parity
  (follow-on). Cross-cycle combined forecasts (disabled here; richer rule = follow-on). Any new gateway endpoint.

## Risks
- Load-bearing, live flow (fetch/resolve/store/readback/provenance/assembly/run). Mitigation: the re-ordered
  sequence lands `ModelRunContext` behaviour-preservingly first; control-only green at every phase; red-first
  tests + full-suite + Codex gate per phase.
- Completeness verification interim relies on `pf` all-or-nothing per cycle (cheap `fc`+first-`pf` probe +
  bounded full-member validation); durable fix = the gateway completeness manifest (decisions-note D2).

## Open items
- **Gateway asks** (decisions note, non-blocking): 3h gap-fill / ensemble-operational, completeness manifest,
  `pf` at all cycles.
- **Group scope** — confirm the exclusion holds for v1 (no operational group ensemble before go-live).
- **Second independent Codex review** of this revised doc, then slice the build sequence into plans.
