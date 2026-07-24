---
status: READY
created: 2026-07-23
plan: 145
title: Future-snow (JSNOW) forecast forcing wiring — fetch → store → broadcast, + canonical aggregation
scope: Wire the FUTURE snow-forecast channel end to end. (1) Fix the aggregation fallback so `swe`/`snow_depth` aggregate as states (MEAN) and `snowmelt` as a flux (SUM). (2) Scope + wire `fetch_snow_forecast` into the forecast cycle → `WeatherForecastStore` (under the SAME effective cycle IFS resolved), broadcast across the IFS ensemble by the existing 2H-snow path — WITH snow-scoped subscription/availability degradation folded in atomically (never a cycle-wide abort). Deterministic single stream. Carved from Plan 139 W7; the PAST/antecedent snow channel is split to Plan 146. Unblocks 144. Forcing ingest.
depends_on: [082]
blocks: [144]
supersedes: []
---

# Plan 145 — Future-snow forecast forcing wiring

## Status
**DRAFT — carved out of Plan 139 W7, then SPLIT 2026-07-23** (owner) after a `/plan` reckoning: the FUTURE
channel here is clean/foldable and unblocks 144; the PAST/antecedent channel (new `ForcingSource` + attribution
+ read-side hybrid snow tier + an owning recap-reanalysis ingest flow — the blocker) is now **Plan 146**. This plan's confirming `/plan` (2026-07-24) converged after folding a proportionality simplification (relax the
assembly `return None` guard + let the existing per-model `assess_future_coverage` gate suppress — no superset
pruning / no availability threaded into assembly), a typed `SnowForecastFetchResult`, `snow_unavailable`→DEGRADED
health, and the pre-submission required-snow scoping map. **READY (owner 2026-07-24) → /implement (hold-at-PR).** Grounded in
[[reference_recap_gateway_12300_products]] (JSNOW is a **single deterministic stream** — no ensemble members;
hourly; swe subscribed for 12300, hs/rof newly subscribed with forecast still materializing).

## Problem — SAP3 never fetches future snow (a plumbing gap, not a data gap)
The gateway *does* deliver snow forecasts (live probe: `snow.forecast(swe)` = 241 hourly rows, 2026-07-23). SAP3
just never fetches them:
1. **No future-snow fetch.** The production forecast fetch iterates `_ifs_variables()` only
   (`fetch_forecasts`, `recap_gateway.py:730-731`; adapter call `run_forecast_cycle.py:886`) — swe/hs/rof have a
   `snow_name` but no `ifs_name`, so they're excluded. The dedicated `fetch_snow_forecast` (`recap_gateway.py:830`,
   `_snow_variables()` at `:859`) has **zero production callers**. So the Plan 082 2H-snow broadcast
   (`operational_inputs._broadcast_deterministic_features_to_members`, `:112-150`) is a **permanent no-op** and
   the `future_dynamic` snow channel is never fed.
2. **Aggregation is wrong.** `_V0_AGGREGATION_FALLBACK` (`training_data.py:30-41`) has only the legacy
   `snow_water_equivalent: MEAN` key. A `swe`/`snow_depth` column falls through to the unknown-parameter fallback
   (`:82-88` — it **warns `resample_to_time_step.unknown_parameter` then falls back to MEAN**, not silent), and
   **`snowmelt` (a flux) would wrongly MEAN instead of SUM**. SAP3 does not read the FI-declared `aggregation`
   field, so this table is the real control.
3. **No snow-scoped degradation, and two failure modes are flow-fatal.** `fetch_snow_forecast` fetches every snow
   variable serially and raises before returning accumulated rows (`:872-877`) — one missing variable discards
   all. `_map_recap_error` (`:296-318`) has no `subscription_not_found` branch → generic `AdapterError` →
   cycle-fatal abort (`run_forecast_cycle.py:1008-1010`). And `source_data_missing` degrades via the
   **cycle-wide** `nwp_unavailable` flag (`:991-1007`), blinding all NWP for the station — including non-snow
   models.
