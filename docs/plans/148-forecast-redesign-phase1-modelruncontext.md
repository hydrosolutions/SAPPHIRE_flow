---
status: READY
created: 2026-07-23
plan: 148
title: Forecast-cycle redesign Phase 1 — ModelRunContext + per-assignment prior_state
scope: The first, behaviour-preserving slice of the forecast-cycle redesign (docs/design/forecast-cycle-redesign.md). Introduce an assignment-keyed `ModelRunContext` and move warm-up state loading in the station-cycle path so each model assignment READS state per `(station_id, model_id)` — fixing the latent shared-state read bug and establishing the per-assignment run unit the rest of the redesign consumes. The station-cycle runner loads warm-up state UNIFORMLY per assignment inside `_run_single_model`, AFTER the model/coverage/artifact eligibility gates, so a state-read failure for an ineligible or non-selected assignment stays assignment-local and never aborts a station whose primary already succeeded. The shared `assemble_station_operational_inputs` keeps loading warm-up state for its representative `model_id` exactly as today (the GROUP path depends on the `warm_up_source`/age it stamps on `OperationalInputMetadata`) and its return type is UNCHANGED (`tuple[StationModelInputs, OperationalInputMetadata]`) — so the GROUP path is byte-for-byte untouched. READ-side only: write-side per-assignment state persistence is explicitly deferred (see Non-goals). No per-assignment input assembly, no track resolution, no GROUP-path behaviour change, and no change to what a successful ensemble forecast produces; the one deliberate ensemble-adjacent change is that the stateful-ensemble reject-guards (`ModelOutputError` only, at their existing two call sites) become assignment-local instead of aborting the whole station — see the State-load failure semantics section. Forecast cycle.
depends_on: []
blocks: []
supersedes: []
---

# Plan 148 — Forecast-cycle redesign Phase 1: `ModelRunContext` + per-assignment `prior_state`

## Status
**DRAFT — Phase 1 of the forecast-cycle redesign** (`docs/design/forecast-cycle-redesign.md`, hardened through 3
independent Codex reviews). This is the deliberately small, **behaviour-preserving-except-one-bugfix** first
slice: it introduces the per-assignment run unit (`ModelRunContext`) and moves the warm-up state **read** to be
per-assignment (inside `_run_single_model`, after the eligibility gates). READ-side only — the write side stays
primary-only and is a named deferred follow-on (Non-goals). **READY (owner-authorized, 2026-07-24) → /implement
(hold-at-PR).** Converged via `/plan` + direct-fold: placement conformed to the locked parent design (service-local
in `services/`); ensemble reject-guards precisely scoped as assignment-local (design-sanctioned); clock/timestamp
design byte-identical for every current configuration (independent Codex CLEAN).

## Problem — one warm-up state is shared across all of a station's models
`assemble_station_operational_inputs` loads warm-up state **once**, for a single representative `model_id`
(`operational_inputs.py:501`: `model_state_store.fetch_latest_state(station_id, model_id)`), and bakes it
into `OperationalInputMetadata.prior_state` (field at `:48`; built at `:501-534`). In the **station-cycle** path
`run_all_station_forecasts` then passes that **same** `input_metadata` — and thus the **same `prior_state`** — to
`_run_single_model` for **every** assignment (`run_station_forecast.py:308-368` builds no per-assignment state;
consumed at `:175` (fan-out reject), `:203` (predict), `:259-260,285-286` (warm-up provenance)). The
representative model id is the *assembly* assignment (`run_forecast_cycle.py:2066`,
`model_id=assembly_assignment.model_id`).

**Scope note — this is a station-cycle-path bug only.** The GROUP path uses the *same* shared assembler but
calls it **once per station with its own single group `model_id`** (`run_group_forecast.py:131-147`), so its
`OperationalInputMetadata` warm-up provenance is already correct — there is no shared-across-assignments reuse
there. Phase 1 must not regress it (see D2).

Consequences:
1. **Latent read-side correctness bug.** A station with **≥2 stateful assignments** (heterogeneous warm-up state)
   *reads* the **wrong** model's `prior_state` into all but one — silently. (Today no live Swiss model is stateful,
   so it is latent; it becomes unavoidable under the redesign's per-assignment execution.) Note this is only half
   the round-trip: the **write** side already discards non-primary state (`run_forecast_cycle.py:2152-2159` PRIMARY,
   `:2226-2237` combination — the loop persists `new_state` only for `mid == primary_model_id`), so a
   multi-stateful station cannot round-trip end-to-end until the write side is also fixed. Phase 1 fixes the READ
   side only and names the write-side fix as a deferred follow-on (Non-goals).
2. **No per-assignment run unit.** The redesign (per-`(track,station)` outcomes, per-assignment assembly, exact-51,
   fallback-as-assignment-failure) needs an assignment-keyed context to hang everything off. Nothing exists today.

## What Phase 1 delivers (and deliberately does NOT)
- **Delivers:** an assignment-keyed `ModelRunContext`; warm-up state **read per `(station_id, model_id)`** in the
  station-cycle path, loaded inside `_run_single_model` after the eligibility gates, for **every** assignment
  uniformly; `run_all_station_forecasts`/`_run_single_model` consuming per-assignment state. Behaviour-preserving
  for every current configuration except the heterogeneous-stateful **read** bug it fixes.
