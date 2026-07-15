---
status: DRAFT
created: 2026-07-15
plan: 115b4
parent: 115b
title: Reader flip + cutover — hybrid default, priority chain, camels-ch retirement, loudness & guards
scope: The RISKY behaviour change. Flips what data models are fed. Gated on 115b3's validation passing.
depends_on: [115b3]
blocks: [115c]
---

# Plan 115b4 — Reader flip + cutover

> **Design source: [Plan 115b](115b-weather-flow6-reachability.md)** — read §5 (parameter-drop fix +
> priority chain + distribution-shift gate + flip), §6 loudness, §7 health-by-effect, §10 converter
> guards, §11 dashboard, and the phase-5 deployment-choreography subsection. Carries **phases 5 and 6**.

## Status

**DRAFT.** Fourth and final chunk (115b1 → 115b2 → 115b3 → **115b4**). Independent Codex review before
READY. **This is the high-risk landing** — it changes what data reaches models, so it is isolated: a
staging problem reverts here without dragging back the schema (115b1) or the backfilled data (115b2).

**⚠️ Gated on 115b3.** Do not flip until the validation gate's result is recorded and any flag
dispositioned.

## Scope

### Phase 5 — the reader

- **5A — hybrid parameter-drop fix (BEFORE the flip).** `hybrid_reanalysis.py:66-72` silently `continue`s
  (drops the row) for any parameter with no configured chain; `StoreBackedReanalysisSource` (today's
  default) passes any parameter through. Flipping as-is is a **silent data-loss regression**. Rule: a
  requested parameter with **no configured chain raises `ConfigurationError`** — **unless exactly one
  source is configured for that parameter**, in which case that source wins. *(Overlap test: two sources
  for the same unconfigured parameter → raise, not a nondeterministic winner.)*
- **5B — the priority chain, no CAMELS tier.** `precipitation: RHIRESD → RPRELIMD`; `temperature: TABSD`;
  `temperature_min: TMIND`; `temperature_max: TMAXD`; `relative_sunshine_duration: SRELD`. Plan 072's
  `… → CAMELS_CH` chains are retired.
- **5C — distribution-shift gate.** The flip changes where a feature's value comes from; a model fitted on
  CAMELS-sourced features that suddenly reads MeteoSwiss-sourced features is fed a different distribution
  (Plan 072 §175). The same path serves training, hindcast AND live forecast past-dynamic inputs. Before
  the flip: enumerate **active** artifacts + their past/future requirements; retrain on the new series, or
  hold the flip for affected stations. *(Repo review suggests today's models are probably unaffected —
  native/fallback declare no past/future dynamic features; the FI NWP model needs only future
  precip/temp — but that is an inference; the live artifact/assignment tables settle it. NEEDS-LIVE-DB.)*
- **5D — flip the reanalysis default to `hybrid`** (`config/deployment.py:111`). Only after 5A.
  `tests/unit/config/test_deployment_reanalysis_source.py:24-39` locks the `single` default and updates deliberately. Verify
  CAMELS-only stations still resolve (the chain falls back correctly) — a test, not an assumption.
- **5E — retire the camels-ch weather binding, ATOMIC with 5D.** `single` (default until 5D) reads
  `cfg.nwp_source` directly (`store_backed_reanalysis.py:35`); retiring the binding while `single` is still
  default leaves a station with **no readable reanalysis source**. **Deployment choreography (executable
  procedure, not a task adjective):** land all of 5A–5E together; on deploy, **flip to hybrid FIRST**,
  confirm the hybrid reader is serving, **then** run the binding-retirement migration; roll back **both**
  together (the migration `downgrade()` restores the binding, config reverts to `single`). State this in
  the migration docstring + deploy runbook. **The CAMELS forcing ROWS are NOT deleted** — they stay as the
  115b3 validation reference and audit trail; only the *weather binding* is retired. (CAMELS remains the
  runoff/discharge + static-attribute + basin-polygon source.)

### Phase 6 — loudness + guards

- **6A — `WEATHER_HISTORY_INGEST` check type.** `ingest_weather_history_flow` has no
  `pipeline_health_store` param (`ingest_weather_history.py:255-262`) and `PipelineCheckType`
  (`types/enums.py:151-164`) has no weather-history value — build both + thread the store. **Note:**
  `pipeline_health.check_type` has **no** DB check constraint today (only `status` is constrained,
  `db/metadata.py:1088-1108`, `0001_v0_schema.py:748-762`), so there is nothing to "extend" — either add a
  NEW full check constraint enumerating all `PipelineCheckType` values, or add none (match the current
  no-constraint state). Do not claim a constraint that isn't there.