4. **The assembly `return None` trap short-circuits the per-model gate.**
   `assemble_station_operational_inputs` reads the store filtered to the **station superset's** required future
   features (`operational_inputs.py:434-441`) and, if that read is empty while any future feature was requested,
   **returns `None`** (`:442-450`) — *before* any per-model coverage runs. The forecast-cycle caller treats a
   `None` result as a station skip (`run_forecast_cycle.py:1875-1878`), so a station whose superset includes a
   snow-fed model **plus** a native/non-snow fallback, with **all** required snow rows absent, is skipped whole —
   the non-snow fallback never runs. This is the exact case D3.2(d) must survive; without an explicit fix the
   per-model gate is unreachable and Phase 2d's mixed-assignment success test is impossible in the
   all-snow-missing case.
   These four must be fixed **in the same phase that wires the fetch**.

## What already exists — 145 fills the wiring, doesn't rebuild
- **Variable mapping** — `RECAP_VARIABLES` (`recap_gateway.py:70-103`): `snow_depth`→`hs`, `snowmelt`→`rof`,
  `swe`→`swe` (via `snow_name`); `convert=None` = "units unresolved" sentinel (kept — see D5/Non-goals).
- **The broadcast path** — `_broadcast_deterministic_features_to_members` (`operational_inputs.py:112-150`,
  Plan 082 2H-snow) already broadcasts member_id=None snow across real IFS members; it has no data to broadcast.
- **The forecast store + assembly** — `WeatherForecastStore` + `assemble_station_operational_inputs`; snow
  mirrors the IFS forecast fetch→store→assemble path (`_fetch_nwp_task` `:838`, adapter call `:886`).
- **A degradation precedent to COPY** — the pf-unavailable tolerance in `fetch_forecasts` (`recap_gateway.py:756-776`).
- **An existing per-model suppression gate** — `assess_future_coverage` (`nwp_coverage.py:65-141`) per model in
  `run_station_forecast` (`:132-150,334`). 145 targets/locks this, not new per-station plumbing.

## Design decisions
- **D1 — JSNOW is deterministic (member_id=None); broadcast, don't fan out.** Snow rows written as member_id=None,
  broadcast across **every IFS member present in the batch** by the existing 2H-snow path
  (`_broadcast_deterministic_features_to_members` broadcasts only across member IDs actually in the batch,
  `operational_inputs.py:112-150`) — identical for each member (aligns with 144). Not a fixed 51: Recap stops
  fetching perturbation members after the first unavailable one (`recap_gateway.py:747`), so control-only or
  partial-member batches occur; 145 promises no member-count, and 2d covers control-only/partial-member broadcast.
- **D2 — fix the aggregation fallback, one semantics per variable.** Add canonical keys to
  `_V0_AGGREGATION_FALLBACK`: `swe`→**MEAN**, `snow_depth`→**MEAN** (states), `snowmelt`→**SUM** (flux). Canonical
  names only (`_accumulate_snow` stamps `parameter=variable.canonical`, `recap_gateway.py:907`); no `rof` key
  (dead). **No `LAST`** — `AggregationMethod` (`enums.py:140-142`) has only SUM/MEAN and the dispatch
  (`training_data.py:89-92`) collapses non-SUM to MEAN; MEAN is correct for a daily state, zero new plumbing.