- **Out of scope (later phases):** per-assignment **input** assembly / dropping the station superset (Phase 3);
  `ForcingTrackKey` + per-track cycle resolution (Phase 3); the runner returning a per-assignment success/failure
  result + fallback-on-missing-track (Phase 2); exact-51 / survival / horizons (Phase 3); group + ensemble
  **behaviour** changes; **write-side** per-assignment state persistence (see Non-goals — Phase 1 fixes the READ
  round-trip only). In Phase 1 `inputs`/`input_metadata` stay the **shared** assembled values; the assembler's
  return type is unchanged, so the GROUP path's warm-up **behaviour and call site** are byte-for-byte unchanged.

## Design
- **D1 — introduce `ModelRunContext` (frozen, kw-only, slots), service-local in `services/`.** The parent design
  is explicit and locked: `ModelRunContext` "stays **service-local** (defined in `services/`, its shape
  documented in the spec) ... per Plan 148" (`forecast-cycle-redesign.md:195-197`); `types-and-protocols.md:3017`
  states the same. Placed in `services/operational_inputs.py`, next to `OperationalInputMetadata` — the existing
  precedent for exactly this pattern: `OperationalInputMetadata` is itself a service-local dataclass defined
  directly in that module (`operational_inputs.py:43-44`), and `run_station_forecast.py` already imports from it
  (`run_station_forecast.py:19`), so no new import path is introduced. Its *shape* is documented in
  `types-and-protocols.md` (T4) even though the type itself lives in `services/`, per the locked convention. Per
  assignment, keyed by `(station_id, model_id)`
  (`ModelAssignment` has no id — identity is station+model, `types/station.py:66-71`). It does **not** embed the
  full `OperationalInputMetadata`: embedding it would expose the representative-scoped
  `warm_up_source`/`warm_up_state_age_hours` under the *same names* as the context's own per-assignment ones —
  the exact shared-provenance landmine this plan removes. The context instead carries the specific **shared
  non-state scalars** it needs (as its own flat fields — not reachable via any nested metadata object), plus its
  own per-assignment warm-up:
  ```python
  @dataclass(frozen=True, kw_only=True, slots=True)
  class ModelRunContext:
      station_id: StationId
      model_id: ModelId
      inputs: StationModelInputs                    # shared in Phase 1 (referenced, not copied)
      observation_staleness_hours: float | None     # shared non-state scalar (copied from metadata)
      nwp_age_hours: float | None                   # shared non-state scalar (copied from metadata)
      prior_state: bytes | None                     # per-assignment
      warm_up_source: WarmUpSource                  # per-assignment
      warm_up_state_age_hours: float | None         # per-assignment
  ```
  So the context *is* "shared inputs + the shared non-state scalars + this assignment's warm-up state" — with **no**
  `OperationalInputMetadata` reachable on the same object. All D3 reads go through
  `context.observation_staleness_hours` / `context.nwp_age_hours` (flat fields — **not** `context.input_metadata.*`,
  which cannot exist since the context holds no metadata object). Later phases make `inputs`/the shared scalars
  per-assignment too; Phase 1 only splits the three warm-up fields out. `OperationalInputMetadata` keeps its own
  `warm_up_source`/age for the GROUP path (D2). **Each context is built INSIDE `_run_single_model`, AFTER the
  model/coverage/artifact eligibility gates (`run_station_forecast.py:114,132-150,152-161`) and immediately before
  the state is first read (the reject-guard, `:175`).** This single, consistent execution order is deliberate (see
  the failure-semantics section): the per-assignment state read only happens once that assignment is eligible to
  run, so a read failure for a missing-model / short-coverage / artifact-less assignment can never abort a station
  whose earlier (higher-priority) assignment already produced a forecast. `run_all_station_forecasts` does **not**
  pre-build any `(station_id, model_id)`-keyed mapping — its `sorted_assignments` loop
  (`run_station_forecast.py:334`) visits each assignment exactly once and hands `_run_single_model` the slim per-run
  scalars and the `model_state_store`; `_run_single_model` reads state (consulting `clock()` only when state is
  present, D2/D3) and constructs the context locally.
  *(No runtime uniqueness assertion is added — the `model_assignments` table's
  `PrimaryKeyConstraint("station_id", "model_id")` (`db/metadata.py:1000`) already guarantees this for the
  production load path (`station_store.fetch_model_assignments`), which is sufficient for what Phase 1 touches.)*
