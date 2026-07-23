---
status: DRAFT
created: 2026-07-23
plan: 147
title: Forecast-cycle redesign Phase 1 — ModelRunContext + per-assignment prior_state
scope: The first, behaviour-preserving slice of the forecast-cycle redesign (docs/design/forecast-cycle-redesign.md). Introduce an assignment-keyed `ModelRunContext` and move warm-up state loading in the station-cycle path so each model assignment READS state per `(station_id, model_id)` — fixing the latent shared-state read bug and establishing the per-assignment run unit the rest of the redesign consumes. The shared `assemble_station_operational_inputs` keeps loading warm-up state for its representative `model_id` exactly as today (the GROUP path depends on it); the station-cycle runner additionally loads per-assignment state inside `_run_single_model`, AFTER the model/coverage/artifact eligibility gates, so a state-read failure for an ineligible or non-selected assignment stays assignment-local and never aborts a station whose primary already succeeded. READ-side only: write-side per-assignment state persistence is explicitly deferred (see Non-goals). No per-assignment input assembly, no track resolution, no ensemble/group behaviour changes. Forecast cycle.
depends_on: []
blocks: []
supersedes: []
---

# Plan 147 — Forecast-cycle redesign Phase 1: `ModelRunContext` + per-assignment `prior_state`

## Status
**DRAFT — Phase 1 of the forecast-cycle redesign** (`docs/design/forecast-cycle-redesign.md`, hardened through 3
independent Codex reviews). This is the deliberately small, **behaviour-preserving-except-one-bugfix** first
slice: it introduces the per-assignment run unit (`ModelRunContext`) and moves the warm-up state **read** to be
per-assignment (inside `_run_single_model`, after the eligibility gates). READ-side only — the write side stays
primary-only and is a named deferred follow-on (Non-goals). Needs `/plan` before READY.

## Problem — one warm-up state is shared across all of a station's models
`assemble_station_operational_inputs` loads warm-up state **once**, for a single representative `model_id`
(`operational_inputs.py:490`: `model_state_store.fetch_latest_state(station_id, model_id)`), and bakes it
into `OperationalInputMetadata.prior_state` (`:48,517-523`). In the **station-cycle** path
`run_all_station_forecasts` then passes that **same** `input_metadata` — and thus the **same `prior_state`** — to
`_run_single_model` for **every** assignment (`run_station_forecast.py:308-368` builds no per-assignment state;
consumed at `:175` (fan-out reject), `:203` (predict), `:259-260,285-286` (warm-up provenance)). The
representative model id is the *assembly* assignment (`run_forecast_cycle.py:1853`,
`model_id=assembly_assignment.model_id`).

**Scope note — this is a station-cycle-path bug only.** The GROUP path uses the *same* shared assembler but
calls it **once per station with its own single group `model_id`** (`run_group_forecast.py:131-147`), so its
`OperationalInputMetadata` warm-up provenance is already correct — there is no shared-across-assignments reuse
there. Phase 1 must not regress it (see D2).

Consequences:
1. **Latent read-side correctness bug.** A station with **≥2 stateful assignments** (heterogeneous warm-up state)
   *reads* the **wrong** model's `prior_state` into all but one — silently. (Today no live Swiss model is stateful,
   so it is latent; it becomes unavoidable under the redesign's per-assignment execution.) Note this is only half
   the round-trip: the **write** side already discards non-primary state (`run_forecast_cycle.py:1939-1946` PRIMARY,
   `:2013-2024` combination — the loop persists `new_state` only for `mid == primary_model_id`), so a
   multi-stateful station cannot round-trip end-to-end until the write side is also fixed. Phase 1 fixes the READ
   side only and names the write-side fix as a deferred follow-on (Non-goals).
2. **No per-assignment run unit.** The redesign (per-`(track,station)` outcomes, per-assignment assembly, exact-51,
   fallback-as-assignment-failure) needs an assignment-keyed context to hang everything off. Nothing exists today.

## What Phase 1 delivers (and deliberately does NOT)
- **Delivers:** an assignment-keyed `ModelRunContext`; warm-up state **read per `(station_id, model_id)`** in the
  station-cycle path, loaded inside `_run_single_model` after the eligibility gates;
  `run_all_station_forecasts`/`_run_single_model` consuming per-assignment state. Behaviour-preserving for every
  current configuration except the heterogeneous-stateful **read** bug it fixes.