- **D3 — snow-scoped, required-variable-aware fetch + degradation (folded into the wiring, atomic).**
  1. **Scope the fetch** from the `future_dynamic_features` of active **station** assignments only (v1) — fetch
     only required future snow variables, only for snow-bound stations (today: 12300). `past_dynamic_features` do
     NOT trigger a `snow.forecast` fetch (that is Plan 146's antecedent channel). This is also the opt-in gate
     (no per-HRU "JSNOW subscribed" config flag exists — `WeatherSourceRole` is only FORECAST/REANALYSIS,
     `enums.py:214-217`). **Group-model snow scoping is a follow-up** (adopting the reviewer's escape hatch): group
     assignments resolve only in Phase B2 (`run_forecast_cycle.py:2101-2126`), structurally **after** Phase A's
     fetch, so a group-only snow model cannot be scoped pre-fetch without hoisting group discovery — deferred, and
     the "any JSNOW-fed model" claim is scoped to station-assigned models for v1.
  2. **Degrade cleanly, snow-scoped.** (a) Do NOT change `_map_recap_error` globally (it maps IFS + reanalysis
     too); introduce a distinct typed snow error (`RecapSnowUnavailableError`, or structurally catch
     `subscription_not_found`/`source_data_missing`) at the `fetch_snow_forecast` boundary only. (b) Contain per
     `(hru, canonical variable)` inside `fetch_snow_forecast` (`:858-877`): preserve rows already accumulated for
     other variables, don't raise-and-discard — swe subscribed + snow_depth unsubscribed still returns swe. (c) A
     distinct `snow_unavailable` outcome flag, decoupled from `nwp_unavailable` (`:991-1007`), so a snow outage
     never trips the cycle-wide NWP degrade. (d) **Reach the EXISTING per-model gate via the SIMPLE general fix —
     relax the assembly `return None` trap, do NOT prune the superset (reviewer proportionality finding).** An
     absent required future snow column should make `assess_future_coverage` (`nwp_coverage.py:65-141`, called
     per-model with **`model.data_requirements.future_dynamic_features`** — the model's OWN requirement, never the
     override, `run_station_forecast.py:129-145`) suppress that model so the fallback loop advances. Today the
     assembly `return None` trap (Problem §4, `operational_inputs.py:442-450`) fires *before* that gate whenever
     the superset future read is empty. **Fix (verified sufficient + strictly more general): stop returning `None`
     on an empty future read when `reqs.future_dynamic_features` is non-empty — log and continue with an empty
     `future_dynamic` frame.** `_pivot_nwp_records([], …)` already yields an empty frame (`:195-196`), and
     `assess_future_coverage` on it produces `adequate=False, "required feature 'swe' absent"` → the exact
     suppression wanted, with **no per-`(hru,variable)`-availability threading into assembly and no superset
     pruning at all**. The per-assignment availability from (b) is kept **only** for the `snow_unavailable`
     outcome/logging (c), not for assembly. Bonus: relaxing the guard fixes the **identical IFS-absent** skip
     for free (a non-NWP fallback now runs instead of the whole station being skipped) — a general repair, less
     code than the snow-specific prune. (Coverage runs before the artifact fetch, `run_station_forecast.py:135`
     vs `:152`, so no extra per-model DB cost from not early-returning.)
- **D4 — snow rides the SAME effective cycle IFS resolved (reuse, don't re-probe).** IFS may resolve an older
  cycle (`_resolve_effective_cycle`, `:673-702`); forecast-store reads require exact `nwp_source`+`cycle_time`
  match (`weather_forecast_store.py:49-66`). The caller (`_fetch_nwp_task`) extracts the already-resolved
  `WeatherForecastResult.cycle_time` from the IFS result and passes **that** into `fetch_snow_forecast`, which
  must NOT call `_resolve_effective_cycle` again (an independent re-probe could resolve a different cycle → mismatch).
- **D5 — snow units stay unresolved (`convert=None` retained); do NOT onboard a snow-fed FI model here.** hs/swe
  (metres?) + rof (flux) magnitudes are unconfirmed; resolution is a **further follow-on plan** (see Non-goals).
  145 delivers the pipeline (shape/provenance/co-retrieval), not canonical magnitudes; **no FI model declaring a
  canonical unit on a snow variable may be onboarded until that plan lands** (numbers would be mislabeled).
- **D6 — capability-gated snow fetch; non-Recap adapters are untouched.** `fetch_snow_forecast` lives only on
  `RecapGatewayForecastAdapter` (`recap_gateway.py:830`); the flow injects `adapter: WeatherForecastSource`
  (`run_forecast_cycle.py:845`) whose Protocol exposes only `fetch_forecasts`
  (`protocols/adapters.py:16-33`), and also runs `MeteoSwissNwpAdapter` plus injected test/replay adapters. A
  bare `adapter.fetch_snow_forecast(...)` call therefore fails pyright and can raise at runtime on non-Recap
  adapters. **Fix:** add a narrow `@runtime_checkable` `SnowForecastSource` Protocol (single method
  `fetch_snow_forecast(station_configs, cycle_time) -> dict[StationId, WeatherForecastResult]`) alongside
  `WeatherForecastSource` in `protocols/adapters.py`, and in `_fetch_nwp_task` invoke the snow fetch **only when
  `isinstance(adapter, SnowForecastSource)`** (capability detection, not an `isinstance(RecapGatewayForecastAdapter)`
  import — keeps replay/test doubles that implement the method compatible). Any adapter that does not satisfy the
  capability skips snow entirely and behaves exactly as today (no snow scoping, no snow fetch, no `snow_unavailable`
  outcome). **The fetch returns a typed `SnowForecastFetchResult` (blocker fix)** — a frozen result carrying
  `forecasts: dict[StationId, WeatherForecastResult]` **and** `unavailable: Mapping[hru, frozenset[str]]` (the
  per-`(hru,variable)` gaps from D3.2b). A plain dict cannot represent per-variable/HRU failures; the typed result
  can. It is used for **storage** (the forecasts) + the **`snow_unavailable` outcome/logging** (the unavailable
  map) — it is **not** threaded into assembly (assembly relies on the relaxed guard + `assess_future_coverage`,
  D3.2d). Non-Recap adapters are gated by **capability**, so the regression is that MeteoSwiss/replay/an ordinary
  injected `WeatherForecastSource` **do not satisfy `SnowForecastSource`** and retain current fetch/store behaviour
  (not "count `snow.forecast` calls" — those adapters have no snow client); "zero `snow.forecast` calls" is
  asserted only for a **Recap** adapter with no required future snow variables.

## Non-goals (owned elsewhere)
- **The PAST/antecedent snow channel** (reanalysis → provenance + read-tier + owning ingest flow) — **Plan 146**.
- **Snow unit resolution / retiring `convert=None`** — a further follow-on plan (needs gateway/hydrosolutions
  unit confirmation; owns the Recap unit map + FI unit mapping/onboarding validation `forecast_interface.py:530-575`).
- The ensemble fan-out / two-track orchestration (144). The snow *model* (aquacast/139). Group-model snow scoping.
- Any new gateway endpoint (uses existing `snow.*` client methods).

## Phases (red-first; each task lists In/Out + Verify)
### Phase 1 — Aggregation fallback fix (D2)
- Add `swe:MEAN`, `snow_depth:MEAN`, `snowmelt:SUM` to `_V0_AGGREGATION_FALLBACK` (`training_data.py:30-41`);
  red-first test that `snowmelt` SUMs and `swe`/`snow_depth` MEAN across a sub-daily→daily resample, and the
  `unknown_parameter` warning no longer fires. **Out:** no `LAST`, no dispatch rewrite, no `rof` key.
  **Verify:** `uv run pytest tests/unit/services/test_training_data.py`.

### Phase 2 — Future-snow forecast wiring WITH degradation (D1/D3/D4/D6) — depends Phase 1
Degradation (D3) and cycle-consistency (D4) ship **atomically** with the wiring (the pattern already exists,
`recap_gateway.py:756-776`; shipping the wiring without it hard-aborts the cycle for every non-snow station).
2a→2b→2c→2d are **sequential** (2b threads the cycle 2a scopes; 2c returns the availability 2d prunes on;
2d integrates all three). Each task states scope + an exact verify command.

- **2a — capability gate + required-snow-variable scoping / opt-in gate (D3.1 + D6, station-level v1).**
  - **In:** `SnowForecastSource` `@runtime_checkable` Protocol in `protocols/adapters.py`; capability-gated call
    in `_fetch_nwp_task` (`run_forecast_cycle.py:845`). **Scoping inputs (major finding):** `_fetch_nwp_task`
    today receives only weather-source bindings, **not** the active assignments or the model registry — so the
    required future-snow set cannot be derived inside it. The flow **computes a per-station
    `required_snow: Mapping[StationId, frozenset[str]]` map BEFORE task submission** — from the already-loaded
    active assignments (`run_forecast_cycle.py:1495-1507`) and their resolved models' `future_dynamic_features`
    (snow canonical names only) — and passes that **immutable map** into `_fetch_nwp_task` (`:1630-1641`), which
    threads each station's set into `fetch_snow_forecast`. Inactive assignments and unresolved/missing models
    contribute nothing.
  - **Out:** no group-model scoping (deferred, D3.1); no non-Recap adapter behavior change; no store/broadcast
    wiring (2d).
  - **Red-first:** (i) non-Recap injected `WeatherForecastSource` (and MeteoSwiss/replay) do **not** satisfy
    `SnowForecastSource` → no snow fetch, unchanged outcome; (ii) Recap adapter + non-snow station → zero
    `snow.forecast` calls; (iii) past-only snow model → zero future snow fetches; (iv) **inactive assignment /
    unresolved model → no snow fetch** (the map excludes them).
  - **Verify:** `uv run pytest tests/unit/flows/test_run_forecast_cycle.py tests/unit/adapters/test_recap_gateway.py`.
- **2b — resolved-cycle consistency (D4).**
  - **In:** extract the resolved IFS `WeatherForecastResult.cycle_time` in `_fetch_nwp_task` and pass it into
    `fetch_snow_forecast`; snow fetch must NOT call `_resolve_effective_cycle` (`recap_gateway.py:673-702`) again.
  - **Out:** no change to IFS cycle resolution.
  - **Red-first:** IFS falls back to an older cycle → snow persists under the SAME `cycle_time` → both co-retrieve
    in one store batch (exact `nwp_source`+`cycle_time` match, `weather_forecast_store.py:49-66`).
  - **Verify:** `uv run pytest tests/unit/flows/test_run_forecast_cycle.py tests/unit/adapters/test_recap_gateway.py`.
- **2c — snow-scoped error containment + `snow_unavailable` outcome + `SnowForecastFetchResult` (D3.2 a-c).**
  - **In:** distinct snow error at the `fetch_snow_forecast` boundary only (`RecapSnowUnavailableError` or
    structural catch of `subscription_not_found`/`source_data_missing`), NOT global `_map_recap_error`
    (`:296-318`); per-`(hru,variable)` containment inside `fetch_snow_forecast` (`:858-877`) returning the typed
    `SnowForecastFetchResult(forecasts, unavailable)` (D6); a `snow_unavailable` outcome flag decoupled from
    `nwp_unavailable` (`:991-1007`), **threaded into `_forecast_cycle_health` as DEGRADED** (major finding —
    `_NwpFetchOutcome`/`_forecast_cycle_health`, `run_forecast_cycle.py:174-193,1690-1704`): a snow outage that
    suppresses the preferred snow model while a non-snow fallback still succeeds is **DEGRADED** (station success
    preserved, but surfaced — the plan calls this degradation, so health must reflect it), not silently HEALTHY.
  - **Out:** no assembly-side availability threading (D3.2d uses the relaxed guard, not the availability map); no
    change to IFS/reanalysis error mapping.
  - **Regression / red-first:** IFS + reanalysis config-failure semantics **unchanged**; swe-present +
    snow_depth-absent still returns swe; a snow outage does NOT set `nwp_unavailable` but DOES mark the cycle
    DEGRADED; **two-HRU leakage guard (major):** the same snow variable fails for HRU A and succeeds for HRU B →
    only A's stations lose it, B's stations store+assemble it normally (proves availability is per-`(hru)`, no
    global set).
  - **Verify:** `uv run pytest tests/unit/adapters/test_recap_gateway.py tests/unit/flows/test_run_forecast_cycle.py`.
- **2d — wire fetch → store → broadcast + relax the assembly `return None` guard (D1 + D3.2d).**
  - **In:** store snow rows and broadcast via the existing 2H-snow path; **relax the `if not nwp_records and
    reqs.future_dynamic_features: return None` guard** (`operational_inputs.py:442-450`) to log-and-continue with
    an empty `future_dynamic` frame, so the per-model `assess_future_coverage` gate (`nwp_coverage.py:65-141`)
    runs and suppresses the snow-fed model (its own requirement), advancing to the non-snow fallback. **No
    superset pruning, no availability threaded into assembly** (D3.2d — the simpler general fix).
  - **Out:** no member-count assertion (D1). (This deliberately also repairs the identical IFS-absent skip — a
    general fix, called out in D3.2d.)
  - **Integration / red-first:** (i) snow → store → broadcast → member-suffixed inputs across every member present
    (incl. a **control-only/partial-member** batch); (ii) **mixed-assignment** station (snow-fed + non-snow),
    **all snow rows absent** → assembly does NOT return `None`, the snow model is suppressed, and the non-snow
    fallback produces a **SUCCESSFUL** forecast (this is the Problem §3.4 regression — must fail red against the
    current `return None`); (iii) a **snow-only future model + native fallback with zero stored snow rows** →
    snow-only model degrades to empty future frame, native fallback still SUCCESSFUL; (iv) unsubscribed-snow HRU →
    SUCCESSFUL cycle for its non-snow models.
  - **Verify:** `uv run pytest tests/unit/adapters/test_recap_gateway.py tests/unit/services/test_nwp_coverage.py tests/unit/services/test_operational_inputs.py tests/unit/flows/test_run_forecast_cycle.py`.

### Phase 3 — Docs (repo workflow requires it) — depends Phase 2
- **In:** update `docs/v0-scope.md` (snow forecast forcing now wired), `docs/standards/logging.md` (the new
  `snow_unavailable` outcome + any new event names, e.g. the snow fetch/degradation events), and the
  forecast-cycle touchpoint map (`docs/touchpoint-maps.md`) for the new snow fetch/scoping/gate touchpoints.
- **Out:** no code change; no snow-unit doc (deferred, D5); no Plan 146 antecedent-channel docs.
- **Verify:** `uv run python -c "import pathlib; [pathlib.Path(p).read_text() for p in ['docs/v0-scope.md','docs/standards/logging.md','docs/touchpoint-maps.md']]"`
  (files parse) + manual read-back that each mentions `snow_unavailable`.

### Full-suite gate
- **Verify:** `uv run pytest` + `uv run ruff check` + `uv run pyright` (repo gate).

## Dependencies
- **082** (2H-snow broadcast path + polygon bindings). Client pin ≥ 9340e40 (#127). **Blocks 144.** Sibling
  **146** (past-snow channel) depends on 145's aggregation (Phase 1) for the read/training path.

## Open items / to confirm
- **hs/rof forecast availability** — subscription added for 12300; forecast still materializing (probed
  `source_data_missing` 2026-07-23). Re-verify before the 2d integration test; D3 degradation means a
  still-materializing forecast no longer blocks the cycle. Keep the 2d acceptance test **hermetic** (fake client);
  a live probe is an optional separate check.
- **Snow unit magnitudes** — deferred to the follow-on unit-resolution plan (D5), not a phase here.
- **Group-model snow scoping** — deferred follow-up (needs group discovery hoisted before Phase A).

## Dependency graph
Phase 1 → Phase 2 → Phase 3. Phase 1 is standalone (single task). Phase 2's tasks are **sequential**
(2a→2b→2c→2d): 2b threads the resolved cycle into the fetch 2a scopes, 2c produces the per-variable availability
2d prunes on, and 2d integrates fetch+store+broadcast+gate. Phase 3 (docs) depends on the whole of Phase 2.

```json
{
  "phases": [
    { "id": "phase-1", "tasks": ["1"], "parallel": false },
    { "id": "phase-2", "tasks": ["2a", "2b", "2c", "2d"], "parallel": false, "depends_on": ["phase-1"] },
    { "id": "phase-3", "tasks": ["3"], "parallel": false, "depends_on": ["phase-2"] }
  ]
}
```