- **D2 — extract `load_warm_up_state(model_state_store, station_id, model_id, clock) -> WarmUpState`** (where
  `clock: Callable[[], UtcDatetime]` is consulted **only when state is present** — an empty store returns
  `COLD_START` without invoking it, so the empty-store path never touches the clock) out of the
  state block in `assemble_station_operational_inputs` (`operational_inputs.py:500-514`), where `WarmUpState` is a
  new frozen bundle of the three warm-up fields (placed in `services/operational_inputs.py`, next to
  `ModelRunContext` and `OperationalInputMetadata` — service-local, same module as the function that produces it):
  ```python
  @dataclass(frozen=True, kw_only=True, slots=True)
  class WarmUpState:
      prior_state: bytes | None
      warm_up_source: WarmUpSource
      warm_up_state_age_hours: float | None
  ```
  `load_warm_up_state` is the one place that reads the store and derives source/age; both the assembler (for its
  representative `model_id`, feeding `OperationalInputMetadata`) and the station-cycle runner (per assignment, D3)
  call it. It lives in `operational_inputs.py` (service layer — it reads a store); `run_station_forecast.py` imports
  it.
  - **The assembler return type is UNCHANGED — it still returns `tuple[StationModelInputs, OperationalInputMetadata]`.**
    The station-cycle runner does **not** reuse the assembler's representative warm-up; it reads its own state for
    every assignment (D3, uniform read). So the assembler has no representative value to hand back, and there is no
    `StationInputAssembly` wrapper and no return-shape migration. `assemble_station_operational_inputs` simply calls
    `load_warm_up_state` for its representative `model_id` (as today) and populates
    `OperationalInputMetadata.warm_up_source` / `warm_up_state_age_hours` from the returned `WarmUpState` — same
    behaviour, refactored to route through the extracted helper.
  - **GROUP path — completely unchanged.** `assemble_group_operational_inputs` (`run_group_forecast.py:131-147`)
    calls the shared assembler once per station, destructures the same 2-tuple (`for inputs, metadata in [result]`,
    `run_group_forecast.py:166`) and reads `input_metadata.warm_up_source` / `warm_up_state_age_hours`
    (`:308-312,336-338`). None of that moves. The GROUP path never threads any representative identity or runs the
    station-cycle per-assignment runner.
  - **`OperationalInputMetadata.prior_state` is REMOVED** (`operational_inputs.py:48`). No reader survives Phase 1:
    the GROUP path never reads `prior_state` (verified — its only warm-up reads are source/age at `:308-312,336-338`),
    and the two station-path readers (`run_station_forecast.py:175,203`) migrate to `ModelRunContext` (D3). Removing
    the field — rather than leaving it "nullable + unused" — kills the shared-bytes landmine that produced this bug
    (a future contributor reasonably assuming `.prior_state` is authoritative). The state bytes now travel **only**
    through each assignment's own `ModelRunContext.prior_state` (explicitly per-assignment; the representative's
    bytes, no longer needed by the station-cycle path, are simply not carried out of the assembler). **Removal is
    sequenced in T2, not T1:** T1 leaves `prior_state` present-but-unread so T1's gate stays green; T2 removes it in
    the same task that migrates the `:175,:203` readers — no intentionally-broken intermediate.
    `warm_up_source`/`warm_up_state_age_hours` **stay** on `OperationalInputMetadata` as shared *provenance* because
    the GROUP path still reads them — deliberate, documented dual-ownership of provenance (source/age), while the
    *state bytes* move to the per-assignment context.
  - **Forecast timestamps are byte-identical for every current configuration — `_run_single_model` keeps its own
    per-assignment `now = clock()` at `run_station_forecast.py:270` (unchanged, **not** hoisted) and keeps its
    `clock` param.** Phase 1 introduces **no** shared evaluation instant and does **not** move or add the
    `created_at`/`updated_at` clock read — each assignment stamps them from its own `:270` `now` exactly as today.
    The per-assignment warm-up read (D3) consults the clock **only when state is present**; on the **empty store**
    (every current configuration, and every test that does not inject state) it makes **no** clock call, so each
    assignment makes exactly the one `:270` call it makes today → byte-identical under **any** clock, frozen or
    ticking. No shared-timestamp field is added to `OperationalInputMetadata`, no new toggle, no assembler
    return-shape change, and no forced kwarg on the ~5 direct `OperationalInputMetadata(...)` test constructors.
  - **The per-assignment warm-up read passes the `clock` callable (D3), consulted lazily.** A *present* state's age
    is classified against a runner instant (rather than the assembler's single `now` today) — a direct consequence
    of moving the read into the runner. This is unreachable today (empty store → `COLD_START`, no clock call,
    `operational_inputs.py:505-514`), does not touch `created_at`/`updated_at` (byte-identical per the bullet
    above), and the only-hypothetical present-state timing nuance is characterized in D4.
  - Assembly's `model_id` param is **still used** (unchanged from today): the representative warm-up read (via
    `load_warm_up_state`, `operational_inputs.py:501`) and the `short_lookback` diagnostic (`:404`). It is not left
    vestigial.
