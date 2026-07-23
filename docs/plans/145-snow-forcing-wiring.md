---
status: DRAFT
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
+ read-side hybrid snow tier + an owning recap-reanalysis ingest flow — the blocker) is now **Plan 146**. This
plan has no blocker. Needs a confirming `/plan` before READY. Grounded in
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
   `snow_water_equivalent: MEAN` key. A `swe`/`snow_depth` column falls through to the unknown-parameter MEAN
   fallback (`:82-88` — silent), and **`snowmelt` (a flux) would wrongly MEAN instead of SUM**. SAP3 does not read
   the FI-declared `aggregation` field, so this table is the real control.
3. **No snow-scoped degradation, and one failure mode is flow-fatal.** `fetch_snow_forecast` fetches every snow
   variable serially and raises before returning accumulated rows (`:872-877`) — one missing variable discards
   all. `_map_recap_error` (`:296-318`) has no `subscription_not_found` branch → generic `AdapterError` →
   cycle-fatal abort (`run_forecast_cycle.py:1008-1010`). And `source_data_missing` degrades via the
   **cycle-wide** `nwp_unavailable` flag (`:991-1007`), blinding all NWP for the station — including non-snow
   models. These must be fixed **in the same phase that wires the fetch**.

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
  broadcast across all 51 IFS members by the existing 2H-snow path — identical for every member (aligns with 144).
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
     other variables, return structured per-variable availability, don't raise-and-discard — swe subscribed +
     snow_depth unsubscribed still returns swe. (c) A distinct `snow_unavailable` outcome flag, decoupled from
     `nwp_unavailable` (`:991-1007`), so a snow outage never trips the cycle-wide NWP degrade. (d) **Lock the
     EXISTING per-model gate** — when a required future snow column is absent, `assess_future_coverage` suppresses
     that model and the fallback loop advances to the next non-snow model; add NO per-model return-None to
     `assemble_station_operational_inputs` (called once per station with a superset, `:342-353,442-450`).
- **D4 — snow rides the SAME effective cycle IFS resolved (reuse, don't re-probe).** IFS may resolve an older
  cycle (`_resolve_effective_cycle`, `:673-702`); forecast-store reads require exact `nwp_source`+`cycle_time`
  match (`weather_forecast_store.py:56-61`). The caller (`_fetch_nwp_task`) extracts the already-resolved
  `WeatherForecastResult.cycle_time` from the IFS result and passes **that** into `fetch_snow_forecast`, which
  must NOT call `_resolve_effective_cycle` again (an independent re-probe could resolve a different cycle → mismatch).
- **D5 — snow units stay unresolved (`convert=None` retained); do NOT onboard a snow-fed FI model here.** hs/swe
  (metres?) + rof (flux) magnitudes are unconfirmed; resolution is a **further follow-on plan** (see Non-goals).
  145 delivers the pipeline (shape/provenance/co-retrieval), not canonical magnitudes; **no FI model declaring a
  canonical unit on a snow variable may be onboarded until that plan lands** (numbers would be mislabeled).

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

### Phase 2 — Future-snow forecast wiring WITH degradation (D1/D3/D4) — depends Phase 1
Degradation (D3) and cycle-consistency (D4) ship **atomically** with the wiring (the pattern already exists,
`recap_gateway.py:756-776`; shipping the wiring without it hard-aborts the cycle for every non-snow station).
- **2a — required-snow-variable scoping / opt-in gate (D3.1, station-level v1).** Compute the required future
  snow set from active station `future_dynamic_features`; thread into `fetch_snow_forecast`. Red-first: (i) a
  non-snow station → **zero** `snow.forecast` calls; (ii) a past-only snow model → zero future fetches.
- **2b — resolved-cycle consistency (D4).** Extract the resolved IFS `cycle_time`, pass into `fetch_snow_forecast`,
  no second `_resolve_effective_cycle`. Red-first: IFS falls back to an older cycle → snow persists under the
  SAME cycle → both co-retrieve in one store batch.
- **2c — snow-scoped error containment + `snow_unavailable` + lock the existing per-model gate (D3.2).** Snow error
  wrapper (not global `_map_recap_error`); per-`(hru,variable)` containment; `snow_unavailable` flag; lock
  `assess_future_coverage`. Regression: IFS/reanalysis config-failure semantics **unchanged**; swe-present/
  snow_depth-absent returns swe.
- **2d — wire fetch → store → broadcast (D1).** Integration tests: (i) snow → store → broadcast → member-suffixed
  inputs; (ii) **mixed-assignment** station (snow-fed + non-snow), snow unavailable → the non-snow model still
  produces a **SUCCESSFUL** forecast; (iii) unsubscribed-snow HRU → SUCCESSFUL cycle for its non-snow models.
  **Verify:** `uv run pytest tests/unit/adapters/test_recap_gateway.py tests/unit/services/test_nwp_coverage.py tests/unit/services/test_operational_inputs.py tests/unit/flows/test_run_forecast_cycle.py`.

### Phase 3 — Docs (repo workflow requires it)
- Update `docs/v0-scope.md` (snow forecast forcing now wired), `docs/standards/logging.md` (the new
  `snow_unavailable` outcome + any new event names), and the touchpoint map for the forecast cycle.

### Full-suite gate
- `uv run pytest` + `uv run ruff check` + `uv run pyright` (repo gate).

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
