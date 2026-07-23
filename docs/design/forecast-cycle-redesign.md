# Forecast-cycle redesign — forcing tracks, per-assignment run context, probabilistic multi-track

**Status:** DRAFT design doc — created 2026-07-23, **revised after independent Codex review round 1**.
**Supersedes the incremental patching of Plans 126 + 144** (folded here). Decisions locked in
`docs/design/v1-forecasting-decisions.md`. **Next:** a second fresh Codex review, then slice the build sequence
into implementation plans. Control-only forecasting is live and must stay green at every phase.

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
- **`ModelRunContext` (assignment-keyed) = inputs + input metadata + resolved NWP provenance + `prior_state`,
  loaded by `(station_id, assignment.model_id)`.** This is the assemble/run unit; it is the missing consumer
  that makes a per-track cycle non-dormant.

Components:
1. **Track projection with per-feature horizons.** Derive each assignment's `(features→future_steps, time_step,
   mode, spatial)` at the FI boundary **before** the max-collapse, into a `ForcingTrackKey`. Do **not** reuse
   `ModelDataRequirements` (one scalar horizon) as the fetch requirement. Distinguish three horizons explicitly:
   **fetch-acceptance** (per-feature), **internal rectangular frame** (max horizon, with an explicit FI-boundary
   per-variable slice so a short-horizon feature isn't rejected for lacking long-horizon steps), and **output**
   horizon (separate from forcing).
2. **A requirement-aware source contract + per-track cycle resolution.** Add
   `fetch_requirement(track, stations, nominal_cycle) -> CandidateFetchResult` (immutable) alongside the existing
   `fetch_forecasts` (kept as a compatibility adapter until control has migrated). Per track, walk back to the
   latest cycle satisfying its completeness (D1: exact-51 for ENSEMBLE features; `fc` for CONTROL) + per-feature
   horizon, bounded by `max_cycle_age_hours` (D4). Because `pf` is 00Z-only (D3), ENSEMBLE tracks resolve to the
   latest 00Z (once/day). Different tracks may resolve to different cycles → the flow carries a
   **`map[track → resolved_cycle]`**; readback + provenance key off each track's cycle. **Candidate-local,
   immutable accumulation**: validate each candidate into a fresh result, commit only on full pass (fixes the
   Recap in-memory partial-`pf` contamination).
3. **Assignment-keyed `ModelRunContext` (build this FIRST — see phases).** Replace the single shared
   assembly/metadata/state passed to every assignment (`run_station_forecast.py:334,336,348`) with a per-assignment
   context; `prior_state` loaded by `(station_id, model_id)` (fixes the today-latent shared-`prior_state` bug for
   heterogeneous stateful assignments; ensemble fan-out stays stateless per `ensemble_fanout.py:47,56`).
4. **Per-assignment assembly from shared stored rows.** Each assignment assembles its own frame (its features,
   its time_step, its horizon) from the rows fetched by its track — no cross-assignment superset.
5. **Route into the existing fan-out runner.** ENSEMBLE assignments already fan out per member; CONTROL runs
   member 0. This redesign feeds them assignment-specific inputs; it does not rebuild the runner.
6. **exact-51 as typed checks (nothing enforces it today).** Coverage checks identical member sets but not
   `{0..50}` (`nwp_coverage.py:92,106`); `min_operational_ensemble_size` gates **alerts**, not production
   (`alert_checker.py:181-188`). Add: **input completeness** (exactly members 0–50 + horizons at fetch
   acceptance), a **post-prediction survival** publication/storage policy, and keep alert eligibility as-is.

### Fallback-chain semantics (round-1: control-only is NOT a degenerate single requirement)
A station carries a **list of assignments run as a priority fallback chain** (`run_forecast_cycle.py:1781,1887`);
runoff-only mode strips future features and lets per-model coverage/fallback decide (`:1826,1832`). So an
ordinary CONTROL station is **many** assignments, not one requirement. The redesign must **preserve the chain**:
an assignment whose track/cycle is unavailable **advances to the next assignment**, it does **not** mark the
station dark. Equivalent assignments deduplicate onto one track, so this adds no extra fetches.

### Group models — EXPLICITLY OUT OF SCOPE for this redesign (round-1 forced the call)
Group prediction uses the same station assembler, one cycle/time_step, and calls `predict_batch` **once** with
**no ensemble-forcing fan-out** (`run_group_forecast.py:110,131,136,440,442`; one global readback cycle
`run_forecast_cycle.py:2193,2198,2240`). Operational **group ensembles are excluded here** — this redesign
targets station-assigned models; group-level requirements + group fan-out are a **named follow-on** (do not leave
implicit).

## Locked decisions (see `docs/design/v1-forecasting-decisions.md`)
D1 exact-51 + walk-back · D3 `pf` 00Z-only (ensemble once/day now, 4×/day later) · D4 walk-back-only · units
canonical from the recap adapter · reuse the already-wired `ensemble_fanout`/`ForecastEnsemble`/`forecast_qc`.

## What this supersedes / relates to
- **Supersedes** Plan 126 (→ components 2 + candidate-local accumulation) and Plan 144 (→ components 1/4/5).
  Both marked `SUPERSEDED`.
- **Separate:** 134 (control bridge), 145/146 (snow forcing), 143 (onboarding). Reuses the existing fan-out stack.

## Build sequence (re-ordered per round-1; control-only green at every step)
1. **Assignment-keyed `ModelRunContext`** — per-assignment inputs + metadata + provenance + `prior_state` by
   `(station, model_id)`. Initially each context is filled from today's shared values (behaviour-preserving), so
   control-only is unchanged; this is the consumer everything else needs.
2. **Migrate the station runner** to consume per-assignment `ModelRunContext` (fallback chain intact). Still one
   cycle — no behaviour change, just per-assignment plumbing.
3. **`ForcingTrackKey` projection + per-track requirement-aware cycle resolution** — deduplicated tracks, the
   requirement-aware source contract, candidate-local immutable results, the `map[track→resolved_cycle]`, and
   per-track readback/provenance. Control-only = a single CONTROL track (one cycle) → unchanged.
4. **Per-assignment assembly + exact-51 typed validation** — drop the station superset; per-feature horizon +
   FI-boundary slicing; input-completeness/survival checks.
5. **Remove the legacy station-superset path** once all consumers are on `ModelRunContext`.
6. **Follow-ons (separate plans):** group-ensemble requirements + group fan-out; hindcast/operational parity
   (`hindcast.py:328,331,354` uses a separate assembler + `prior_state=None`, so it won't reproduce
   ensemble-forcing fan-out automatically — parity is needed before ensemble skill comparisons are trusted);
   combined-forecast provenance when models consumed different cycles (`run_forecast_cycle.py:2031,2035`).

## Non-goals / out of scope
- Group operational ensembles (follow-on). Training-data path (already model-specific, `training_data.py:142,178`
  — no shared operational cycle resolution needed). Hindcast parity (follow-on). Any new gateway endpoint.

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