- **D3 — station-cycle path reads warm-up state per assignment, uniformly, inside `_run_single_model`, after the
  eligibility gates.**
  - `run_all_station_forecasts` gains one new param: `model_state_store: ModelStateStore` (the flow already holds it
    in scope at both call sites — `run_forecast_cycle.py:2075` passes it to the assembler). It keeps its shared
    `inputs`/`input_metadata` params (Phase 1 does not make inputs per-assignment) and its existing `clock` param,
    unchanged. For each `assignment` it forwards to `_run_single_model` a **slim set of per-run scalars extracted
    once from `input_metadata`** — `observation_staleness_hours=input_metadata.observation_staleness_hours`,
    `nwp_age_hours=input_metadata.nwp_age_hours` — plus `model_state_store` and its existing `clock` param. It does
    **not** pre-load any state before the loop and does **not** compute any shared timestamp itself.
  - **`_run_single_model` stops receiving `OperationalInputMetadata` entirely (structural fix for the "landmine one
    level up").** Instead of the full `input_metadata` it takes the slim per-run scalars above
    (`observation_staleness_hours`, `nwp_age_hours`) and `model_state_store`; it keeps its `inputs` param **and its
    existing `clock: Callable[[], UtcDatetime]` param, unchanged (D2)**. Rationale: `input_metadata.warm_up_source` / `.warm_up_state_age_hours` are the
    *representative*'s provenance, and `_run_single_model` is the one function that constructs each forecast's
    provenance — leaving the metadata object in its scope would let a future edit read the representative's warm-up
    for a non-representative assignment, silently reintroducing this bug. Removing the object makes that misread
    **unrepresentable** (Type-Driven Development) rather than relying on a "always reach for `context.*`" convention.
    **After** the model-found (`run_station_forecast.py:114`), NWP-coverage (`:132-150`) and artifact (`:152-161`)
    gates pass, and **before** the reject-guard first reads state (`:175`), it resolves **this assignment's** warm-up
    state uniformly — no representative special case — passing the `clock` **callable** (not a pre-computed instant):
    ```python
    warm_up = load_warm_up_state(model_state_store, station_id, assignment.model_id, clock)
    ```
    wrapped assignment-local (see failure-semantics). `load_warm_up_state` consults `clock()` **only when state is
    present** (to classify a present state's age); on the **empty store — every current configuration —** it returns
    `COLD_START` **without calling `clock()` at all** (`operational_inputs.py:505-514`; the `else` branch is a bare
    `COLD_START`). Then construct the per-assignment `ModelRunContext(station_id=…,
    model_id=assignment.model_id, inputs=inputs, observation_staleness_hours=observation_staleness_hours,
    nwp_age_hours=nwp_age_hours, prior_state=warm_up.prior_state, warm_up_source=warm_up.warm_up_source,
    warm_up_state_age_hours=warm_up.warm_up_state_age_hours)`. `_run_single_model`'s existing `now = clock()` at
    `run_station_forecast.py:270` — the single per-assignment call it makes today for `created_at`/`updated_at` —
    **stays exactly where it is** (not hoisted), so on the empty store each assignment makes exactly the one
    `clock()` call it makes today and forecast timestamps are byte-identical under any clock (D4).
  - `_run_single_model` then reads **everything downstream from the context** (never from a metadata object, which
    is no longer in scope):
    - `context.prior_state` at the fan-out reject-guard (`run_station_forecast.py:175`) and at `predict`
      (`:203`) — the reject-guard now checks the **ensemble assignment's own** state (still `None` for a stateless
      ensemble model, so unchanged);
    - `context.warm_up_source` / `context.warm_up_state_age_hours` for input-quality (`:259-260`) and forecast
      provenance (`:285-286`);
    - `context.observation_staleness_hours` (`:258,:287`) / `context.nwp_age_hours` (`:262`) — flat context fields
      (not `context.input_metadata.*`), matching the declared `ModelRunContext` shape in D1;
    - `context.inputs` everywhere `inputs` is used (predict, coverage safety net, etc.).
  - `run_station_forecast` (the PRIMARY-mode wrapper, `run_station_forecast.py:371`) also gains `model_state_store`
    and forwards it to `run_all_station_forecasts`.
  *(Design note — why uniform read, not representative-reuse: an earlier draft had the runner REUSE the assembler's
  representative warm-up for the primary and only re-read for non-representative assignments, to avoid a second
  store read for the primary. That guarded a concurrent-write race that cannot occur today — `run_all_station_forecasts`
  is a plain sequential `for` loop (`run_station_forecast.py:334`), the store write happens strictly after the loop
  (`run_forecast_cycle.py:2152-2159,2226-2237`), and per-station cross-task parallelism (`task.map`, pooled
  combination) is a not-yet-implemented v0b remainder — so no writer can race a read within one station-cycle. Uniform
  read removes the reuse branch, the `representative_model_id`/`representative_warm_up` threading through 5 call sites,
  the `StationInputAssembly` return-shape migration and its forced GROUP-destructure/test-fake changes, and the
  reuse-specific test — buying the same read-side fix for far less blast radius. **Trade-off, noted:** the primary is
  now read twice (once in the assembler for GROUP-facing metadata, once in the runner for its context), and the runner
  read is a **new** failure surface for the primary that reuse avoided. Because both reads hit the **same** store
  moments apart, a durable store failure fails the assembler read first → `forecast_cycle.input_assembly_failed`
  (existing flow abort, unchanged); the runner-only failure is a pathological transient, and no shipped model is
  stateful (reads return `None`, never raise for missing state). If v0b adds per-station parallelism, the reuse-vs-read
  distinction must be revisited alongside the write side.)*
- **D4 — behaviour-preservation invariant (READ side), scoped precisely.** The invariant holds for **every current
  configuration**: in production the `model_state_store` is empty for all models (no shipped model writes state —
  every ForecastInterface `predict` returns `new_state=None`, `adapters/forecast_interface.py:736`), so every
  `fetch_latest_state` returns `None` → `COLD_START` for every assignment (representative and non-representative
  alike), before and after this change → byte-identical forecasts, provenance and input-quality flags. This
  includes `created_at`/`updated_at`: an **empty store returns `None` WITHOUT consulting the clock at all** — the
  per-assignment warm-up read only needs an instant to classify a *present* state's age (`operational_inputs.py:507`;
  the `else` branch is a bare `COLD_START`), so on the empty store every assignment makes exactly the one
  `clock()` call it makes today, at `:270` for `created_at`/`updated_at`. Hence **byte-identical under any clock,
  fixed or ticking, for every current configuration.** *(The only case that is not perfectly clock-call-identical is
  a HYPOTHETICAL future stateful model with **present** stored state: such an assignment consults the clock at its
  earlier per-assignment read to classify age, so one that then fails predict/QC would consume a read today's
  assembler-scoped code does not — shifting later `created_at` by microseconds under a real ticking clock. This is
  immaterial (`created_at` is a provenance timestamp, no logic reads it), unreachable today (no shipped model writes
  state, `adapters/forecast_interface.py:736`), and moot until the deferred write-side follow-on ships — at which
  point that follow-on owns the stateful round-trip and this timing.)*
  **What deliberately changes (this is the fix, not a regression):** for any station where a **non-representative**
  assignment has its **own** stored state differing from the representative's — whether that model is genuinely
  stateful *or* merely carries historical/orphaned bytes — that assignment now reports **its own** warm-up
  provenance (`warm_up_source`/`warm_up_state_age_hours`) and the input-quality flags derived from it, instead of
  silently inheriting the **representative's** provenance (today's bug). So a *stateless* secondary that happens to
  have orphaned state bytes will, post-change, report e.g. its own `COLD_START`/`SNAPSHOT` rather than the
  representative's `FRESH` — even though both still `predict` with the same (ignored) state and return
  `new_state=None`. That divergence is the intended correction, exercised by dedicated regression tests (T3).
  The GROUP path is unchanged (D2). **Scope note:** this closes the latent correctness hole on the **READ** side
  only; the write side still persists just the primary model's `new_state`
  (`run_forecast_cycle.py:2152-2159,2226-2237`), so a multi-stateful station does not yet round-trip end-to-end — a
  later phase must fix the write side (Non-goals). Pin the read-side invariant with golden tests.