- **6B — health measured by EFFECT, never `rows_stored`.** `rows_stored` is `len(records)` after
  `on_conflict_do_nothing` (`ingest_weather_history.py:230`, `historical_forcing_store.py:52`), so a
  pure-duplicate re-fetch looks healthy. UNHEALTHY when `stations_targeted == 0` (config fault) and when a
  run inserts nothing over a full window — asserted via **actual DB rowcount** or a **non-advancing
  `MAX(valid_time)` per source**. Two distinct failures distinguished: "nobody bound" vs "bound but silent."
- **6C — converter guards, ALL THREE (round-1 major).** `point_forecast_to_records`,
  `elevation_band_to_records`, **and** `basin_avg_to_records` all write `WeatherForecastRecord.nwp_source`
  (`converters.py:21,50,79`) — not just the two an earlier draft named. Centralize a single
  **reanalysis-tag reject helper** and call it from all three, so a reanalysis row can never be written into
  the forecast table (Plan 071 §243; the code has no such check). Tests for each converter.
- **6D — dashboard forcing endpoint = HYBRID-RESOLVED** (decided). `api/routes/stations.py:452-490` today
  reads `historical_forcing` and ignores `source`, merging provenance streams. Route it through
  `select_reanalysis_source(mode="hybrid")` so it serves exactly what a forecast used, with the **winning
  `source` tag per point** (so an operator can spot a stuck/preliminary tail). **API wiring:** the route today
  depends only on a raw SQL connection and selects `valid_time,parameter,value` grouped by parameter, ignoring
  `source` (`stations.py:452-499`); rewire it to use `get_stores` → `station_store.fetch_reanalysis_bindings`
  → `select_reanalysis_source(mode="hybrid")`.

## Tests

- **The double-dark regression:** with the MeteoSwiss binding present and `hybrid` default, rows written
  under product tags are readable **end to end** by the default consumer. *Must fail against today's wiring.*
- **Priority, not supersession (§3):** for a `(station, valid_time, parameter)` covered by BOTH precip
  sources, a **direct source-keyed fetch returns BOTH rows**, while the **hybrid reader returns only the
  `RhiresD` winner**. *Two assertions.*
- **Parameter drop (5A):** a parameter with no chain **raises**, does not vanish; **overlap** (two sources,
  unconfigured parameter) also raises.
- **CAMELS-only station survives the flip (5D)** — the chain resolves; past-dynamic features unchanged.
- **Flow 6 health (6B):** `stations_targeted == 0` → UNHEALTHY; bound-but-no-inserts over a full window →
  UNHEALTHY (via DB rowcount / non-advancing `MAX(valid_time)`), NOT via `rows_stored`.
- **Phase-5 ordering (5E):** the retire-camels migration cannot leave a station unreadable — test/deploy-gate
  that hybrid is serving BEFORE the binding is retired.
- **No `camels-ch` weather binding remains** after this plan; CAMELS forcing rows are untouched and still
  readable by a direct source-keyed fetch.
- **Converter guards (6C)** reject a reanalysis tag.

## Dependency graph

```json
{
  "phases": [
    {
      "id": "phase-5",
      "name": "Reader: param-drop fix -> chain -> distribution-shift gate -> flip; camels-ch retirement choreographed with the flip",
      "tasks": ["5A-hybrid-parameter-drop-raise", "5B-chain-rhiresd-then-rprelimd-no-camels-tier", "5C-distribution-shift-gate", "5D-flip-default-to-hybrid", "5E-retire-camels-weather-binding"],
      "parallel": false,
      "note": "STRICT ORDER; 5E choreographed atomic with 5D (flip first, confirm serving, then retire; roll back both together).",
      "depends_on": ["plan-115b3"]
    },
    {
      "id": "phase-6",
      "name": "Loudness + guards",
      "tasks": ["6A-weather-history-ingest-check-type", "6B-health-by-effect", "6C-converter-guards", "6D-dashboard-hybrid-resolved"],
      "parallel": true,
      "depends_on": ["phase-5"]
    }
  ]
}
```

## Exit gates

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest
```

**Deploy gate (staging, do not skip):** after the flip, confirm `ingest-weather-history` reports a
**non-zero** effect (advancing `MAX(valid_time)`), a station serves past-dynamic features via the
`RHIRESD → RPRELIMD`/`TABSD`/… chain, and the `camels-ch` weather binding is gone while its forcing rows
remain. Confirm a forecast cycle completes on the new series. **A green flow is not evidence.**

**Doc sync:** `docs/v0-scope.md §A12` + `docs/architecture-context.md:140,574` (CAMELS is now a validation
reference, not the training-forcing source; record the self-derived provenance
`RhiresD`/`TabsD`/`TminD`/`TmaxD`/`SrelD`, our polygons); `docs/standards/cicd.md` (the flip + retirement
choreography as a deploy step).

## Provenance

Extracted from Plan 115b (phases 5–6), 2026-07-15. The risky landing, deliberately isolated.