- **Out of scope (later phases):** per-assignment **input** assembly / dropping the station superset (Phase 3);
  `ForcingTrackKey` + per-track cycle resolution (Phase 3); the runner returning a per-assignment success/failure
  result + fallback-on-missing-track (Phase 2); exact-51 / survival / horizons (Phase 3); group + ensemble
  **behaviour** changes; **write-side** per-assignment state persistence (see Non-goals — Phase 1 fixes the READ
  round-trip only). In Phase 1 `inputs`/`input_metadata` stay the **shared** assembled values; the GROUP path
  is left functionally unchanged (D2).

## Design
- **D1 — introduce `ModelRunContext` (frozen, kw-only, slots), SERVICE-LOCAL.** Defined in the **service** layer
  (`services/run_station_forecast.py`, or `operational_inputs.py`), **not `types/`** (reviewer #3 — `types/` is
  domain contracts and must not reference the service-owned `OperationalInputMetadata`; keeping the context
  service-local avoids the layering inversion for Phase 1). Per assignment, keyed by `(station_id, model_id)`
  (`ModelAssignment` has no id — identity is station+model, `types/station.py:55-58`). **Exact fields — it does
  NOT embed the full `OperationalInputMetadata` (reviewer #7):** embedding it would expose the
  representative-scoped `warm_up_source`/`warm_up_state_age_hours` under the *same names* as the context's own
  per-assignment ones — the exact shared-provenance landmine this plan removes. Instead the context carries the
  specific **shared non-state scalars** it needs, plus its own warm-up:
  ```python
  @dataclass(frozen=True, kw_only=True, slots=True)
  class ModelRunContext:
      station_id: StationId
      model_id: ModelId
      inputs: StationModelInputs                    # shared in Phase 1 (referenced, not copied)
      observation_staleness_hours: float | None     # shared non-state metadata (from input_metadata)
      nwp_age_hours: float | None                   # shared non-state metadata (from input_metadata)
      prior_state: bytes | None                     # per-assignment
      warm_up_source: WarmUpSource                  # per-assignment
      warm_up_state_age_hours: float | None         # per-assignment
  ```
  So the context *is* "shared inputs + the shared non-state provenance scalars + this assignment's warm-up state" —
  with **no** `OperationalInputMetadata.warm_up_*` reachable on the same object (the landmine is gone). Later
  phases make `inputs`/the shared scalars per-assignment too; Phase 1 only splits the three warm-up fields out.
  `OperationalInputMetadata` keeps its own `warm_up_source`/age for the GROUP path (D2). **Each
  context is built INSIDE `_run_single_model`, AFTER the model/coverage/artifact eligibility gates
  (`run_station_forecast.py:114,132-150,152-161`) and immediately before the state is first read (the reject-guard,
  `:175`).** This single, consistent execution order is deliberate (see the failure-semantics section): the state
  read for an assignment only happens once that assignment is eligible to run, so a read failure for a
  missing-model / short-coverage / artifact-less assignment can never abort a station whose earlier (higher-priority)
  assignment already produced a forecast. `run_all_station_forecasts` does **not** pre-build a per-assignment
  context before running models, and there is **no** `(station_id, model_id)`-keyed mapping — its
  `sorted_assignments` loop (`run_station_forecast.py:334`) visits each assignment exactly once and hands
  `_run_single_model` the shared `inputs`/`input_metadata` plus the `model_state_store`; `_run_single_model`
  constructs the context locally.
  *(No runtime uniqueness assertion — the `model_assignments` table's `PrimaryKeyConstraint("station_id",
  "model_id")` (`db/metadata.py:926`) already guarantees a `list[ModelAssignment]` for one station cannot contain a
  duplicate pair. A prior draft's "assert key uniqueness" line was dead defensive code and is dropped; reviewer
  finding accepted.)*
- **D2 — extract `load_warm_up_state(model_state_store, station_id, model_id, now) -> tuple[bytes | None,
  WarmUpSource, float | None]`** out of the state block in `assemble_station_operational_inputs`
  (`operational_inputs.py:489-503`). **Ownership decision (resolves the GROUP-regression blocker):**
  - The **shared assembler keeps loading warm-up state unconditionally, exactly as today.**
    `assemble_station_operational_inputs` calls `load_warm_up_state` for its representative `model_id` and populates
    `OperationalInputMetadata.warm_up_source` / `warm_up_state_age_hours` — no new toggle. This is what the **GROUP
    path** relies on: `assemble_group_operational_inputs` (`run_group_forecast.py:131-147`) calls the shared
    assembler once per station with its single group `model_id`, and reads `input_metadata.warm_up_source` /
    `warm_up_state_age_hours` directly (`run_group_forecast.py:309-310,336-337`). Keeping the load unconditional
    means the GROUP path is **byte-for-byte unchanged**.
  - **No `include_warm_up_state` toggle (reviewer minor accepted).** A prior draft added an
    `include_warm_up_state: bool = False` opt-out on the assembler so the station-cycle path could skip one
    representative-model store read. It is dropped: it saved exactly **one** read out of the N-per-assignment reads
    Phase 1 already accepts, at the cost of a permanent boolean on a shared function (used with the non-default by a
    single caller) plus a documented "placeholder values that must never be read" footgun on the `False` branch —
    precisely the class of shared-provenance landmine this plan exists to remove, relocated behind a flag rather
    than eliminated. Instead the assembler always loads for the representative model (one harmless duplicate read,
    dwarfed by the per-assignment reads), and the **station-cycle path simply does not read** the resulting
    `warm_up_source`/`warm_up_state_age_hours` off `input_metadata` — it reads warm-up only from the per-assignment
    `ModelRunContext` (D3). Same behaviour-preservation invariant, no dead-placeholder branch, no flag API surface.
  - **`OperationalInputMetadata.prior_state` is REMOVED** (`operational_inputs.py:48`). No reader survives Phase 1:
    the GROUP path never reads `prior_state` (only `warm_up_source`/age — verified: the only `prior_state` reads in
    `run_group_forecast.py` are none; it reads solely source/age at `:309-310,336-337`), and the two station-path
    readers (`run_station_forecast.py:175,203`) migrate to `ModelRunContext` (D3). Removing the field — rather than
    leaving it "nullable + unused" — kills the exact shared-bytes landmine that produced this bug (a future
    contributor reasonably assuming `.prior_state` is authoritative). `warm_up_source`/`warm_up_state_age_hours`
    **stay** on `OperationalInputMetadata` as shared provenance because the GROUP path still reads them — this is
    intentional, documented dual-ownership of *provenance* (source/age), while the *state bytes* move to the
    per-assignment context.
  - **`OperationalInputMetadata` gains `evaluated_at: UtcDatetime`** — the assembler's single `now = clock()`
    (`operational_inputs.py:344`), which today already stamps observation staleness (`:377`), NWP age (`:474`) and
    the representative warm-up age (`:496`) at one instant. Exposing it lets the station-cycle per-assignment state
    reads (D3) use the **same** instant, so warm-up age is byte-identical to assembly's and every assignment agrees
    (resolves the ticking-clock finding — see D3). The GROUP path ignores the field.
  - Assembly's `model_id` param is **still used** (unchanged from today): the representative warm-up read
    (`operational_inputs.py:490`) and the `short_lookback` diagnostic (`:404`). It is not left vestigial, so the
    prior "audit model_id under the flag" open item is closed.
- **D3 — station-cycle path reads warm-up state per assignment, inside `_run_single_model`, after the eligibility
  gates.**
  - `run_all_station_forecasts` gains a `model_state_store: ModelStateStore` param **and the representative
    warm-up tuple already resolved by assembly** — `assemble_station_operational_inputs` returns
    `(representative_model_id, prior_state, warm_up_source, warm_up_state_age_hours)` alongside its inputs/metadata
    (it already performs that read, D2). It forwards both (plus shared `inputs`/`input_metadata`) to
    `_run_single_model` for each `assignment` in its sort loop. It does **not** call `clock()` for state age and
    does **not** pre-load any other state before the loop.
  - `_run_single_model` gains a `model_state_store: ModelStateStore` param **and the representative tuple**. It
    keeps its shared `inputs` + `input_metadata` params (Phase 1 does not make inputs per-assignment). **After**
    the model-found (`run_station_forecast.py:114`), NWP-coverage (`:132-150`) and artifact (`:152-161`) gates
    pass, and **before** the reject-guard first reads state (`:175`), it resolves this assignment's warm-up state:
    - **If `assignment.model_id == representative_model_id`, REUSE the assembly-resolved tuple — do NOT re-read
      (reviewer #1/#2).** A second `fetch_latest_state` for the representative could return different bytes/timestamp
      under a concurrent write (breaking the byte-identical invariant) and would add a **new** failure point for the
      primary/single-model path that assembly's read already covers. Reuse guarantees identity and adds no new
      failure mode.
    - **Only for a non-representative assignment**, call
      `load_warm_up_state(model_state_store, station_id, assignment.model_id, input_metadata.evaluated_at)` — the
      genuinely new per-assignment read, **wrapped assignment-local** (see failure-semantics). `evaluated_at` (D2)
      makes its age byte-identical to assembly's.
    Then construct the per-assignment `ModelRunContext` (shared `inputs` + the shared non-state scalars + this
    assignment's three warm-up fields).
  - `_run_single_model` then reads from the context:
    - `context.prior_state` at the fan-out reject-guard (`run_station_forecast.py:175`) and at `predict`
      (`:203`) — the reject-guard now checks the **ensemble assignment's own** state (still `None` for a stateless
      ensemble model, so unchanged);
    - `context.warm_up_source` / `context.warm_up_state_age_hours` for input-quality and forecast provenance
      (`:259-260,285-286`) — replacing the `input_metadata.*` reads there;
    - `context.input_metadata.observation_staleness_hours` / `.nwp_age_hours` (the still-shared non-state metadata);
    - `context.inputs` everywhere `inputs` is used (predict, coverage safety net, etc.).
  - `run_station_forecast` (the PRIMARY-mode wrapper, `run_station_forecast.py:371`) also gains `model_state_store`
    and forwards it to `run_all_station_forecasts`.
- **D4 — behaviour-preservation invariant (READ side).** For the assembly/representative model and for **all
  stateless models** (`prior_state is None`), the per-assignment state read equals today's shared value at the same
  `evaluated_at` instant → identical output. The read change is observable **only** for a heterogeneous-stateful
  station (the bug). The GROUP path is unchanged (D2). **Scope note:** this closes the latent correctness hole on
  the **READ** side only; the write side still persists just the primary model's `new_state`
  (`run_forecast_cycle.py:1939-1946,2013-2024`), so a multi-stateful station does not yet round-trip end-to-end — a
  later phase must fix the write side (Non-goals). Pin the read-side invariant with golden tests.

## State-load failure semantics — assignment-local, not a new station-abort mode
Phase 1 adds per-assignment store reads on top of the assembler's existing single representative-model read. A
reviewer blocker flagged that if those new reads happened *before* the eligibility gates (or propagated to the
per-station catch-all), a state-read failure for a **secondary / fallback / missing-model / short-coverage /
artifact-less** assignment would abort a station whose **primary already succeeded** — a genuinely NEW failure mode
that today cannot occur (today only the representative model's state is read, once, at assembly time). The claim
that this was "just a renamed log event" was **wrong**; finding accepted. Phase 1 therefore preserves fallback
semantics (`docs/touchpoint-maps.md:144`) as follows:
- **The representative-model read stays in the assembler**, unchanged. If it raises, assembly's existing try
  (`run_forecast_cycle.py:1849-1873`) logs `forecast_cycle.input_assembly_failed` and marks the station failed —
  **exactly today's behaviour**. No log-name shift, no new station-abort path for this read.
- **The per-assignment reads happen inside `_run_single_model`, after the model/coverage/artifact gates** (D3), and
  are **caught locally**: `load_warm_up_state` still does not swallow its own exception, but `_run_single_model`
  wraps the call in a `try` and, on error, logs (e.g. `run_station_forecast.warm_up_load_failed`) and **returns a
  reason string** — the same assignment-local failure channel the model-not-found / no-artifact / predict-failed
  gates already use (`run_station_forecast.py:121,161,212`). The reason is recorded in
  `MultiModelForecastResult.failed_models`; a higher-priority assignment that already produced a
  `StationForecastResult` is **kept** as the primary. A station is marked failed by the flow **only** when
  `primary_model_id is None` (`run_forecast_cycle.py:1908-1926,1979-1997`), i.e. when *every* eligible model failed
  — identical to today's all-models-failed semantics.
- **The ensemble reject-guards must be wrapped assignment-local too (reviewer #6 — same class of bug).**
  `reject_prior_state_for_fanout` (`run_station_forecast.py:175`) and `reject_stateful_ensemble_states`
  (`ensemble_fanout.py:47-57`) currently **raise `ModelOutputError` OUTSIDE any `try`** ("Raise OUTSIDE the try so
  it propagates loudly", `run_station_forecast.py:172-175`), and `run_all_station_forecasts`' loop has no
  try/except around `_run_single_model` (`:334-360`) — so the exception escapes the whole function, **discarding
  already-succeeded higher-priority results**, to the per-station catch-all (`run_forecast_cycle.py:2070-2075`).
  Today this can only mis-fire on the *representative* model's (possibly-wrong) shared state; **Phase 1 makes
  `context.prior_state` genuinely per-assignment, so a lower-priority stateful ENSEMBLE assignment now hits a
  *correct* guard — which must not abort a station whose primary already succeeded.** Fix: wrap these guards in the
  **same per-assignment `try/except`** that converts a raise into a reason string in `failed_models` (alongside the
  `load_warm_up_state` wrap). A stateful ensemble assignment therefore fails **locally** and the chain advances.
- **Consequence for a single-model station:** if the sole model's state read raises, that model fails →
  `primary_model_id is None` → station failed (via `all_models_failed`, not `input_assembly_failed`). Still a
  station failure, just through the existing all-models-failed path.
- Phase 1 does **not** introduce assignment-local *fallback-to-COLD_START*-on-read-error (silently substituting cold
  state) — that would be a behaviour change and is deferred to Phase 2's per-assignment run-result. The test asserts
  a raising store → that assignment recorded in `failed_models` (and, for a single-model station, station failure),
  **not** a silent COLD_START forecast.

## Non-goals
- Per-assignment input assembly, track resolution, success/failure run-result, exact-51/survival/horizons,
  group/ensemble **behaviour** changes (all later phases). Any model-behaviour change for stateless or single-model
  stations, or for any GROUP forecast.
- **Write-side per-assignment state persistence is NOT fixed by Phase 1.** The flow still persists only the primary
  model's `new_state` per station-cycle: PRIMARY mode stores `fc_result.new_state`
  (`run_forecast_cycle.py:1939-1946`) and combination mode stores state only for `mid == primary_model_id`
  (`run_forecast_cycle.py:2013-2024`, comment "Persist warm-up state for primary model only"), discarding every
  non-primary model's `new_state` even though `run_all_station_forecasts` runs (and produces state for) every
  eligible assignment (`run_station_forecast.py:334-360`). So after Phase 1 a genuinely heterogeneous-stateful
  station still cannot round-trip: a non-primary stateful model reads its own per-assignment state (fixed here) but
  that state is never written back, so `fetch_latest_state` keeps returning the same stale/absent snapshot. Fixing
  this requires a later phase (Phase 2, which owns the per-assignment run-result and its persistence) to store
  `new_state` for **every** result in `multi_result.results` with a non-`None` `new_state`, and to expose all
  results through the PRIMARY-mode wrapper (`run_station_forecast` currently returns only the primary,
  `run_station_forecast.py:409-414`). Deferred deliberately to keep Phase 1 a minimal, behaviour-preserving READ-side
  slice — for all **current** (stateless) configs both sides are no-ops, so nothing regresses.

## Phases / tasks (red-first)
- **T1 — `ModelRunContext` type + `load_warm_up_state` extraction + `evaluated_at`.**
  - **In scope:** add the frozen `ModelRunContext` type (D1 fields) **service-local** (`run_station_forecast.py`,
    NOT `types/` — reviewer #3); extract `load_warm_up_state` (D2) from `operational_inputs.py:489-503`; have
    `assemble_station_operational_inputs` call it unconditionally (as today) **and RETURN the representative tuple
    `(representative_model_id, prior_state, warm_up_source, warm_up_state_age_hours)`** for the runner to reuse
    (reviewer #1/#2 — no second read for the representative); add `evaluated_at: UtcDatetime` to
    `OperationalInputMetadata`; **remove `OperationalInputMetadata.prior_state`**.
  - **Out of scope:** no `include_warm_up_state` toggle (D2, dropped); no station-cycle wiring (T2); no reader
    migration (T2); no write-side change.
  - **Extraction-parity tests** (not a red-first *bug* demonstration — the store already keys on
    `(station_id, model_id)` (`model_state_store.py:38-42`), so the bug is *routing one assembled value to many
    runners*, proven in T2, not lookup behaviour): `load_warm_up_state` reproduces the old assembly logic for
    cold-start / fresh(<24h → FRESH) / snapshot(≥24h → SNAPSHOT); `assemble_station_operational_inputs` still emits
    the same `warm_up_source`/`warm_up_state_age_hours` as today for its representative `model_id`; and
    `input_metadata.evaluated_at` equals the injected clock value (so downstream age reuse is provable).
  - **Gate:** `uv run pytest tests/unit/services/test_operational_inputs.py tests/unit/types/ -q`.
- **T2 — wire the station-cycle path to per-assignment state (read inside the runner, after the gates) + thread
  `ModelStateStore`.**
  - **In scope:** thread `model_state_store` into the station-cycle runner; move the per-assignment warm-up read
    into `_run_single_model` after the eligibility gates; build + consume `ModelRunContext`; migrate the D3 reads;
    the signature migration below.
  - **Out of scope:** GROUP path, ensemble/combination behaviour, write-side persistence, per-assignment inputs.
  - **Signature migration:**
    1. `run_all_station_forecasts` (`run_station_forecast.py:308`) — add `model_state_store: ModelStateStore`;
       forward it (plus shared `inputs`/`input_metadata`) to `_run_single_model`. Do **not** call `clock()` here for
       state age.
    2. `_run_single_model` (`run_station_forecast.py:95`) — add `model_state_store: ModelStateStore`; keep
       `inputs`+`input_metadata`; after the artifact gate (`:163`) call
       `load_warm_up_state(model_state_store, station_id, assignment.model_id, input_metadata.evaluated_at)` inside a
       `try` (assignment-local failure → reason string, per the failure-semantics section); build `ModelRunContext`;
       migrate the reads listed in D3 (`:175,203,259-260,285-286`).
    3. `run_station_forecast` wrapper (`run_station_forecast.py:371,390`) — add `model_state_store`, forward it.
    4. Flow PRIMARY branch (`run_forecast_cycle.py:1888`, `run_station_forecast(...)`) — pass `model_state_store`.
    5. Flow combination branch (`run_forecast_cycle.py:1959`, `run_all_station_forecasts(...)`) — pass
       `model_state_store`.
    6. Flow assembly call (`run_forecast_cycle.py:1850-1867`) — unchanged wiring (no new arg; the assembler keeps
       loading warm-up state per D2). Only `first_model`/`assembly_assignment.model_id` remain the representative.
    7. **Test call sites — grep, do not trust a frozen line list.** Update **every** call of `run_station_forecast(`
       and `run_all_station_forecasts(` in `tests/unit/services/test_run_station_forecast.py` and
       `tests/unit/services/test_run_station_forecast_fanout.py` to pass a fake `ModelStateStore` seeded with
       per-`(station,model)` states. Find them with a grep for the call name at implementation time (the file is
       actively touched by other in-flight work, so any hard-coded line numbers here would drift and silently
       under-cover). A leftover un-migrated call site fails at collection time, which is the backstop.
    8. E2E integration caller `tests/integration/test_e2e_pipeline.py` (grep for `run_station_forecast(` /
       `run_all_station_forecasts(`) — pass the real `model_state_store`.
    9. Any test constructing `OperationalInputMetadata(...)` directly must add the new `evaluated_at` field (grep
       for `OperationalInputMetadata(`).
  - **Red-first bug-demonstrating test:** a station with **two stateful assignments** persisting *different* states
    now runs each model with its OWN state (fails today because the shared `input_metadata.prior_state` routes one
    value to both runners). Plus:
    - **ensemble reject-guard wrapped assignment-local (reviewer #6 regression):** a station with a **succeeding
      primary** and a lower-priority **stateful ENSEMBLE** assignment (its own persisted non-`None` state) →
      `reject_prior_state_for_fanout` fails that assignment **locally** (recorded in `failed_models`), the primary's
      result is **still returned**, the station is **not** aborted (red-first: today the guard raises out of the
      loop and the per-station catch-all discards the primary);
    - **representative-reuse / concurrent-write (reviewer #1/#2):** a fake `ModelStateStore` that returns a
      *different* snapshot on successive `fetch_latest_state` calls → the **representative/primary** model's forecast
      uses the **assembly** snapshot (read exactly once; `_run_single_model` does not re-read it), while a
      non-representative assignment gets its own fresh read;
    - **assignment-local read failure (blocker regression test):** a station with a **succeeding primary** and a
      lower-priority secondary whose `fetch_latest_state` **raises** → the secondary is recorded in
      `failed_models`, the **primary's `StationForecastResult` is still returned** as `primary_model_id`, and the
      station is **not** failed. A single-model station whose sole model's read raises → `primary_model_id is None`
      → station failure. Neither yields a silent COLD_START forecast.
    - **ticking-clock regression test (major finding):** drive the runner with a **non-fixed** clock (each `clock()`
      call advances) and assert every assignment's `warm_up_state_age_hours` on the emitted forecasts is identical
      (proves state age is stamped from the single `input_metadata.evaluated_at`, not a per-assignment `clock()`).
  - **Gate:** `uv run pytest tests/unit/services/test_run_station_forecast.py
    tests/unit/services/test_run_station_forecast_fanout.py -q`.
- **T3 — behaviour-preservation golden tests (station-cycle + GROUP + E2E).**
  - **In scope:** golden/regression tests (a)-(d) below; no production-code change.
  - **Out of scope:** any behaviour change; write-side persistence.
  (a) a control-only **single-model** station → byte-identical forecast + provenance;
  (b) a multi-model **stateless** station → unchanged;
  (c) warm-up provenance (`warm_up_source`/age) unchanged for the assembly model;
  (d) **GROUP regression** — run `tests/unit/services/test_run_group_forecast.py` against the **REAL, unpatched**
  `assemble_group_operational_inputs → assemble_station_operational_inputs` path (the existing suite monkeypatches
  the assembler out via `_patch_station_assembler`, `test_run_group_forecast.py:152-176`, so it has **zero**
  coverage of the warm-up-loading code this plan touches). Add a test that exercises the real assembler with a
  seeded `ModelStateStore` and asserts group warm-up provenance (`warm_up_source`/`warm_up_state_age_hours` on
  `OperationalForecast`) and input-quality flags are **unaffected**.
  - **Gate:** `uv run pytest tests/unit/services/ tests/unit/flows/test_run_forecast_cycle.py
    tests/integration/test_e2e_pipeline.py -q`.
- **T4 — docs + final full-suite gate.**
  - **In scope:** note Phase 1 (READ-side) complete in `docs/design/forecast-cycle-redesign.md`, explicitly recording
    that the write side remains primary-only and is deferred; update the forecast-cycle touchpoint map
    (`docs/touchpoint-maps.md`) and `docs/standards/logging.md`. **The new `run_station_forecast.warm_up_load_failed`
    event is REQUIRED, not conditional (reviewer #5)** — its implementation, a focused log-assertion test, and the
    `logging.md` entry are all in scope (it fires on the assignment-local state-read / reject-guard failure). **No `input_assembly_failed` → `station_forecast_failed`
    log-name shift** — the representative read stays in the assembler, so that event is unchanged (superseded design;
    the earlier draft's log-shift note is void).
  - **Out of scope:** any code change beyond docs.
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
    {"from": "T1", "to": "T2", "reason": "T2 wires the ModelRunContext / load_warm_up_state / evaluated_at introduced in T1"},
    {"from": "T2", "to": "T3", "reason": "T3 golden/GROUP/E2E tests assert behaviour of the wired station-cycle path from T2"},
    {"from": "T3", "to": "T4", "reason": "T4 docs + full-suite gate finalise once all implementation + tests are green"}
  ]
}
```
Strictly sequential: T1 → T2 → T3 → T4.

## Open items / to confirm
- *(Resolved — reviewer #4) **No currently-shipped production model is stateful.** ForecastInterface models
  explicitly ignore state and return `new_state=None` (`forecast_interface.py:735`); no production model returns
  non-`None` state. So Phase 1's read-side fix is **preemptive + foundational** — it unblocks every later phase and
  closes the READ half of a latent correctness hole (the write half is a named deferred follow-on, Non-goals). The
  heterogeneous-stateful scenarios are exercised with **stateful fakes** in the red-first tests.*
- *(Resolved — no longer open: remove-vs-deprecate `prior_state` is decided in D2 = **remove**; the GROUP-ownership
  question is decided in D2 = **keep the shared assembler loading unconditionally, station-cycle reads per
  assignment**; the `include_warm_up_state` toggle is **dropped** (D2); assembly's `model_id` residual uses are
  **confirmed** (representative warm-up read + `short_lookback` diagnostic, D2); the new per-assignment state-read
  failure mode is handled **assignment-locally** (failure-semantics section).)*