## State-load failure semantics — assignment-local, not a new station-abort mode
Phase 1 adds per-assignment store reads inside `_run_single_model`, after the eligibility gates, on top of the
assembler's existing single representative-model read (unchanged: still aborts the station via
`forecast_cycle.input_assembly_failed` if it raises, `run_forecast_cycle.py:2062-2086` — no log-name shift). A
reviewer blocker flagged that without care, a state-read failure for a secondary/fallback assignment could abort a
station whose primary already succeeded — a NEW failure mode that cannot occur today. Phase 1 preserves fallback
semantics (`docs/touchpoint-maps.md:156`) via two operative rules:
1. **New per-assignment reads never escape the loop.** `load_warm_up_state`'s store-read exception is caught
   assignment-local inside `_run_single_model` → logs `run_station_forecast.warm_up_load_failed`, returns a reason
   string recorded in `failed_models` — the same channel the model-not-found/no-artifact/predict-failed gates
   already use (`run_station_forecast.py:121,161,212`). A higher-priority assignment that already succeeded is kept
   as primary; the station fails only when `primary_model_id is None` (`run_forecast_cycle.py:2121-2139` PRIMARY,
   `:2192-2210` combination) — identical to today's all-models-failed semantics. The reject-guards get the same
   treatment: today `reject_prior_state_for_fanout` (input-side, `:175`) and `reject_stateful_ensemble_states`
   (output-side, `:218-219`) raise `ModelOutputError` OUTSIDE any `try` (`:172-175`) and the loop has no
   try/except around `_run_single_model` (`:334-360`), so the raise escapes to the per-station catch-all and
   discards an already-succeeded primary; Phase 1 wraps both assignment-local too, logging the distinct event
   `run_station_forecast.unsupported_stateful_ensemble`. Phase 1 does **not** introduce fallback-to-COLD_START on a
   read error (deferred to Phase 2's per-assignment run-result) — a raising store records a `failed_models` entry,
   never a silent COLD_START forecast.
2. **The reject-guard `try` catches `ModelOutputError` ONLY at its two call sites (`:175`, `:218-219`) — never
   widened to wrap `predict`.** Gotcha: the FI adapter also maps an FI `ModelFailure` to
   `raise ModelOutputError(...)` (`adapters/forecast_interface.py:370-373`), the SAME exception class the
   reject-guards raise — but that raise happens inside `model.predict(...)` (`:199-204`), caught by the existing,
   unchanged `except Exception` boundary around deserialize/predict (`:205-212`) as `predict_failed`, before
   control ever reaches the guards. The two boundaries are separated by code location, not exception type: widening
   the guard-`try` to span `predict` would mislabel an FI `ModelFailure` as a stateful-ensemble rejection,
   regressing the FI failure contract. A regression test (T2) proves this: a non-ensemble `predict` raising an
   adapter-mapped `ModelFailure` still records `predict_failed`, not `unsupported_stateful_ensemble` or
   `warm_up_load_failed`.

## Non-goals
- Per-assignment input assembly, track resolution, success/failure run-result, exact-51/survival/horizons (all
  later phases). The GROUP path (D2) and what any successful ensemble forecast produces are both unchanged for
  every current configuration. **Not preserved, and out of scope for Phase 1 to preserve further:** the
  stateful-ensemble reject-guard's *station-abort* effect — today a lower-priority stateful-ensemble assignment's
  guard raise escapes the whole loop and discards an already-succeeded primary; Phase 1 deliberately fixes this to
  assignment-local (primary kept), which is the correction this plan makes, not a regression (see State-load
  failure semantics). No other ensemble/group behaviour is touched.
- **Write-side per-assignment state persistence is NOT fixed by Phase 1.** The flow still persists only the primary
  model's `new_state` per station-cycle (PRIMARY mode `run_forecast_cycle.py:2152-2159`; combination mode
  `:2226-2237`). So after Phase 1 a genuinely heterogeneous-stateful station still cannot round-trip: a non-primary
  stateful model reads its own per-assignment state (fixed here) but that state is never written back. Making the
  write side per-assignment is deferred to the later phase that owns the per-assignment run-result; its exact shape
  is that plan's job, grounded against the code current then — not pre-designed here. Deferred deliberately to keep
  Phase 1 a minimal, behaviour-preserving READ-side slice — for all **current** (stateless) configs both sides are
  no-ops, so nothing regresses.

