---
status: DRAFT
created: 2026-07-23
plan: 147
title: Forecast-cycle redesign Phase 1 — ModelRunContext + per-assignment prior_state
scope: The first, behaviour-preserving slice of the forecast-cycle redesign (docs/design/forecast-cycle-redesign.md). Introduce an assignment-keyed `ModelRunContext` and split warm-up `prior_state` loading out of station-input assembly so each model assignment gets state loaded per `(station_id, model_id)` — fixing the latent shared-state bug and establishing the per-assignment run unit the rest of the redesign consumes. No per-assignment input assembly, no track resolution, no ensemble/group changes. Forecast cycle.
depends_on: []
blocks: []
supersedes: []
---

# Plan 147 — Forecast-cycle redesign Phase 1: `ModelRunContext` + per-assignment `prior_state`

## Status
**DRAFT — Phase 1 of the forecast-cycle redesign** (`docs/design/forecast-cycle-redesign.md`, hardened through 3
independent Codex reviews). This is the deliberately small, **behaviour-preserving-except-one-bugfix** first
slice: it introduces the per-assignment run unit and moves `prior_state` loading to be per-assignment. Needs
`/plan` before READY.

## Problem — one warm-up state is shared across all of a station's models
`assemble_station_operational_inputs` loads warm-up state **once**, for a single representative `model_id`
(`operational_inputs.py:488-503`: `model_state_store.fetch_latest_state(station_id, model_id)`), and bakes it
into `OperationalInputMetadata.prior_state` (`:44-48,518-521`). `run_all_station_forecasts` then passes that
**same** `input_metadata` — and thus the **same `prior_state`** — to `_run_single_model` for **every** assignment
(`run_station_forecast.py:310-360`; consumed at `:175` (fan-out reject), `:203` (predict),
`:259-286` (warm-up provenance)). The representative model id is the *assembly* assignment
(`run_forecast_cycle.py:1851`).

Consequences:
1. **Latent correctness bug.** A station with **≥2 stateful assignments** (heterogeneous warm-up state) feeds the
   *wrong* model's `prior_state` to all but one — silently. (Today no live Swiss model is stateful, so it is
   latent; it becomes unavoidable under the redesign's per-assignment execution.)
2. **No per-assignment run unit.** The redesign (per-`(track,station)` outcomes, per-assignment assembly, exact-51,
   fallback-as-assignment-failure) needs an assignment-keyed context to hang everything off. Nothing exists today.

## What Phase 1 delivers (and deliberately does NOT)
- **Delivers:** an assignment-keyed `ModelRunContext`; warm-up `prior_state` loaded **per `(station_id,
  model_id)`**; `run_all_station_forecasts` consuming per-assignment state. Behaviour-preserving for every current
  configuration except the heterogeneous-stateful bug it fixes.
- **Out of scope (later phases):** per-assignment **input** assembly / dropping the station superset (Phase 3);
  `ForcingTrackKey` + per-track cycle resolution (Phase 3); the runner returning a per-assignment success/failure
  result + fallback-on-missing-track (Phase 2); exact-51 / survival / horizons (Phase 3); group + ensemble changes.
  In Phase 1 `inputs`/`input_metadata` stay the **shared** assembled values.

## Design
- **D1 — introduce `ModelRunContext` (frozen, kw-only, slots).** Per assignment, keyed by `(station_id,
  model_id)` (round-3: `ModelAssignment` has no id — identity is station+model, `types/station.py:56`; assert key
  uniqueness across a station's active assignments). Phase-1 fields = the **warm-up-state** slice only:
  `prior_state: bytes | None`, `warm_up_source: WarmUpSource`, `warm_up_state_age_hours: float | None`. It
  references (does not copy) the shared `StationModelInputs` / `OperationalInputMetadata` for now — later phases
  make inputs per-assignment too.
- **D2 — extract `load_warm_up_state(model_state_store, station_id, model_id, now) -> (prior_state,
  warm_up_source, warm_up_state_age_hours)`** out of `assemble_station_operational_inputs`
  (`operational_inputs.py:488-503`). Assembly **stops loading state**; `OperationalInputMetadata` no longer owns
  `prior_state`/warm-up (or keeps them nullable + unused — the `/plan` decides removal vs deprecate, but the
  runner must not read them for state). Note assembly's `model_id` param is now **decoupled from state** — verify
  its remaining uses (aggregation/features) and keep only those.
- **D3 — `run_all_station_forecasts` builds a `ModelRunContext` per assignment** (state via `load_warm_up_state`
  per `(station_id, assignment.model_id)`) and `_run_single_model` consumes **the context's** `prior_state` /
  warm-up fields instead of the shared `input_metadata` ones (`run_station_forecast.py:175,203,259-286`). The
  ensemble fan-out reject-guard now checks the **ensemble assignment's own** state (`reject_prior_state_for_fanout`,
  `:175`) — still `None` for a stateless ensemble model, so unchanged.
- **D4 — behaviour-preservation invariant.** For the assembly-model assignment and for **all stateless models**
  (`prior_state is None`), the per-assignment state equals today's shared value → identical output. The change is
  observable **only** for a heterogeneous-stateful station (the bug). Pin with golden tests.

## Non-goals
- Per-assignment input assembly, track resolution, success/failure run-result, exact-51/survival/horizons,
  group/ensemble changes (all later phases). Any model-behaviour change for stateless or single-model stations.

## Phases / tasks (red-first)
- **T1 — `ModelRunContext` type + `load_warm_up_state` extraction.** Add the frozen type; extract the loader;
  assembly stops loading state. **Red-first test:** two **stateful** assignments on one station with *different*
  persisted states → `load_warm_up_state` returns each its own (fails today because assembly loads one shared
  state). Unit tests for cold-start / fresh(<24h) / snapshot(≥24h) parity with the old assembly logic.
  **Gate:** `uv run pytest tests/unit/services/test_operational_inputs.py tests/unit/types/ -q`.
- **T2 — wire `run_all_station_forecasts` to per-assignment context.** Build a `ModelRunContext` per assignment;
  `_run_single_model` reads context state. Assert key `(station_id, model_id)` uniqueness. **Tests:** a
  two-stateful-assignment station now runs each model with its own state (red-first vs the shared-state path); the
  ensemble reject-guard still fires only on a genuinely-stateful ensemble assignment.
  **Gate:** `uv run pytest tests/unit/services/test_run_station_forecast.py -q`.
- **T3 — behaviour-preservation golden tests.** (a) a control-only **single-model** station → byte-identical
  forecast + provenance; (b) a multi-model **stateless** station → unchanged; (c) warm-up provenance
  (`warm_up_source`/age) unchanged for the assembly model. **Gate:** the full flows + services suites
  `uv run pytest tests/unit/services/ tests/unit/flows/test_run_forecast_cycle.py -q`.
- **T4 — docs.** Note Phase 1 complete in `docs/design/forecast-cycle-redesign.md`; update the forecast-cycle
  touchpoint map + `docs/standards/logging.md` if warm-up event fields move.

## Dependencies
- `docs/design/forecast-cycle-redesign.md` (the parent architecture; D1/D3/D4 decisions). No plan dependencies —
  Phase 1 is self-contained and behaviour-preserving, so it can land first.

## Open items / to confirm
- **Remove vs deprecate `OperationalInputMetadata.prior_state`/warm-up fields** — `/plan` decides; the runner must
  stop reading them for state either way.
- **Assembly `model_id` residual uses** — confirm what (if anything) still needs it after the state split.
- **Any currently-stateful model?** — if none, the fix is purely preemptive + foundational (still worth it: it
  unblocks every later phase and closes a latent correctness hole).