## Phases / tasks (red-first)
- **T1 — new service-local types + `load_warm_up_state` extraction (assembler return type UNCHANGED).**
  - **In scope:** add the frozen `ModelRunContext` (D1 fields) **and `WarmUpState` (D2)** to
    **`services/operational_inputs.py`**, next to `OperationalInputMetadata` (service-local, per the locked parent
    design — `forecast-cycle-redesign.md:195`; both reference only symbols already available in that module:
    `StationId`/`ModelId`, `StationModelInputs`, `WarmUpSource`); extract `load_warm_up_state(...) -> WarmUpState`
    (D2) from `operational_inputs.py:500-514` and have `assemble_station_operational_inputs` call it for its
    representative
    `model_id` to populate `OperationalInputMetadata.warm_up_source`/`warm_up_state_age_hours` (and `prior_state`,
    still present in T1). **The assembler return type does not change** — it still returns
    `tuple[StationModelInputs, OperationalInputMetadata]`, so there is **no** consumer-migration ripple (no
    `StationInputAssembly`, no GROUP destructure change, no GROUP test-fake change). `prior_state` stays on
    `OperationalInputMetadata` through T1 (removal is T2, keeping T1's gate green with no broken intermediate).
  - **Out of scope:** no station-cycle runner wiring / reader migration / `prior_state` removal / runner-signature
    threading (all T2); no write-side change.
  - **Extraction-parity tests** (not a red-first *bug* demonstration — the store already keys on
    `(station_id, model_id)` (`model_state_store.py:38-42`), so the bug is *routing one assembled value to many
    runners*, proven in T2, not lookup behaviour): `load_warm_up_state` reproduces the old assembly logic for
    cold-start / fresh(<24h → FRESH) / snapshot(≥24h → SNAPSHOT) and returns a `WarmUpState`;
    `assemble_station_operational_inputs` still emits the same `warm_up_source`/`warm_up_state_age_hours` on
    `metadata` as today for its representative `model_id` (proving the extraction is behaviour-preserving).
  - **Gate:** `uv run pytest tests/unit/services/test_operational_inputs.py -q`. *(No `tests/unit/types/` addition —
    both new types are service-local in `services/operational_inputs.py`, covered by that module's own test file.
    The GROUP and seasonal-model suites are NOT in-gate for T1 — the assembler return type is unchanged, so their
    destructuring/monkeypatch sites do not move.)*
- **T2 — wire the station-cycle path to per-assignment state (uniform read inside the runner, after the gates) +
  thread `ModelStateStore`.**
  - **In scope:** thread `model_state_store` into the station-cycle runner; move the per-assignment warm-up read into
    `_run_single_model` after the eligibility gates (uniform — every assignment), passing the `clock` callable so the
    read consults `clock()` only when state is present (empty store → no clock call; the `now = clock()` at `:270`
    stays put, unchanged, D2/D3); drop `input_metadata` from `_run_single_model` (pass slim scalars instead, keeping
    its existing `clock` param); build + consume `ModelRunContext`; migrate the D3 reads; **remove
    `OperationalInputMetadata.prior_state`** (its `:175,:203` readers migrate here); wrap the reject-guards +
    `load_warm_up_state` assignment-local and add the two failure log events + their focused assertion tests (the log
    statements live in T2 where the `try/except` is written, NOT T4).
  - **Out of scope:** GROUP path, combination behaviour, write-side persistence, per-assignment inputs, and any
    change to what a successful ensemble forecast produces. (In scope, per this task: making the two reject-guards
    assignment-local — see In scope above and State-load failure semantics.)
  - **Signature migration** (thread only `model_state_store` into the runner; the per-assignment `now = clock()`
    stays at `run_station_forecast.py:270`, NOT hoisted):
    1. `run_all_station_forecasts` (`run_station_forecast.py:308`) — add `model_state_store: ModelStateStore`.
       In the loop, extract the slim per-run scalars from `input_metadata` (`observation_staleness_hours`,
       `nwp_age_hours`) and forward them + `model_state_store` + shared `inputs` + its existing `clock` param to
       `_run_single_model`. (`input_metadata` stays a param of `run_all_station_forecasts` — the slim extraction
       happens here, not inside `_run_single_model`.)
    2. `_run_single_model` (`run_station_forecast.py:95`) — **remove the `input_metadata: OperationalInputMetadata`
       param** (structural landmine fix, D3); **keep the existing `clock: Callable[[], UtcDatetime]` param**
       unchanged (D2); add `model_state_store: ModelStateStore`, `observation_staleness_hours: float | None`,
       `nwp_age_hours: float | None`; keep `inputs`. After the artifact gate (`:161`), and before the reject-guard
       first reads state (`:175`), resolve this assignment's warm-up passing the `clock` **callable**:
       `warm_up = load_warm_up_state(model_state_store, station_id, assignment.model_id, clock)`
       inside a `try` (store exception → `run_station_forecast.warm_up_load_failed` + reason string) — uniform for
       every assignment, no representative special case. `load_warm_up_state` consults `clock()` **only when state is
       present** (empty store → `COLD_START`, no clock call, so the empty-store timestamp behaviour is byte-identical,
       D2/D4). Build `ModelRunContext` (D3). Wrap the input-side
       `reject_prior_state_for_fanout(context.prior_state)` (`:175`) and the output-side
       `reject_stateful_ensemble_states(...)` (`:218-219`) each in an assignment-local `try` that catches
       **`ModelOutputError` only** (never widen either guard `try` to span the `predict` call), logging
       `run_station_forecast.unsupported_stateful_ensemble` + reason string — a distinct event from the store
       failure. Leave the existing `predict` `except Exception` (`:205-212`) **unchanged** so an FI `ModelFailure`
       still maps to `predict_failed`. **Leave the `now = clock()` at `:270` exactly where it is** — `created_at`/
       `updated_at` are stamped from it per assignment as today, not from any hoisted or shared instant. **Grep every remaining
       read of the removed `input_metadata` param inside `_run_single_model` and redirect it to the equivalent
       `context.*` field** — see D3 for the authoritative field→read mapping and rationale; don't trust a frozen
       line list here either (same caveat as item 7 below).
    3. `run_station_forecast` wrapper (`run_station_forecast.py:371,390`) — add `model_state_store`; forward it to
       `run_all_station_forecasts`.
    4. Flow PRIMARY branch (`run_forecast_cycle.py:2101`, `run_station_forecast(...)`) — pass `model_state_store`
       (already in scope at `:2075`).
    5. Flow combination branch (`run_forecast_cycle.py:2172`, `run_all_station_forecasts(...)`) — pass
       `model_state_store`.
    6. The flow's assembler call and 2-tuple unpack (`run_forecast_cycle.py:2063-2093`) are **unchanged** in both T1
       and T2 (assembler return type never changes).
    7. **Test call sites — grep, do not trust a frozen line list.** Grep for every call of `_run_single_model(`,
       `run_all_station_forecasts(` and `run_station_forecast(` under `tests/` and `src/` and migrate all of them to
       pass a fake `ModelStateStore` seeded with per-`(station,model)` states (and drop the now-removed
       `input_metadata` arg to `_run_single_model` / add `model_state_store` to the runner entry points). A leftover
       un-migrated call site fails at collection time — the backstop.
    8. **Any test constructing `OperationalInputMetadata(...)` directly must drop `prior_state` in T2.** Grep for
       `OperationalInputMetadata(` under `tests/` and remove the `prior_state=` kwarg from each (T2 removes the
       field). The E2E integration caller (`tests/integration/test_e2e_pipeline.py`) hand-builds an `OperationalInputMetadata` and
       calls `run_station_forecast(...)` directly (grep the file): drop its `prior_state=None`, and pass the test's
       existing `ModelStateStore` (or an empty in-memory fake so every `fetch_latest_state` returns `None` →
       `COLD_START`, byte-identical to the manual metadata). Grep is authoritative for the exact sites.
  - **Red-first bug-demonstrating test:** a station with **two stateful assignments** persisting *different* states
    now runs each model with its OWN state (fails today because the shared `input_metadata.prior_state` routes one
    value to both runners). Plus:
    - **ensemble reject-guard wrapped assignment-local:** a station with a **succeeding primary** and a lower-priority
      **stateful ENSEMBLE** assignment (its own persisted non-`None` state) → `reject_prior_state_for_fanout` fails
      that assignment **locally** (recorded in `failed_models`, logged `unsupported_stateful_ensemble`), the primary's
      result is **still returned**, the station is **not** aborted (red-first: today the guard raises out of the loop
      and the per-station catch-all discards the primary). A focused companion test covers the **output-side** guard
      `reject_stateful_ensemble_states` the same way (a stateless-input ensemble whose per-member `predict` returns
      non-`None` state → local failure, primary kept);
    - **assignment-local read failure:** a station with a **succeeding primary** and a lower-priority
      **non-representative** secondary whose `fetch_latest_state` **raises** → the secondary is recorded in
      `failed_models` (logged `warm_up_load_failed`), the **primary's `StationForecastResult` is still returned** as
      `primary_model_id`, and the station is **not** failed, **not** a silent COLD_START forecast;
    - *(No dedicated per-assignment warm-up-age clock test: `_run_single_model` computes `now = clock()` once per
      assignment exactly as today, so forecast timestamps are byte-identical under any clock — nothing new to pin.
      The per-assignment age itself is `None` in every current configuration (store empty), so there is nothing
      observable to assert beyond the existing golden tests, T3.)*
    - **FI-failure-vs-reject-guard separation regression:** a **non-ensemble** model whose `predict` raises an
      adapter-mapped FI `ModelFailure` (the adapter's `ModelOutputError` from `adapters/forecast_interface.py:370-373`,
      the **same** exception class the reject-guards raise) is recorded in `failed_models` with the **existing**
      `run_station_forecast.predict_failed` reason/event, **not** `unsupported_stateful_ensemble` and **not**
      `warm_up_load_failed`. This proves the boundary-2 reject-guard `try` catches `ModelOutputError` **only at the
      guard call sites** (`:175`, `:218-219`) and never spans the `predict` call.
  - **Gate:** `uv run pytest tests/unit/services/test_run_station_forecast.py
    tests/unit/services/test_run_station_forecast_fanout.py tests/unit/flows/test_run_forecast_cycle.py -q` **and**
    `uv run pyright` (ratchet). T2's own signature-migration list (items 4-5) changes the flow call sites at
    `run_forecast_cycle.py:2101,2172`, so T2's gate must cover them directly rather than deferring that coverage to
    T3 — `test_run_forecast_cycle.py` exercises both the PRIMARY (`:2101`) and combination (`:2172`) branches, and
    `pyright` catches any call site the grep in item 7 missed (a stale positional/keyword mismatch fails
    type-checking even before a test would).
- **T3 — behaviour-preservation golden tests (station-cycle + GROUP + E2E).**
  - **In scope:** golden/regression tests (a)-(f) below; no production-code change.
  - **Out of scope:** any behaviour change; write-side persistence.
  (a) a control-only **single-model** station → byte-identical forecast + provenance;
  (b) a multi-model **stateless** station with an **empty** `model_state_store` (current-production shape) →
  unchanged: every assignment reports `COLD_START` before and after (this is the invariant D4 actually guarantees);
  (c) warm-up provenance (`warm_up_source`/age) unchanged for the assembly model under the empty-store config;
  (d) **GROUP regression** — run `tests/unit/services/test_run_group_forecast.py` against the **REAL, unpatched**
  `assemble_group_operational_inputs → assemble_station_operational_inputs` path (the existing suite monkeypatches
  the assembler out via `_patch_station_assembler`, `test_run_group_forecast.py:152-176`, so it has **zero** coverage
  of the warm-up-loading code this plan refactors, even though the GROUP call site is byte-for-byte unchanged). Add a
  test that exercises the real assembler with a seeded `ModelStateStore` and asserts group warm-up provenance
  (`warm_up_source`/`warm_up_state_age_hours` on `OperationalForecast`) and input-quality flags are **unaffected** by
  the `load_warm_up_state` extraction;
  **The intended-divergence regression is split into TWO deterministic fixtures (D4) — an empty state and orphaned
  bytes cannot be alternatives inside one fixture:**
  (e) **representative-only-state → secondary reports `COLD_START`:** a multi-model station where **only the
  representative** has stored state (`FRESH`/`SNAPSHOT`) and the lower-priority secondary has **no** stored state
  (its `fetch_latest_state` returns `None`) → post-change the secondary's forecast reports **its own** `COLD_START`
  (and the corresponding input-quality flags), **not** the representative's `FRESH`/`SNAPSHOT` — red-first: today the
  secondary wrongly inherits the representative's provenance. This pins (b)'s "unchanged" holds strictly for the
  empty-store case, not universally;
  (f) **secondary has its OWN stored snapshot (different from the representative's):** a multi-model station where the
  representative has one stored snapshot and the lower-priority secondary has a **different** stored snapshot
  (different timestamp) → post-change the secondary reports **its own** `warm_up_source`/`warm_up_state_age_hours`
  derived from its own snapshot, and the representative reports its own — the two provenances diverge correctly
  instead of both showing the representative's. Together (e)+(f) prove the D4 divergence is the intended fix across
  both the no-secondary-state and distinct-secondary-state cases.
  - **Gate:** `uv run pytest tests/unit/services/ tests/unit/flows/test_run_forecast_cycle.py
    tests/integration/test_e2e_pipeline.py -q`.
- **T4 — docs + final full-suite gate.**
  - **In scope (docs only):** note Phase 1 (READ-side) complete in `docs/design/forecast-cycle-redesign.md`,
    explicitly recording that the write side remains primary-only and is deferred; **document the two new
    service-local types' *shape* in `docs/spec/types-and-protocols.md`** — add a `ModelRunContext` / `WarmUpState`
    entry (next to the existing `ModelRunContext` note at `:3017`, both frozen/kw-only/slots, defined in
    `services/operational_inputs.py`), stating their ownership (per-assignment run unit + warm-up bundle,
    **service-local**, per `forecast-cycle-redesign.md:195` and `types-and-protocols.md:3017` — the spec documents
    the shape, not the type's home) and the distinction between the **FI's state-free `predict(prior_state=...)`
    contract** (`:1733-1761`, the boundary passes `bytes | None`, no provenance) and **SAP3's native
    adapter-facing `StationForecastModel.predict` protocol** (`protocols/forecast_model.py:32`, itself also
    `prior_state: bytes | None` at the SAP3↔model boundary) whose state fields — `prior_state`/`warm_up_source`/
    `warm_up_state_age_hours` — are carried on `ModelRunContext`, with `warm_up_source`/age still on
    `OperationalInputMetadata` for the GROUP path (dual-ownership of provenance per D2); note FI models are
    state-free at the FI boundary — the provenance fields are a SAP3-native addition, not part of the FI contract
    itself; update the forecast-cycle
    touchpoint map (`docs/touchpoint-maps.md`); and add `logging.md` entries for the **two** new events introduced in
    T2 — `run_station_forecast.warm_up_load_failed` (store-read exception) and
    `run_station_forecast.unsupported_stateful_ensemble` (deterministic input/output-side reject-guard) — describing
    when each fires and that both are assignment-local (do not abort a station whose primary succeeded). *(Their
    implementation + assertion tests live in T2, where the `try/except` is written; T4 is documentation only.)* **No
    `input_assembly_failed` → `station_forecast_failed` log-name shift** — the representative read stays in the
    assembler, so that event is unchanged.
  - **Out of scope:** any code change (T2 owns the log statements and tests).
  - **Final gate (whole repo):** `uv run pytest -q` (full suite) **and** `uv run ruff check` **and**
    `uv run pyright` (ratchet).

## Dependencies
- `docs/design/forecast-cycle-redesign.md` (the parent architecture; D1/D3/D4 decisions). No plan dependencies —
  Phase 1 is self-contained and behaviour-preserving, so it can land first.

### Task dependency graph
```json
{
  "nodes": ["T1", "T2", "T3", "T4"],
  "edges": [
    {"from": "T1", "to": "T2", "reason": "T2 wires the ModelRunContext / load_warm_up_state introduced in T1"},
    {"from": "T2", "to": "T3", "reason": "T3 golden/GROUP/E2E tests assert behaviour of the wired station-cycle path from T2"},
    {"from": "T3", "to": "T4", "reason": "T4 docs + full-suite gate finalise once all implementation + tests are green"}
  ]
}
```
Strictly sequential: T1 → T2 → T3 → T4.

## Open items / to confirm
- **No open blockers.** All prior design questions are resolved in D1–D4 and the failure-semantics section.
- **Trade-offs deliberately accepted (noted, not silently regressed):**
  1. **Uniform read replaces representative-reuse.** See D3's design note (above) for the full rationale — accepted
     trade-off: revisit alongside the write side if v0b adds per-station parallelism.
- Context: no currently-shipped model is stateful — every ForecastInterface `predict` returns `new_state=None`
  (`adapters/forecast_interface.py:735-736`), so Phase 1's read-side fix is preemptive + foundational and the
  heterogeneous-stateful scenarios are exercised with **stateful fakes** in the red-first tests.
